# -*- coding: utf-8 -*-

import os
import sys
import re
import json
import subprocess

# ── Ensure site-packages is in sys.path so Picard can load pykakasi in-memory (100x FASTER) ──
for p in [
    r"C:\Users\charl\AppData\Roaming\Python\Python312\site-packages",
    r"C:\Users\charl\AppData\Local\Programs\Python\Python312\Lib\site-packages",
    r"C:\Users\charl\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.12_qbz5n2kfra8p0\LocalCache\local-packages\Python312\site-packages"
]:
    if os.path.exists(p) and p not in sys.path:
        sys.path.insert(0, p)

try:
    from . import romanizer
except Exception:
    try:
        import romanizer
    except Exception:
        romanizer = None

from picard.config import config
from picard import log
from picard.plugin3.api import File, Metadata, OptionsPage, PluginApi

PLUGIN_NAME = "Auto Romanizer"
PLUGIN_AUTHOR = "Dev"
PLUGIN_DESCRIPTION = "Automatic Japanese/Romaji title and album romanization"
PLUGIN_VERSION = "0.3.0"
PLUGIN_API_VERSIONS = ["3.0", "3.1", "3.2"]

TITLE_MODE_OPTION = "auto_romanizer_mode"
DEFAULT_MODE = "auto"  # "auto" (dual), "japanese", "romaji"

LATIN_META_WORDS = {
    'feat', 'ft', 'cv', 'tv', 'ver', 'version', 'vs', 'ep', 'op', 'ed',
    'from', 'the', 'first', 'take', 'live', 'acoustic', 'instrumental',
    'off', 'vocal', 'original', 'mix', 'remix', 'edit', 'size', 'short',
    'full', 'deluxe', 'edition', 'bonus', 'track', 'mono', 'stereo', 'remaster',
    'remastered', 'piano', 'strings', 'orchestral', 'arrange', 'arranged', 'inst'
}


def contains_japanese(text: str) -> bool:
    if not text:
        return False
    for char in str(text):
        if ('\u3040' <= char <= '\u309f') or ('\u30a0' <= char <= '\u30ff') or ('\u4e00' <= char <= '\u9faf'):
            return True
    return False


def already_has_latin_translation(text: str) -> bool:
    if not text or not contains_japanese(text):
        return False
    parts = re.split(r'\s*[\-\–\—\(\)]\s*', str(text))
    if len(parts) < 2:
        return False
    has_jp = False
    has_latin = False
    for p in parts:
        p_clean = p.strip()
        if contains_japanese(p_clean):
            has_jp = True
        else:
            words = [w.lower().rstrip('.') for w in re.findall(r'[a-zA-Z]{2,}', p_clean)]
            non_meta = [w for w in words if w not in LATIN_META_WORDS]
            if len(non_meta) >= 1:
                has_latin = True
    return has_jp and has_latin


def has_dual_structure(text: str) -> bool:
    if not text:
        return False
    parts = re.split(r'\s*[\-\–\—\/]\s*', str(text))
    if len(parts) < 2:
        return False
    if contains_japanese(text) and already_has_latin_translation(text):
        return True
    if not contains_japanese(text):
        w0 = set(re.findall(r'[a-zA-Z0-9]+', parts[0].lower())) - LATIN_META_WORDS
        w1 = set(re.findall(r'[a-zA-Z0-9]+', parts[1].lower())) - LATIN_META_WORDS
        if w0 and w1 and (w0 == w1 or w0.issubset(w1) or w1.issubset(w0)):
            return True
    return False


def _strip_track_num_prefix(text):
    if not text:
        return text
    cleaned = re.sub(r'^(?:0\d{1,2}\s*[\.\-_\/:]*|\d{1,3}\s*[\.\-_\/:]+)\s*', '', str(text)).strip()
    return cleaned if cleaned else str(text)


def _normalize_parentheses_title(title_text: str) -> str:
    if not title_text:
        return title_text
    parts = [p.strip() for p in re.split(r'\s+[\-\–\—]\s+|\s*[\-\–\—]\s*', str(title_text)) if p.strip()]
    if len(parts) >= 2:
        new_parts = []
        i = 0
        while i < len(parts):
            p = parts[i]
            if i + 1 < len(parts):
                next_p = parts[i + 1]
                words = [w.lower() for w in re.findall(r'[a-zA-Z]{2,}', next_p)]
                if words and any(w in LATIN_META_WORDS for w in words) and not p.endswith(')'):
                    clean_next = re.sub(r'[\.\-\_\s]+$', '', next_p)
                    new_parts.append(f"{p} ({clean_next})")
                    i += 2
                    continue
            new_parts.append(p)
            i += 1
        return " - ".join(new_parts)
    return str(title_text)


def _deduplicate_latin_dual(title_text: str) -> str:
    if not title_text or contains_japanese(title_text):
        return title_text
    parts = [p.strip() for p in re.split(r'\s+[\-\–\—]\s+', str(title_text)) if p.strip()]
    if len(parts) >= 2:
        p0, p1 = parts[0], parts[1]
        w0 = set(re.findall(r'[a-zA-Z0-9]+', p0.lower())) - LATIN_META_WORDS
        w1 = set(re.findall(r'[a-zA-Z0-9]+', p1.lower())) - LATIN_META_WORDS
        if w0 and w1 and (w0 == w1 or w0.issubset(w1) or w1.issubset(w0)):
            if '(' in p1 or len(p1) > len(p0):
                return _normalize_parentheses_title(p1)
            return _normalize_parentheses_title(p0)
    return str(title_text)


def _get_latin_part(dual_title):
    if not dual_title:
        return ""
    parts = re.split(r'\s*[\-\–\—\/]\s*', str(dual_title))
    for p in parts:
        p = p.strip()
        if not contains_japanese(p):
            return p
    return ""


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
                if has_dual_structure(clean_t) or already_has_latin_translation(clean_t):
                    return clean_t

    filename = getattr(f, 'filename', '')
    if filename:
        basename = os.path.splitext(os.path.basename(filename))[0]
        clean_name = _strip_track_num_prefix(basename)
        if has_dual_structure(clean_name) or already_has_latin_translation(clean_name):
            return clean_name

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
                if contains_japanese(clean_t):
                    return clean_t

    if filename:
        basename = os.path.splitext(os.path.basename(filename))[0]
        clean_name = _strip_track_num_prefix(basename)
        if contains_japanese(clean_name):
            return clean_name

    return None


def _clean_internal_tags(metadata):
    pass


def safe_to_romaji(text):
    if not text:
        return text
    if romanizer and hasattr(romanizer, 'to_romaji') and getattr(romanizer, 'kks', None) is not None:
        try:
            return romanizer.to_romaji(text)
        except Exception:
            pass
    res = romanize_dict({'title': text})
    return res.get('title', text)


PYTHON_PATH = r"C:\Users\charl\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.12_qbz5n2kfra8p0\python.exe"
LOCAL_SCRIPT = os.path.join(os.path.dirname(__file__), "romanizer.py")
SCRIPT_PATH = LOCAL_SCRIPT if os.path.exists(LOCAL_SCRIPT) else r"E:\Descargas\SPbot\scripts\romanizer.py"


def romanize_dict(tags_dict):
    global romanizer
    if romanizer and hasattr(romanizer, "to_romaji") and getattr(romanizer, "kks", None) is not None:
        try:
            res = {}
            for k, v in tags_dict.items():
                if isinstance(v, str) and contains_japanese(v):
                    res[k] = romanizer.to_romaji(v)
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
    except Exception as e:
        log.error("Auto Romanizer exception: %s", e)
    return tags_dict


def _apply_romanization(api, track, metadata, file=None):
    cfg = config.setting
    mode = cfg.get(TITLE_MODE_OPTION, DEFAULT_MODE) if hasattr(cfg, "get") else DEFAULT_MODE

    existing_orig_title = None
    if file and hasattr(file, 'orig_metadata') and file.orig_metadata:
        existing_orig_title = file.orig_metadata.get('originaltitle') or file.orig_metadata.get('_original_title')
    if not existing_orig_title:
        existing_orig_title = metadata.get('originaltitle') or metadata.get('_original_title')
    if isinstance(existing_orig_title, list) and existing_orig_title:
        existing_orig_title = existing_orig_title[0]

    _clean_internal_tags(metadata)

    orig_title = metadata.get('title', '')
    if isinstance(orig_title, list) and orig_title:
        orig_title = orig_title[0]

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
    jp_only = None

    if target_title:
        if contains_japanese(target_title):
            if already_has_latin_translation(target_title):
                parts = re.split(r'\s*[\-\–\—\/]\s*', target_title)
                for p in parts:
                    if contains_japanese(p):
                        jp_only = p.strip()
                        break
                if mode in ("auto", "dual"):
                    metadata['title'] = _normalize_parentheses_title(target_title)
                elif mode == "japanese":
                    if jp_only:
                        metadata['title'] = jp_only
                elif mode == "romaji":
                    lat = _get_latin_part(target_title)
                    if lat:
                        metadata['title'] = lat
            else:
                clean_jp = _strip_track_num_prefix(target_title)
                jp_only = clean_jp
                if mode in ("auto", "dual"):
                    romaji = safe_to_romaji(clean_jp)
                    if romaji and romaji != clean_jp:
                        metadata['title'] = _normalize_parentheses_title(f"{clean_jp} - {romaji}")
                    else:
                        metadata['title'] = clean_jp
                elif mode == "japanese":
                    metadata['title'] = clean_jp
                elif mode == "romaji":
                    romaji = safe_to_romaji(clean_jp)
                    metadata['title'] = romaji
        else:
            metadata['title'] = _normalize_parentheses_title(_deduplicate_latin_dual(target_title))

    if existing_orig_title:
        metadata['originaltitle'] = existing_orig_title
    elif jp_only:
        metadata['originaltitle'] = jp_only

    orig_album = metadata.get('album', '')
    if isinstance(orig_album, list) and orig_album:
        orig_album = orig_album[0]

    local_album = None
    if file and hasattr(file, 'orig_metadata') and file.orig_metadata:
        file_alb = file.orig_metadata.get('album')
        if file_alb:
            if isinstance(file_alb, list) and file_alb:
                file_alb = file_alb[0]
            if file_alb:
                local_album = _strip_track_num_prefix(str(file_alb))

    if orig_album and not contains_japanese(orig_album):
        if local_album and (has_dual_structure(local_album) or already_has_latin_translation(local_album)):
            target_album = local_album
        else:
            target_album = orig_album
    else:
        target_album = local_album if (local_album and (has_dual_structure(local_album) or already_has_latin_translation(local_album))) else orig_album

    if target_album:
        if 'originalalbum' not in metadata:
            if contains_japanese(target_album):
                if already_has_latin_translation(target_album):
                    parts = re.split(r'\s*[\-\–\—]\s*', target_album)
                    for p in parts:
                        if contains_japanese(p):
                            metadata['originalalbum'] = p.strip()
                            break
                else:
                    metadata['originalalbum'] = target_album
            else:
                metadata['originalalbum'] = target_album

        if contains_japanese(target_album):
            if already_has_latin_translation(target_album):
                if mode in ("auto", "dual"):
                    metadata['album'] = _normalize_parentheses_title(target_album)
                elif mode == "japanese":
                    metadata['album'] = metadata.get('originalalbum', target_album)
                elif mode == "romaji":
                    lat = _get_latin_part(target_album)
                    if lat:
                        metadata['album'] = lat
            else:
                clean_jp_alb = _strip_track_num_prefix(target_album)
                rom_alb = safe_to_romaji(clean_jp_alb)
                if mode in ("auto", "dual"):
                    if rom_alb and rom_alb != clean_jp_alb:
                        metadata['album'] = _normalize_parentheses_title(f"{clean_jp_alb} - {rom_alb}")
                    else:
                        metadata['album'] = clean_jp_alb
                elif mode == "japanese":
                    metadata['album'] = clean_jp_alb
                elif mode == "romaji":
                    metadata['album'] = rom_alb if rom_alb else clean_jp_alb
        else:
            metadata['album'] = _normalize_parentheses_title(_deduplicate_latin_dual(target_album))

    to_convert = {}
    for k in ('artist', 'albumartist'):
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


class AutoRomanizerOptionsPage(OptionsPage):
    NAME = "auto_romanizer"
    TITLE = "Auto Romanizer"
    PARENT = "plugins"

    def __init__(self, parent=None):
        super().__init__(parent)
        from PyQt6 import QtWidgets

        self.combo_mode = QtWidgets.QComboBox(self)
        self.combo_mode.addItem(
            "Automático: conservar tag original si ya tiene traducción (ej: プラネタリウム - Planetarium)", "auto"
        )
        self.combo_mode.addItem(
            "Dual: Japonés + Romaji generado (ej: プラネタリウム - Puranetariumu)", "dual"
        )
        self.combo_mode.addItem(
            "Solo Romaji: convertir a Romaji (ej: Puranetariumu)", "romaji"
        )
        self.combo_mode.addItem(
            "Original: conservar Japonés sin cambiar (ej: プラネタリウム)", "japanese"
        )

        form = QtWidgets.QFormLayout()
        form.addRow(QtWidgets.QLabel("Modo de conversión de títulos:"), self.combo_mode)

        group = QtWidgets.QGroupBox("Formato de Títulos en Japonés", self)
        group.setLayout(form)

        vbox = QtWidgets.QVBoxLayout(self)
        vbox.addWidget(group)
        vbox.addStretch()

    def load(self):
        cfg = config.setting
        mode = cfg.get(TITLE_MODE_OPTION, DEFAULT_MODE) if hasattr(cfg, "get") else DEFAULT_MODE
        idx = self.combo_mode.findData(mode)
        if idx >= 0:
            self.combo_mode.setCurrentIndex(idx)

    def save(self):
        mode = self.combo_mode.currentData()
        config.setting[TITLE_MODE_OPTION] = mode


_api = None

def enable(api: PluginApi):
    global _api
    _api = api
    """Called when plugin is enabled in Picard API v3."""
    api.register_track_metadata_processor(process_track)
    api.register_album_metadata_processor(process_album)
    api.register_file_post_addition_to_track_processor(on_file_added_to_track)
    api.register_options_page(AutoRomanizerOptionsPage)

def disable(api: PluginApi):
    global _api
    _api = None
