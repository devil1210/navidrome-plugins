# -*- coding: utf-8 -*-
PLUGIN_NAME = "Auto Romanizer"
PLUGIN_AUTHOR = "SPbot"
PLUGIN_DESCRIPTION = (
    "Romaniza automáticamente títulos, artistas y álbumes de japonés a Romaji "
    "preservando metadatos originales. En modo Automático conserva títulos que "
    "ya tienen traducción al inglés/Romaji desde el archivo original."
)
PLUGIN_VERSION = "3.22"
PLUGIN_API_VERSIONS = ["2.0", "2.1", "2.2", "2.3", "2.4", "2.5", "2.6",
                       "2.7", "2.8", "2.9", "2.10", "2.11", "2.12", "2.13"]
PLUGIN_LICENSE = "GPL-2.0"

import os
import re
import json
import subprocess

from picard import config, log
from picard.metadata import register_track_metadata_processor, register_album_metadata_processor
from picard.ui.options import OptionsPage, register_options_page

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


def _on_file_loaded(file_):
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


try:
    from picard.file import register_file_post_load_processor
    register_file_post_load_processor(_on_file_loaded)
    log.debug("Auto Romanizer: file_post_load_processor registered successfully")
except (ImportError, AttributeError):
    try:
        from picard.plugin import register_file_post_load_processor
        register_file_post_load_processor(_on_file_loaded)
        log.debug("Auto Romanizer: file_post_load_processor registered via picard.plugin")
    except (ImportError, AttributeError):
        log.debug("Auto Romanizer: register_file_post_load_processor unavailable")

def romanize_dict(tags_dict):
    if not os.path.exists(SCRIPT_PATH):
        return tags_dict
    try:
        creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)
        proc = subprocess.Popen(
            [PYTHON_PATH, SCRIPT_PATH, "--json-dict", json.dumps(tags_dict)],
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

def process_track(tagger, metadata, track, release):
    mode = config.setting[TITLE_MODE_OPTION] \
        if TITLE_MODE_OPTION in config.setting else DEFAULT_MODE

    # Preserve originals before we touch anything
    if metadata.get('title') and 'originaltitle' not in metadata:
        metadata['originaltitle'] = metadata['title']
    if metadata.get('artist') and 'originalartist' not in metadata:
        metadata['originalartist'] = metadata['artist']
    if metadata.get('album') and 'originalalbum' not in metadata:
        metadata['originalalbum'] = metadata['album']
    _clean_internal_tags(metadata)

    orig_title = metadata.get('title', '')
    if orig_title and contains_japanese(orig_title):
        if mode == "auto":
            dual = None
            raw_key = _extract_base_jp(orig_title)
            track_num = metadata.get('tracknumber', '')
            if isinstance(track_num, list) and track_num:
                track_num = track_num[0]
            track_num = str(track_num).split('/')[0].strip() if track_num else None
            key = _make_cache_key(orig_title, track_num)

            # 0. Check linked files directly first (highest priority)
            linked_files = getattr(track, 'linked_files', None) or getattr(track, 'files', [])
            for f in linked_files:
                for attr in ('orig_metadata', 'metadata'):
                    meta = getattr(f, attr, None)
                    if not meta:
                        continue
                    t = meta.get('title', '')
                    if isinstance(t, list) and t:
                        t = t[0]
                    if t and already_has_latin_translation(t):
                        fk = _extract_base_jp(t)
                        if fk and raw_key and (fk == raw_key or fk in raw_key or raw_key in fk):
                            dual = _strip_track_num_prefix(t)
                            break
                if dual:
                    break

            # 1. Check track-number specific cache (e.g. "14:帰りたくなったよ")
            if not dual and key and key in _ORIGINAL_DUAL_CACHE:
                dual = _ORIGINAL_DUAL_CACHE[key]
                log.debug("Auto Romanizer: MATCHED via _ORIGINAL_DUAL_CACHE track key: %r for key=%r", dual, key)

            # 2. Check raw Japanese cache
            if not dual and raw_key and raw_key in _ORIGINAL_DUAL_CACHE:
                dual = _ORIGINAL_DUAL_CACHE[raw_key]
                log.debug("Auto Romanizer: MATCHED via _ORIGINAL_DUAL_CACHE raw key: %r for raw_key=%r", dual, raw_key)

            # 3. Search unlinked files in tagger.files
            if not dual:
                dual = _find_dual_title_in_tagger(tagger, orig_title)

            if dual:
                clean_dual = _strip_track_num_prefix(dual)
                metadata['title'] = clean_dual
                metadata['originaltitle'] = clean_dual
            else:
                # No dual original found – generate dual (Japonés - Romaji)
                result = romanize_dict({'title': orig_title})
                romaji = result.get('title', orig_title)
                if romaji and romaji != orig_title:
                    metadata['title'] = "{} - {}".format(orig_title, romaji)

        elif mode == "japanese":
            pass  # Leave Japanese title untouched

        elif mode == "dual":
            result = romanize_dict({'title': orig_title})
            romaji = result.get('title', orig_title)
            if romaji and romaji != orig_title:
                metadata['title'] = "{} - {}".format(orig_title, romaji)

        else:  # romaji
            result = romanize_dict({'title': orig_title})
            if 'title' in result:
                metadata['title'] = result['title']

    elif orig_title and not contains_japanese(orig_title):
        # Non-Japanese track (e.g., "Message" or "Happy Smile Again")
        if mode == "auto":
            track_num = metadata.get('tracknumber', '')
            if isinstance(track_num, list) and track_num:
                track_num = track_num[0]
            track_num = str(track_num).split('/')[0].strip() if track_num else None
            l_key = orig_title.strip().lower()

            latin_title = None

            # 0. Check linked files directly
            linked_files = getattr(track, 'linked_files', None) or getattr(track, 'files', [])
            for f in linked_files:
                for attr in ('orig_metadata', 'metadata'):
                    meta = getattr(f, attr, None)
                    if not meta:
                        continue
                    t = meta.get('title', '')
                    if isinstance(t, list) and t:
                        t = t[0]
                    if t and t.lower().strip() == l_key:
                        latin_title = t
                        break
                if latin_title:
                    break

            # 1. Check _ORIGINAL_LATIN_CACHE by track number key
            if not latin_title and track_num:
                tn_key = "{}:{}".format(track_num, l_key)
                if tn_key in _ORIGINAL_LATIN_CACHE:
                    latin_title = _ORIGINAL_LATIN_CACHE[tn_key]
                    log.debug("Auto Romanizer: MATCHED Latin title via track key: %r", latin_title)

            # 2. Check _ORIGINAL_LATIN_CACHE by lowercased title
            if not latin_title and l_key in _ORIGINAL_LATIN_CACHE:
                latin_title = _ORIGINAL_LATIN_CACHE[l_key]
                log.debug("Auto Romanizer: MATCHED Latin title via l_key: %r", latin_title)

            # 3. Check tagger.files
            if not latin_title:
                all_files = getattr(tagger, 'files', {}) or {}
                for filename, file_ in all_files.items():
                    for attr in ('orig_metadata', 'metadata'):
                        meta = getattr(file_, attr, None)
                        if meta:
                            t = meta.get('title', '')
                            if isinstance(t, list) and t:
                                t = t[0]
                            if t and t.lower().strip() == l_key:
                                latin_title = t
                                break
                    if not latin_title:
                        basename = os.path.splitext(os.path.basename(filename))[0]
                        clean_name = re.sub(r'^\d+[\s\.\-_]+', '', basename).strip()
                        clean_name = re.sub(r'^[^\-\–\—\/]+[\-\–\—\/]\s*', '', clean_name).strip()
                        if clean_name.lower() == l_key:
                            latin_title = clean_name
                            break
                    if latin_title:
                        break

            if latin_title:
                metadata['title'] = _strip_track_num_prefix(latin_title)

    # Artist / album – always convert to Romaji regardless of mode
    to_convert = {}
    for k in ('artist', 'album', 'albumartist'):
        v = metadata.get(k)
        if v and contains_japanese(v):
            to_convert[k] = v
    if to_convert:
        converted = romanize_dict(to_convert)
        for k, v in converted.items():
            metadata[k] = v


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

    options = [
        config.TextOption("setting", TITLE_MODE_OPTION, DEFAULT_MODE),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        from PyQt5 import QtWidgets  # lazy – avoid module-level crash

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

    def load(self):
        mode = config.setting[TITLE_MODE_OPTION] \
            if TITLE_MODE_OPTION in config.setting else DEFAULT_MODE
        idx = self.combo_mode.findData(mode)
        if idx >= 0:
            self.combo_mode.setCurrentIndex(idx)

    def save(self):
        config.setting[TITLE_MODE_OPTION] = self.combo_mode.currentData()


register_track_metadata_processor(process_track)
register_album_metadata_processor(process_album)
register_options_page(AutoRomanizerOptionsPage)
