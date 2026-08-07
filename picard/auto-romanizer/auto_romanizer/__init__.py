# -*- coding: utf-8 -*-
#
# Copyright (C) 2024-2026 Auto Romanizer Plugin for MusicBrainz Picard
#

import os
import sys
import re
from difflib import SequenceMatcher
from functools import lru_cache

# ── Load pykakasi from vendored copy inside plugin ──
_here = os.path.dirname(os.path.abspath(__file__))
_vendor = os.path.join(_here, "vendor")
_roaming_vendor = r"C:\Users\charl\AppData\Roaming\MusicBrainz\Picard\plugins3\auto_romanizer\vendor"
_roaming_here = r"C:\Users\charl\AppData\Roaming\MusicBrainz\Picard\plugins3\auto_romanizer"

for p in [_vendor, _here, _roaming_vendor, _roaming_here]:
    if os.path.isdir(p) and p not in sys.path:
        sys.path.insert(0, p)

_kks = None
try:
    import pykakasi
    _kks = pykakasi.kakasi()
except Exception as e:
    _kks = None

from picard import log
from picard.config import config
from picard.plugin3.api import OptionsPage, PluginApi

PLUGIN_NAME = "Auto Romanizer"
PLUGIN_AUTHOR = "Dev"
PLUGIN_DESCRIPTION = "Automatic Japanese/Romaji title and album romanization"
PLUGIN_VERSION = "1.0.0"
PLUGIN_API_VERSIONS = ["3.0", "3.1", "3.2"]

TITLE_MODE_OPTION = "auto_romanizer_mode"
DEFAULT_MODE = "auto"  # "auto" (dual), "japanese", "romaji"

_api = None

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


def _normalize_for_comparison(s: str) -> str:
    """Lowercase, strip spaces and non-alphanumeric for phonetic comparison."""
    return re.sub(r'[^a-z0-9]', '', str(s).lower())


def safe_to_romaji(text: str) -> str:
    if not text or not contains_japanese(text):
        return text
    try:
        import pykakasi
        kks = pykakasi.kakasi()
        conv = kks.convert(str(text))
        if not conv:
            return str(text)
        words = []
        for item in conv:
            orig = item.get('orig', '')
            h = item.get('hepburn', '').strip()
            if not h:
                if orig:
                    words.append(orig)
                continue
            if orig == '・':
                words.append('・')
            elif h.lower() in ('no', 'ga', 'to', 'ni', 'wa', 'o', 'e', 'de', 'mo', 'ka', 'ya', 'na', 'ne', 'wo'):
                words.append(h.lower())
            else:
                words.append(h.capitalize())
        res = ' '.join(words).strip()
        res = re.sub(r'\s*([・\-\–\—\(\)])\s*', r'\1', res)
        res = re.sub(r'\)\s*([a-zA-Z])', r') \1', res)
        return res if res else str(text)
    except Exception as e:
        log.error("Auto Romanizer safe_to_romaji error for %r: %s", text, e)
        return str(text)


def romanize_dict(tags_dict: dict) -> dict:
    res = {}
    for k, v in tags_dict.items():
        if isinstance(v, str) and contains_japanese(v):
            res[k] = safe_to_romaji(v)
        else:
            res[k] = v
    return res


@lru_cache(maxsize=4096)
def _is_romanization_of(latin_text: str, jp_text: str) -> bool:
    """Return True only if latin_text is a phonetic romanization of jp_text."""
    if not latin_text or not jp_text or not contains_japanese(jp_text):
        return False
    romanized = safe_to_romaji(jp_text)
    if not romanized or romanized == jp_text:
        return False
    lat_norm = _normalize_for_comparison(latin_text)
    rom_norm = _normalize_for_comparison(romanized)
    if not lat_norm or not rom_norm:
        return False
    if lat_norm == rom_norm:
        return True
    shorter, longer = (lat_norm, rom_norm) if len(lat_norm) <= len(rom_norm) else (rom_norm, lat_norm)
    if len(shorter) >= 4 and shorter in longer:
        return True
    ratio = SequenceMatcher(None, lat_norm, rom_norm).ratio()
    return ratio >= 0.70


def _is_corresponding_translation(lat: str, jp: str) -> bool:
    if not lat or not jp:
        return False
    lat_clean = lat.strip()
    jp_clean = jp.strip()

    if _is_romanization_of(lat_clean, jp_clean):
        return True

    lat_lower = lat_clean.lower()
    noise_patterns = [
        r'^track\s*\d+', r'^\d+$', r'^pista\s*\d+', r'^audio\d*',
        r'^unknown', r'^untitled', r'^no\s*title', r'^artist',
        r'^album', r'^disc\s*\d+', r'^flac$', r'^mp3$'
    ]
    for pat in noise_patterns:
        if re.match(pat, lat_lower):
            return False

    words = [w for w in re.findall(r'[a-zA-Z]{2,}', lat_clean)]
    if len(words) >= 1:
        return True

    return False


def already_has_latin_translation(text: str) -> bool:
    """Check if text already has a verified corresponding Latin/English translation or Romaji attached."""
    if not text or not contains_japanese(text):
        return False
    parts = re.split(r'\s*[\-\–\—\/\(\)]\s*', str(text))
    if len(parts) < 2:
        return False
    jp_parts = [p.strip() for p in parts if contains_japanese(p.strip())]
    lat_parts = [p.strip() for p in parts if not contains_japanese(p.strip()) and any(c.isalpha() for c in p)]
    if jp_parts and lat_parts:
        for jp in jp_parts:
            for lat in lat_parts:
                if _is_corresponding_translation(lat, jp):
                    return True
    return False


def has_dual_structure(text: str) -> bool:
    if not text:
        return False
    parts = re.split(r'\s*[\-\–\—\/]\s*', str(text))
    if len(parts) < 2:
        return False
    if contains_japanese(text) and already_has_latin_translation(text):
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
    # Repair previously corrupted nested parentheses e.g. '(LEO (NiNE ver.))' -> '(LEO-NiNE ver.)'
    title_text = re.sub(r'\(([^()]+)\s*\(([^()]+)\)\)', r'(\1-\2)', str(title_text))
    parts = [p.strip() for p in re.split(r'\s+[\-\–\—]\s+', str(title_text)) if p.strip()]
    if len(parts) >= 2:
        new_parts = []
        i = 0
        while i < len(parts):
            p = parts[i]
            if i + 1 < len(parts):
                next_p = parts[i + 1]
                words = [w.lower() for w in re.findall(r'[a-zA-Z]{2,}', next_p)]
                if words and any(w in LATIN_META_WORDS for w in words) and not p.endswith(')'):
                    clean_next = re.sub(r'[\.\-_\s]+$', '', next_p)
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
        parts = re.split(r'\s*[\-\–\—]\s*', clean_name)
        jp_parts = [p.strip() for p in parts if contains_japanese(p.strip())]
        if jp_parts:
            return jp_parts[-1]

    return None


def _get_option(key, default=None):
    global _api
    if _api and hasattr(_api, "plugin_config"):
        try:
            val = _api.plugin_config.get(key)
            if val is not None:
                return val
        except Exception:
            pass
    if hasattr(config, "setting"):
        try:
            val = config.setting[key]
            if val is not None:
                return val
        except Exception:
            pass
    return default


def _apply_romanization(api, track, metadata, file=None):
    mode = _get_option(TITLE_MODE_OPTION, DEFAULT_MODE)
    log.info(f"[Auto Romanizer] _apply_romanization called: mode='{mode}', orig_title='{metadata.get('title') if metadata else None}'")

    existing_orig_title = None
    if file and hasattr(file, 'orig_metadata') and file.orig_metadata:
        existing_orig_title = file.orig_metadata.get('originaltitle') or file.orig_metadata.get('_original_title')
    if not existing_orig_title:
        existing_orig_title = metadata.get('originaltitle') or metadata.get('_original_title')
    if isinstance(existing_orig_title, list) and existing_orig_title:
        existing_orig_title = existing_orig_title[0]

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
                    metadata['title'] = target_title
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
                    try:
                        romaji = safe_to_romaji(clean_jp)
                        log.info(f"[Auto Romanizer] safe_to_romaji('{clean_jp}') produced: '{romaji}'")
                        if romaji and romaji != clean_jp:
                            new_t = f"{clean_jp} - {romaji}"
                            metadata['title'] = new_t
                            log.info(f"[Auto Romanizer] Converted: '{orig_title}' -> '{new_t}'")
                        else:
                            metadata['title'] = clean_jp
                    except Exception as err:
                        log.error(f"[Auto Romanizer Error in safe_to_romaji]: {err}", exc_info=True)
                        metadata['title'] = clean_jp
                elif mode == "japanese":
                    metadata['title'] = clean_jp
                elif mode == "romaji":
                    romaji = safe_to_romaji(clean_jp)
                    new_t = romaji if romaji else clean_jp
                    metadata['title'] = new_t
                    log.info(f"[Auto Romanizer] Converted (romaji): '{orig_title}' -> '{new_t}'")
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


def _extract_args(args):
    track = None
    album = None
    file = None
    metadata = None

    if len(args) >= 3 and (hasattr(args[2], 'add_unique') or hasattr(args[2], 'getall') or type(args[2]).__name__ == 'Metadata' or isinstance(args[2], dict)):
        metadata = args[2]
        if hasattr(args[1], 'album') or hasattr(args[1], 'linked_files') or type(args[1]).__name__ == 'Track':
            track = args[1]
        elif hasattr(args[1], 'tracks'):
            album = args[1]

    if not metadata:
        for a in args:
            if hasattr(a, 'filename') and hasattr(a, 'metadata'):
                file = a
            elif hasattr(a, 'tracks') and hasattr(a, 'metadata'):
                album = a
            elif hasattr(a, 'album') or hasattr(a, 'linked_files') or type(a).__name__ == 'Track':
                track = a
            elif hasattr(a, 'add_unique') or hasattr(a, 'getall') or type(a).__name__ == 'Metadata' or isinstance(a, dict):
                if type(a).__name__ != 'PluginApi':
                    metadata = a

    return track, album, file, metadata


def process_track(*args, **kwargs):
    log.info(f"[Auto Romanizer] process_track called with {len(args)} args: {[type(a).__name__ for a in args]}")
    try:
        track, album, file, metadata = _extract_args(args)
        if metadata:
            _apply_romanization(_api, track, metadata)
        else:
            log.warning(f"[Auto Romanizer] process_track: metadata extraction failed from args")
    except Exception as e:
        log.error(f"[Auto Romanizer Error in process_track]: {e}", exc_info=True)


def process_album(*args, **kwargs):
    log.info(f"[Auto Romanizer] process_album called with {len(args)} args: {[type(a).__name__ for a in args]}")
    try:
        track, album, file, metadata = _extract_args(args)
        if metadata:
            _apply_romanization(_api, album, metadata)
    except Exception as e:
        log.error(f"[Auto Romanizer Error in process_album]: {e}", exc_info=True)


def on_file_added_to_track(*args, **kwargs):
    log.info(f"[Auto Romanizer] on_file_added_to_track called with {len(args)} args: {[type(a).__name__ for a in args]}")
    try:
        track, album, file, metadata = _extract_args(args)
        if file and hasattr(file, "metadata"):
            _apply_romanization(_api, track, file.metadata, file=file)
        if track and hasattr(track, "metadata"):
            _apply_romanization(_api, track, track.metadata, file=file)
        if metadata:
            _apply_romanization(_api, track, metadata, file=file)
    except Exception as e:
        log.error(f"[Auto Romanizer Error in on_file_added_to_track]: {e}", exc_info=True)


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
        mode = _get_option(TITLE_MODE_OPTION, DEFAULT_MODE)
        index = self.combo_mode.findData(mode)
        if index >= 0:
            self.combo_mode.setCurrentIndex(index)

    def save(self):
        mode = self.combo_mode.currentData()
        if hasattr(self, 'api') and self.api and hasattr(self.api, 'plugin_config'):
            self.api.plugin_config[TITLE_MODE_OPTION] = mode
        config.setting[TITLE_MODE_OPTION] = mode


def enable(api: PluginApi):
    global _api
    _api = api
    if hasattr(api, "plugin_config") and hasattr(api.plugin_config, "register_option"):
        try:
            api.plugin_config.register_option(TITLE_MODE_OPTION, DEFAULT_MODE)
        except Exception:
            pass
    log.info("[Auto Romanizer] Engine v1.0.1 with vendored jaconv initialized!")
    api.register_track_metadata_processor(process_track)
    api.register_album_metadata_processor(process_album)
    api.register_file_post_addition_to_track_processor(on_file_added_to_track)
    api.register_options_page(AutoRomanizerOptionsPage)
