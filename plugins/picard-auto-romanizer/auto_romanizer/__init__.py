from picard.config import config
from picard import log
# -*- coding: utf-8 -*-

from picard.plugin3.api import PluginApi

from picard.plugin3.api import (
    File,
    Metadata,
    OptionsPage,
)

import os
import re
import json
import subprocess



LOCAL_SCRIPT = os.path.join(os.path.dirname(__file__), "romanizer.py")
SPBOT_SCRIPT = r"E:\Descargas\SPbot\scripts\romanizer.py"
SCRIPT_PATH = LOCAL_SCRIPT if os.path.exists(LOCAL_SCRIPT) else SPBOT_SCRIPT
PYTHON_PATH = r"python"

TITLE_MODE_OPTION = "auto_romanizer_title_mode"
DEFAULT_MODE = "auto"

LATIN_META_WORDS = {
    'feat', 'ft', 'cv', 'tv', 'ver', 'version', 'vs', 'ep', 'op', 'ed',
    'remix', 'mix', 'instrumental', 'off', 'vocal', 'acoustic'
}
_JP_RE = re.compile(r'[\u3040-\u309f\u30a0-\u30ff\u4e00-\u9faf\uff00-\uffef]')


def contains_japanese(text):
    return bool(text and _JP_RE.search(text))


def already_has_latin_translation(text):
    if not text or not contains_japanese(text):
        return False
    # Split by standard separators (- / — –)
    parts = re.split(r'\s*[\-\–\—\/]\s*', text)
    if len(parts) < 2:
        return False
    has_jp = False
    has_latin = False
    for p in parts:
        p = p.strip()
        if contains_japanese(p):
            has_jp = True
        else:
            # Check for non-metadata Latin/Romaji words
            words = [w.lower().rstrip('.') for w in re.findall(r'[a-zA-Z]{2,}', p)]
            if words and any(w not in LATIN_META_WORDS for w in words):
                has_latin = True
    return has_jp and has_latin




def _extract_local_title_from_file(f):
    if not f:
        return None
    for attr in ('orig_metadata', 'metadata'):
        meta = getattr(f, attr, None)
        if not meta:
            continue
        for key in ('title', 'originaltitle', '_original_title'):
            t = meta.get(key, '')
            if isinstance(t, list) and t:
                t = t[0]
            if t:
                clean_t = _strip_track_num_prefix(str(t))
                if already_has_latin_translation(clean_t) or contains_japanese(clean_t):
                    return clean_t
    filename = getattr(f, 'filename', '')
    if filename:
        basename = os.path.splitext(os.path.basename(filename))[0]
        clean_name = _strip_track_num_prefix(basename)
        if already_has_latin_translation(clean_name) or contains_japanese(clean_name):
            return clean_name
    return None


def _get_latin_part(dual_title):
    if not dual_title:
        return ""
    parts = re.split(r'\s*[\-\–\—\/]\s*', dual_title)
    for p in parts:
        p = p.strip()
        if not contains_japanese(p):
            return p
    return ""


def _strip_track_num_prefix(text):
    """Strip leading track number prefix like '01 - ', '02 - ', '01. ', '01_' if present."""
    if not text:
        return text
    cleaned = re.sub(r'^\d{1,3}[\s\.\-_]+\s*', '', text).strip()
    return cleaned if cleaned else text


def _extract_base_jp(text):
    """Extract all Japanese characters from text, removing spaces and symbols to form a pure matching key.
    'プラネタリウム - Planetarium' → 'プラネタリウム'
    '01 - Ikimonogakari - 帰りたくなったよ -acoustic version- - Kaeritakunattayo' → '帰りたくなったよ'
    """
    if not text:
        return None
    # Extract all Japanese character sequences (\u3040-\u30ff, \u4e00-\u9faf, etc.)
    jp_chars = "".join(_JP_RE.findall(text))
    return jp_chars if jp_chars else None

def _find_dual_title_in_tagger(tagger, jp_title):
    """Search all files loaded in Picard for one whose title is a dual-language
    version of the given Japanese title.

    tagger.files is a dict {filename: File} that is always populated because
    files are loaded into Picard BEFORE the MusicBrainz lookup runs.
    """
    key = _extract_base_jp(jp_title)
    log.debug("Auto Romanizer: searching tagger.files for jp_title=%r (key=%r)", jp_title, key)

    all_files = getattr(tagger, 'files', {}) or {}
    log.debug("Auto Romanizer: total files in tagger=%d", len(all_files))
    for filename, file_ in all_files.items():
        # 1. Check embedded metadata title (orig_metadata or metadata)
        for attr in ('orig_metadata', 'metadata'):
            meta = getattr(file_, attr, None)
            if not meta:
                continue
            title = meta.get('title', '')
            if isinstance(title, list) and title:
                title = title[0]
            if title and already_has_latin_translation(title):
                file_key = _extract_base_jp(title)
                log.debug("Auto Romanizer: candidate attr=%s title=%r file_key=%r vs key=%r", attr, title, file_key, key)
                if file_key and key and (file_key == key or file_key in key or key in file_key):
                    clean_t = _strip_track_num_prefix(title)
                    log.debug("Auto Romanizer: MATCHED via metadata: %r (clean=%r)", title, clean_t)
                    return clean_t
            break  # orig_metadata takes priority

        # 2. Fall back to filename
        basename = os.path.splitext(os.path.basename(filename))[0]
        if already_has_latin_translation(basename):
            file_key = _extract_base_jp(basename)
            log.debug("Auto Romanizer: candidate filename=%r file_key=%r vs key=%r", basename, file_key, key)
            if file_key and key and (file_key == key or file_key in key or key in file_key):
                clean_dual = _strip_track_num_prefix(basename)
                log.debug("Auto Romanizer: MATCHED via filename: %r (clean=%r)", basename, clean_dual)
                return clean_dual if already_has_latin_translation(clean_dual) else basename
    log.debug("Auto Romanizer: NO MATCH found for key=%r", key)
    return None


# ── File loading cache ────────────────────────────────────────────────────────
# Maps extracted Japanese character key -> original file dual-language title
_ORIGINAL_DUAL_CACHE = {}

# Maps lowercased Latin title & track_number -> original file Latin title (casing preserved)
_ORIGINAL_LATIN_CACHE = {}


def _make_cache_key(text, track_num=None):
    """Generate a unique key incorporating Japanese characters and optional track number."""
    jp_key = _extract_base_jp(text)
    if not jp_key:
        return None
    if track_num:
        return "{}:{}".format(track_num, jp_key)
    return jp_key


def _on_file_loaded(*args):
    file_ = args[-1]
    """Fired whenever a file is loaded in Picard.
    Reads original embedded metadata or filename and caches its dual-language title.
    """
    track_num = None
    for attr in ('orig_metadata', 'metadata'):
        meta = getattr(file_, attr, None)
        if meta and 'tracknumber' in meta:
            tn = meta.get('tracknumber', '')
            if isinstance(tn, list) and tn:
                tn = tn[0]
            track_num = str(tn).split('/')[0].strip()
            break

    for attr in ('orig_metadata', 'metadata'):
        meta = getattr(file_, attr, None)
        if not meta:
            continue
        title = meta.get('title', '')
        if isinstance(title, list) and title:
            title = title[0]
        if title and already_has_latin_translation(title):
            clean_title = _strip_track_num_prefix(title)
            key = _make_cache_key(clean_title, track_num)
            raw_key = _extract_base_jp(clean_title)
            if key:
                _ORIGINAL_DUAL_CACHE[key] = clean_title
            if raw_key and raw_key not in _ORIGINAL_DUAL_CACHE:
                _ORIGINAL_DUAL_CACHE[raw_key] = clean_title
            log.debug("Auto Romanizer cache: added title %r (key=%r, raw_key=%r)", clean_title, key, raw_key)
            break

        if title and not contains_japanese(title):
            clean_l = _strip_track_num_prefix(title)
            l_key = clean_l.strip().lower()
            _ORIGINAL_LATIN_CACHE[l_key] = clean_l
            if track_num:
                _ORIGINAL_LATIN_CACHE["{}:{}".format(track_num, l_key)] = clean_l
            log.debug("Auto Romanizer cache: added Latin title %r", clean_l)

    # Also check filename
    filename = getattr(file_, 'filename', '')
    if filename:
        basename = os.path.splitext(os.path.basename(filename))[0]
        # Attempt to parse leading track number from filename if not found in tags
        if not track_num:
            tn_match = re.match(r'^(\d+)', basename)
            if tn_match:
                track_num = tn_match.group(1).lstrip('0') or '0'
        if already_has_latin_translation(basename):
            clean_dual = _strip_track_num_prefix(basename)
            key = _make_cache_key(clean_dual, track_num)
            raw_key = _extract_base_jp(clean_dual)
            val = clean_dual if already_has_latin_translation(clean_dual) else basename
            if key:
                _ORIGINAL_DUAL_CACHE[key] = val
            if raw_key and raw_key not in _ORIGINAL_DUAL_CACHE:
                _ORIGINAL_DUAL_CACHE[raw_key] = val
            log.debug("Auto Romanizer cache: added filename %r (key=%r, raw_key=%r)", val, key, raw_key)
        elif not contains_japanese(basename):
            clean_name = _strip_track_num_prefix(basename)
            l_key = clean_name.lower()
            if l_key not in _ORIGINAL_LATIN_CACHE:
                _ORIGINAL_LATIN_CACHE[l_key] = clean_name
            if track_num:
                _ORIGINAL_LATIN_CACHE["{}:{}".format(track_num, l_key)] = clean_name




PYTHON_PATH = r"C:\Users\charl\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.12_qbz5n2kfra8p0\python.exe"

def romanize_dict(tags_dict):
    try:
        from . import romanizer
        res = {}
        for k, v in tags_dict.items():
            if isinstance(v, str) and contains_japanese(v):
                rom = romanizer.to_romaji(v)
                res[k] = rom
            else:
                res[k] = v
        return res
    except Exception as e:
        log.debug("Auto Romanizer in-process conversion fallback: %s", e)

    if not os.path.exists(SCRIPT_PATH):
        return tags_dict
    try:
        py_exec = PYTHON_PATH if os.path.exists(PYTHON_PATH) else "python"
        creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)
        proc = subprocess.Popen(
            [py_exec, SCRIPT_PATH, "--json-dict", json.dumps(tags_dict)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=creationflags
        )
        out, err = proc.communicate(timeout=5)
        if not out:
            if err:
                log.error("Auto Romanizer: %s", err.decode('utf-8', errors='ignore'))
            return tags_dict
        res = json.loads(out.decode('utf-8', errors='ignore'))
        if isinstance(res, dict) and "error" not in res:
            return res
        if isinstance(res, dict) and "error" in res:
            log.error("Auto Romanizer script error: %s", res["error"])
    except Exception as e:
        log.error("Auto Romanizer exception: %s", e)
    return tags_dict


def _clean_internal_tags(metadata):
    for k in ('_original_title', '_original_artist',
              '_original_album', '_original_albumartist'):
        if k in metadata:
            del metadata[k]


# ── Metadata processors ───────────────────────────────────────────────────────

def _apply_romanization(api, track, metadata, file=None):
    cfg = api.plugin_config if (api and hasattr(api, "plugin_config")) else (api.global_config.setting if (api and hasattr(api, "global_config")) else config.setting)
    mode = cfg.get(TITLE_MODE_OPTION, DEFAULT_MODE) if hasattr(cfg, "get") else (cfg[TITLE_MODE_OPTION] if TITLE_MODE_OPTION in cfg else DEFAULT_MODE)

    if metadata.get('title') and 'originaltitle' not in metadata:
        metadata['originaltitle'] = metadata['title']
    if metadata.get('artist') and 'originalartist' not in metadata:
        metadata['originalartist'] = metadata['artist']
    if metadata.get('album') and 'originalalbum' not in metadata:
        metadata['originalalbum'] = metadata['album']
    _clean_internal_tags(metadata)

    orig_title = metadata.get('title', '')
    if isinstance(orig_title, list) and orig_title:
        orig_title = orig_title[0]

    # Check local file for Japanese or Dual title
    local_title = None
    if file:
        local_title = _extract_local_title_from_file(file)

    if not local_title and track:
        linked_files = getattr(track, 'files', []) or getattr(track, 'linked_files', [])
        for f in linked_files:
            local_title = _extract_local_title_from_file(f)
            if local_title:
                break

    target_title = local_title if local_title else orig_title

    if target_title:
        if already_has_latin_translation(target_title):
            if mode in ("auto", "dual"):
                metadata['title'] = target_title
                metadata['originaltitle'] = target_title
            elif mode == "japanese":
                parts = re.split(r'\s*[\-\–\—\/]\s*', target_title)
                for p in parts:
                    if contains_japanese(p):
                        metadata['title'] = p.strip()
                        break
            elif mode == "romaji":
                lat = _get_latin_part(target_title)
                if lat:
                    metadata['title'] = lat

        elif contains_japanese(target_title):
            clean_jp = _strip_track_num_prefix(target_title)
            if mode in ("auto", "dual"):
                result = romanize_dict({'title': clean_jp})
                romaji = result.get('title', clean_jp)
                if romaji and romaji != clean_jp:
                    metadata['title'] = f"{clean_jp} - {romaji}"
                    metadata['originaltitle'] = f"{clean_jp} - {romaji}"
                else:
                    metadata['title'] = clean_jp
            elif mode == "japanese":
                metadata['title'] = clean_jp
            elif mode == "romaji":
                result = romanize_dict({'title': clean_jp})
                romaji = result.get('title', clean_jp)
                metadata['title'] = romaji

    # Artist / album – convert Japanese to Romaji
    to_convert = {}
    for k in ('artist', 'album', 'albumartist'):
        v = metadata.get(k)
        if isinstance(v, list) and v:
            v = v[0]
        if v and contains_japanese(v):
            to_convert[k] = v
    if to_convert:
        converted = romanize_dict(to_convert)
        for k, v in converted.items():
            metadata[k] = v


def process_track(api, track, metadata, track_node, release_node):
    _apply_romanization(api, track, metadata)


def on_file_added_to_track(*args):
    track = None
    file = None
    for arg in args:
        if hasattr(arg, "metadata") and hasattr(arg, "filename"):
            file = arg
        elif hasattr(arg, "album") and hasattr(arg, "files"):
            track = arg

    if file:
        _apply_romanization(None, track, file.metadata, file=file)
    if track and hasattr(track, "metadata"):
        _apply_romanization(None, track, track.metadata, file=file)
def process_album(tagger, metadata, release):
    if metadata.get('title') and 'originalalbum' not in metadata:
        metadata['originalalbum'] = metadata['title']
    if metadata.get('albumartist') and 'originalalbumartist' not in metadata:
        metadata['originalalbumartist'] = metadata['albumartist']
    _clean_internal_tags(metadata)

    to_convert = {}
    for k in ('title', 'album', 'albumartist'):
        v = metadata.get(k)
        if v and contains_japanese(v):
            to_convert[k] = v
    if to_convert:
        converted = romanize_dict(to_convert)
        for k, v in converted.items():
            metadata[k] = v


# ── Options page ──────────────────────────────────────────────────────────────

class AutoRomanizerOptionsPage(OptionsPage):
    NAME = "auto_romanizer"
    TITLE = "Auto Romanizer"
    PARENT = "plugins"


    def __init__(self, parent=None, api=None):
        super().__init__(parent)
        self.api = api
        super().__init__(parent)
        from PyQt6 import QtWidgets  # lazy – avoid module-level crash

        self.combo_mode = QtWidgets.QComboBox(self)
        self.combo_mode.addItem(
            "Automático: conservar tag original si ya tiene traducción "
            "(ej: プラネタリウム - Planetarium)", "auto")
        self.combo_mode.addItem(
            "Dual: Japonés + Romaji generado "
            "(ej: プラネタリウム - Puranetariumu)", "dual")
        self.combo_mode.addItem(
            "Solo Romaji: convertir a Romaji "
            "(ej: Puranetariumu)", "romaji")
        self.combo_mode.addItem(
            "Original: conservar Japonés sin cambiar "
            "(ej: プラネタリウム)", "japanese")

        form = QtWidgets.QFormLayout()
        form.addRow(QtWidgets.QLabel("Modo de conversión de títulos:"),
                    self.combo_mode)

        group = QtWidgets.QGroupBox("Formato de Títulos en Japonés", self)
        group.setLayout(form)

        vbox = QtWidgets.QVBoxLayout(self)
        vbox.addWidget(group)
        vbox.addStretch()

    def _get_cfg(self):
        if hasattr(self, 'api') and self.api and hasattr(self.api, 'plugin_config'):
            return self.api.plugin_config
        if hasattr(self, 'api') and self.api and hasattr(self.api, 'global_config'):
            return self.api.global_config.setting
        return config.setting

    def load(self):
        cfg = self._get_cfg()
        mode = cfg.get(TITLE_MODE_OPTION, DEFAULT_MODE) if hasattr(cfg, "get") else (cfg[TITLE_MODE_OPTION] if TITLE_MODE_OPTION in cfg else DEFAULT_MODE)
        idx = self.combo_mode.findData(mode)
        if idx >= 0:
            self.combo_mode.setCurrentIndex(idx)

    def save(self):
        cfg = self._get_cfg()
        cfg[TITLE_MODE_OPTION] = self.combo_mode.currentData()
def enable(api: PluginApi):
    """Called when plugin is enabled."""
    api.register_track_metadata_processor(process_track)
    api.register_album_metadata_processor(process_album)
    api.register_file_post_load_processor(_on_file_loaded)
    api.register_file_post_addition_to_track_processor(on_file_added_to_track)
    api.register_options_page(AutoRomanizerOptionsPage)