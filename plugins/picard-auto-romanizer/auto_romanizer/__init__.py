# -*- coding: utf-8 -*-
PLUGIN_NAME = "Auto Romanizer"
PLUGIN_AUTHOR = "SPbot"
PLUGIN_DESCRIPTION = "Romaniza automáticamente títulos, artistas y álbumes de japonés a Romaji preservando metadatos originales. Conserva títulos que ya tienen traducción al inglés/Romaji."
PLUGIN_VERSION = "3.9"
PLUGIN_API_VERSIONS = ["2.0", "2.1", "2.2", "2.3", "2.4", "2.5", "2.6", "2.7", "2.8", "2.9", "2.10", "2.11", "2.12", "2.13"]
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
LATIN_META_WORDS = {'feat', 'ft', 'cv', 'tv', 'ver', 'version', 'vs', 'ep', 'op', 'ed', 'remix', 'mix', 'instrumental', 'off', 'vocal', 'acoustic'}
_JP_RE = re.compile(r'[\u3040-\u309f\u30a0-\u30ff\u4e00-\u9faf\uff00-\uffef]')


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


def romanize_dict(tags_dict):
    if not os.path.exists(SCRIPT_PATH):
        return tags_dict
    try:
        creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)
        proc = subprocess.Popen(
            [PYTHON_PATH, SCRIPT_PATH, "--json-dict", json.dumps(tags_dict)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=creationflags
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
    for k in ['_original_title', '_original_artist', '_original_album', '_original_albumartist']:
        if k in metadata:
            del metadata[k]


def _get_file_dual_title(track):
    """Returns the existing dual-language title from the file on disk, or None."""
    if not (track and hasattr(track, 'files')):
        return None
    for f in track.files:
        file_title = f.metadata.get('title') if hasattr(f, 'metadata') else None
        filename_base = os.path.splitext(os.path.basename(f.filename))[0] if hasattr(f, 'filename') else ''
        clean_name = re.sub(r'^\d+[\s\.\-_]+', '', filename_base).strip()
        if file_title and already_has_latin_translation(file_title):
            return file_title
        if clean_name and already_has_latin_translation(clean_name):
            return clean_name
    return None


def process_track(tagger, metadata, track, release):
    mode = config.setting[TITLE_MODE_OPTION] if TITLE_MODE_OPTION in config.setting else DEFAULT_MODE

    # Guardar originales
    if metadata.get('title') and 'originaltitle' not in metadata:
        metadata['originaltitle'] = metadata['title']
    if metadata.get('artist') and 'originalartist' not in metadata:
        metadata['originalartist'] = metadata['artist']
    if metadata.get('album') and 'originalalbum' not in metadata:
        metadata['originalalbum'] = metadata['album']
    _clean_internal_tags(metadata)

    # Aplicar modo de conversión al título
    orig_title = metadata.get('title', '')
    if orig_title and contains_japanese(orig_title):
        if mode == "auto":
            # Usar el tag original del archivo si ya tiene traducción (ej: プラネタリウム - Planetarium)
            file_dual = _get_file_dual_title(track)
            if file_dual:
                metadata['title'] = file_dual
                metadata['originaltitle'] = file_dual
            else:
                # Sin traducción en el archivo: convertir a Romaji
                result = romanize_dict({'title': orig_title})
                if 'title' in result:
                    metadata['title'] = result['title']
        elif mode == "japanese":
            pass  # Conservar japonés sin cambiar
        elif mode == "dual":
            result = romanize_dict({'title': orig_title})
            romaji = result.get('title', orig_title)
            if romaji and romaji != orig_title:
                metadata['title'] = "{} - {}".format(orig_title, romaji)
        else:  # romaji
            result = romanize_dict({'title': orig_title})
            if 'title' in result:
                metadata['title'] = result['title']

    # Convertir artista/álbum siempre a Romaji
    to_convert = {}
    for k in ['artist', 'album', 'albumartist']:
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
    for k in ['title', 'album', 'albumartist']:
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

    options = [
        config.TextOption("setting", TITLE_MODE_OPTION, DEFAULT_MODE),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        # Lazy import de Qt para no crashear en module load
        from PyQt5 import QtWidgets

        self.combo_mode = QtWidgets.QComboBox(self)
        self.combo_mode.addItem("Automático: conservar tag original si ya tiene traducción (ej: プラネタリウム - Planetarium)", "auto")
        self.combo_mode.addItem("Solo Romaji: convertir a Romaji (ej: Puranetariumu)", "romaji")
        self.combo_mode.addItem("Dual: Japonés + Romaji generado (ej: プラネタリウム - Puranetariumu)", "dual")
        self.combo_mode.addItem("Original: conservar Japonés sin cambiar (ej: プラネタリウム)", "japanese")

        form = QtWidgets.QFormLayout()
        form.addRow(QtWidgets.QLabel("Modo de conversión de títulos:"), self.combo_mode)

        group = QtWidgets.QGroupBox("Formato de Títulos en Japonés", self)
        group.setLayout(form)

        vbox = QtWidgets.QVBoxLayout(self)
        vbox.addWidget(group)
        vbox.addStretch()

    def load(self):
        mode = config.setting[TITLE_MODE_OPTION] if TITLE_MODE_OPTION in config.setting else DEFAULT_MODE
        idx = self.combo_mode.findData(mode)
        if idx >= 0:
            self.combo_mode.setCurrentIndex(idx)

    def save(self):
        config.setting[TITLE_MODE_OPTION] = self.combo_mode.currentData()


register_track_metadata_processor(process_track)
register_album_metadata_processor(process_album)
register_options_page(AutoRomanizerOptionsPage)
