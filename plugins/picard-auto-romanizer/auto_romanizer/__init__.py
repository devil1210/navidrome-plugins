# -*- coding: utf-8 -*-
PLUGIN_NAME = "Auto Romanizer"
PLUGIN_AUTHOR = "SPbot"
PLUGIN_DESCRIPTION = (
    "Romaniza automáticamente títulos, artistas y álbumes de japonés a Romaji "
    "preservando metadatos originales. En modo Automático conserva títulos que "
    "ya tienen traducción al inglés/Romaji desde el archivo original."
)
PLUGIN_VERSION = "3.13"
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

# ── Cache: base-japanese-title → full dual title from original file ──────────
# Built when files are loaded into Picard, BEFORE the MusicBrainz lookup runs.
_title_cache = {}   # e.g. {'プラネタリウム': 'プラネタリウム - Planetarium'}


def contains_japanese(text):
    return bool(text and _JP_RE.search(text))


def already_has_latin_translation(text):
    if not text or not contains_japanese(text):
        return False
    parts = re.split(r'\s*[\-\–\—\/\(\)]\s*', text)
    if len(parts) < 2:
        return False
    has_jp = has_latin = False
    for p in parts:
        p = p.strip()
        if contains_japanese(p):
            has_jp = True
        else:
            words = [w.lower().rstrip('.') for w in re.findall(r'[a-zA-Z]{2,}', p)]
            if any(w not in LATIN_META_WORDS for w in words):
                has_latin = True
    return has_jp and has_latin


def _extract_base_jp(text):
    """Return the core Japanese words before any version/qualifier suffixes.

    'プラネタリウム - Planetarium'  → 'プラネタリウム'
    '帰りたくなったよ -acoustic version-' → '帰りたくなったよ'
    """
    if not text:
        return None
    m = _JP_RE.search(text)
    if not m:
        return None
    jp_onwards = text[m.start():]
    # Cut at a whitespace-preceded '-' or '(' that starts a qualifier
    base = re.split(r'\s+[\-\(]', jp_onwards)[0].strip()
    return base if contains_japanese(base) else None


def _cache_dual_title(dual_title):
    """Add a confirmed dual-language title to the lookup cache."""
    if not dual_title or not already_has_latin_translation(dual_title):
        return
    key = _extract_base_jp(dual_title)
    if key and key not in _title_cache:
        _title_cache[key] = dual_title
        log.debug("Auto Romanizer cache: %r → %r", key, dual_title)


def _cache_file(file_):
    """Called when a file is loaded. Caches its original dual title if present."""
    # 1. From the embedded metadata tag
    for attr in ('orig_metadata', 'metadata'):
        meta = getattr(file_, attr, None)
        if meta:
            _cache_dual_title(meta.get('title', ''))

    # 2. From the filename itself (e.g. "01 - Artist - プラネタリウム - Planetarium.m4a")
    if hasattr(file_, 'filename'):
        basename = os.path.splitext(os.path.basename(file_.filename))[0]
        # Strip leading track-number
        clean = re.sub(r'^\d+[\s\.\-_]+', '', basename).strip()
        # Find where Japanese starts and take from there
        m = _JP_RE.search(clean)
        if m:
            jp_onwards = clean[m.start():]
            _cache_dual_title(jp_onwards)


# Register the file post-load hook (Picard 2.6+).  Fail silently if unavailable.
try:
    from picard.plugin import register_file_post_load_processor
    register_file_post_load_processor(_cache_file)
    log.debug("Auto Romanizer: file_post_load_processor registered")
except (ImportError, AttributeError):
    log.debug("Auto Romanizer: register_file_post_load_processor not available – "
              "auto mode will fall back to dual (Romaji) when no cache hit")


# ── Romanization helper ───────────────────────────────────────────────────────

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
            # Look up cache by the base Japanese part of the MusicBrainz title.
            # The cache was built when the files were loaded from disk.
            key = _extract_base_jp(orig_title) or orig_title
            cached_dual = _title_cache.get(key)
            log.debug("Auto Romanizer auto-mode: key=%r cache_hit=%r", key, cached_dual)
            if cached_dual:
                metadata['title'] = cached_dual
                metadata['originaltitle'] = cached_dual
            else:
                # No cached original – generate dual (Japonés - Romaji)
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
