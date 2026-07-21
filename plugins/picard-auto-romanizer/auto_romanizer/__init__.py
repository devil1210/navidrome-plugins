# -*- coding: utf-8 -*-
PLUGIN_NAME = "Auto Romanizer"
PLUGIN_AUTHOR = "SPbot"
PLUGIN_DESCRIPTION = "Romaniza automáticamente títulos, artistas y álbumes de japonés a Romaji preservando metadatos originales (ORIGINALTITLE, ORIGINALARTIST, ORIGINALALBUM) para búsqueda de letras sincronizadas."
PLUGIN_VERSION = "3.6"
PLUGIN_API_VERSIONS = ["2.0", "2.1", "2.2", "2.3", "2.4", "2.5", "2.6", "2.7", "2.8", "2.9", "2.10", "2.11", "2.12", "2.13"]
PLUGIN_LICENSE = "GPL-2.0"

from picard import config, log
from picard.metadata import register_track_metadata_processor, register_album_metadata_processor
from picard.ui.options import OptionsPage, register_options_page
from picard.ui.qt import QtWidgets
import subprocess
import json
import os
import re

TITLE_MODE_OPTION = "auto_romanizer_title_mode"

LOCAL_SCRIPT = os.path.join(os.path.dirname(__file__), "romanizer.py")
SPBOT_SCRIPT = r"E:\Descargas\SPbot\scripts\romanizer.py"
SCRIPT_PATH = LOCAL_SCRIPT if os.path.exists(LOCAL_SCRIPT) else SPBOT_SCRIPT
PYTHON_PATH = r"python"

LATIN_META_WORDS = {'feat', 'ft', 'cv', 'tv', 'ver', 'version', 'vs', 'ep', 'op', 'ed', 'remix', 'mix', 'instrumental', 'off', 'vocal', 'acoustic'}

def contains_japanese(text):
    if not text:
        return False
    for char in text:
        cp = ord(char)
        if (0x3040 <= cp <= 0x309F) or (0x30A0 <= cp <= 0x30FF) or (0x4E00 <= cp <= 0x9FAF) or (0xFF00 <= cp <= 0xFFEF):
            return True
    return False

def already_has_latin_translation(text):
    if not text or not contains_japanese(text):
        return False
    parts = re.split(r'\s*[\-\–\—\/\(\)]\s*', text)
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

def romanize_dict(tags_dict):
    if not os.path.exists(SCRIPT_PATH):
        return tags_dict
    try:
        raw_json = json.dumps(tags_dict)
        creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)
        proc = subprocess.Popen([PYTHON_PATH, SCRIPT_PATH, "--json-dict", raw_json], stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=creationflags)
        out, err = proc.communicate(timeout=5)
        if not out:
            if err:
                log.error("Auto Romanizer Process Error: %s", err.decode('utf-8', errors='ignore'))
            return tags_dict
        res = json.loads(out.decode('utf-8', errors='ignore'))
        if isinstance(res, dict) and "error" not in res:
            return res
        elif isinstance(res, dict) and "error" in res:
            log.error("Auto Romanizer Script Error: %s", res["error"])
        return tags_dict
    except Exception as e:
        log.error("Auto Romanizer Error: %s", e)
        return tags_dict

def clean_up_internal_tags(metadata):
    for k in ['_original_title', '_original_artist', '_original_album', '_original_albumartist']:
        if k in metadata:
            del metadata[k]

def process_track(tagger, metadata, track, release):
    mode = config.setting[TITLE_MODE_OPTION] if TITLE_MODE_OPTION in config.setting else "dual"

    # 1. Comprobar si el archivo local ya traía un título bilingüe (Japonés + Romaji/Inglés)
    file_dual_title = None
    if track and hasattr(track, 'files'):
        for f in track.files:
            file_meta_title = f.metadata.get('title') if hasattr(f, 'metadata') else None
            filename_base = os.path.splitext(os.path.basename(f.filename))[0] if hasattr(f, 'filename') else ''
            clean_filename = re.sub(r'^\d+[\s\.\-_]+', '', filename_base).strip()

            if file_meta_title and already_has_latin_translation(file_meta_title):
                file_dual_title = file_meta_title
                break
            elif clean_filename and already_has_latin_translation(clean_filename):
                file_dual_title = clean_filename
                break

    if file_dual_title:
        metadata['title'] = file_dual_title
        metadata['originaltitle'] = file_dual_title
        clean_up_internal_tags(metadata)
        return

    # Preservar etiquetas originales
    if metadata.get('title') and 'originaltitle' not in metadata:
        metadata['originaltitle'] = metadata['title']
    if metadata.get('artist') and 'originalartist' not in metadata:
        metadata['originalartist'] = metadata['artist']
    if metadata.get('album') and 'originalalbum' not in metadata:
        metadata['originalalbum'] = metadata['album']

    clean_up_internal_tags(metadata)

    # 2. Aplicar modo de formateo de títulos
    orig_title = metadata.get('title', '')
    if orig_title and contains_japanese(orig_title):
        if mode == "japanese":
            pass # Dejar en japonés original
        elif mode == "dual":
            converted_dict = romanize_dict({'title': orig_title})
            romaji_title = converted_dict.get('title', orig_title)
            if romaji_title and romaji_title != orig_title:
                metadata['title'] = f"{orig_title} - {romaji_title}"
        elif mode == "romaji":
            converted_dict = romanize_dict({'title': orig_title})
            if 'title' in converted_dict:
                metadata['title'] = converted_dict['title']

    # Convertir artistas y álbumes a Romaji
    to_convert = {}
    for key in ['artist', 'album', 'albumartist']:
        val = metadata.get(key)
        if val and contains_japanese(val):
            to_convert[key] = val
    if to_convert:
        converted = romanize_dict(to_convert)
        for k, v in converted.items():
            metadata[k] = v

def process_album(tagger, metadata, release):
    if metadata.get('title') and 'originalalbum' not in metadata:
        metadata['originalalbum'] = metadata['title']
    if metadata.get('albumartist') and 'originalalbumartist' not in metadata:
        metadata['originalalbumartist'] = metadata['albumartist']

    clean_up_internal_tags(metadata)

    to_convert = {}
    for key in ['title', 'album', 'albumartist']:
        val = metadata.get(key)
        if val and contains_japanese(val):
            to_convert[key] = val
    if to_convert:
        converted = romanize_dict(to_convert)
        for k, v in converted.items():
            metadata[k] = v

class AutoRomanizerOptionsPage(OptionsPage):
    NAME = "auto_romanizer"
    TITLE = "Auto Romanizer"
    PARENT = "plugins"

    options = [
        config.TextOption("setting", TITLE_MODE_OPTION, "dual"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.combo_mode = QtWidgets.QComboBox(self)
        self.combo_mode.addItem("Dual: Japonés - Romaji/Inglés (ej: プラネタリウム - Planetarium)", "dual")
        self.combo_mode.addItem("Original: Conservar Japonés intacto (ej: プラネタリウム)", "japanese")
        self.combo_mode.addItem("Solo Romaji: Convertir únicamente a Romaji (ej: Puranetariumu)", "romaji")

        form = QtWidgets.QFormLayout()
        form.addRow(QtWidgets.QLabel("Modo de conversión de títulos:"), self.combo_mode)

        group = QtWidgets.QGroupBox("Formato de Títulos en Japonés", self)
        group.setLayout(form)

        vbox = QtWidgets.QVBoxLayout(self)
        vbox.addWidget(group)
        vbox.addStretch()

    def load(self):
        mode = config.setting[TITLE_MODE_OPTION] if TITLE_MODE_OPTION in config.setting else "dual"
        index = self.combo_mode.findData(mode)
        if index >= 0:
            self.combo_mode.setCurrentIndex(index)

    def save(self):
        config.setting[TITLE_MODE_OPTION] = self.combo_mode.currentData()

register_track_metadata_processor(process_track)
register_album_metadata_processor(process_album)
register_options_page(AutoRomanizerOptionsPage)
