# -*- coding: utf-8 -*-
PLUGIN_NAME = "Auto Romanizer"
PLUGIN_AUTHOR = "SPbot"
PLUGIN_DESCRIPTION = "Romaniza automáticamente títulos, artistas y álbumes de japonés a Romaji preservando metadatos originales (ORIGINALTITLE, ORIGINALARTIST, ORIGINALALBUM) para búsqueda de letras sincronizadas."
PLUGIN_VERSION = "3.4"
PLUGIN_API_VERSIONS = ["2.0", "2.1", "2.2", "2.3", "2.4", "2.5", "2.6", "2.7", "2.8", "2.9", "2.10", "2.11", "2.12", "2.13"]
PLUGIN_LICENSE = "GPL-2.0"

from picard.metadata import register_track_metadata_processor, register_album_metadata_processor
from picard import log
import subprocess
import json
import os

SCRIPT_PATH = r"E:\Descargas\SPbot\scripts\romanizer.py"
PYTHON_PATH = r"python"

def contains_japanese(text):
    for char in text:
        cp = ord(char)
        if (0x3040 <= cp <= 0x309F) or (0x30A0 <= cp <= 0x30FF) or (0x4E00 <= cp <= 0x9FAF) or (0xFF00 <= cp <= 0xFFEF):
            return True
    return False

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

def process_track(tagger, metadata, track, release):
    if metadata.get('title'):
        metadata['_original_title'] = metadata['title']
        metadata['originaltitle'] = metadata['title']
    if metadata.get('artist'):
        metadata['_original_artist'] = metadata['artist']
        metadata['originalartist'] = metadata['artist']
    if metadata.get('album'):
        metadata['_original_album'] = metadata['album']
        metadata['originalalbum'] = metadata['album']

    to_convert = {}
    for key in ['title', 'artist', 'album', 'albumartist']:
        val = metadata.get(key)
        if val and contains_japanese(val):
            to_convert[key] = val
    if to_convert:
        converted = romanize_dict(to_convert)
        for k, v in converted.items():
            metadata[k] = v

def process_album(tagger, metadata, release):
    if metadata.get('title'):
        metadata['_original_album'] = metadata['title']
        metadata['originalalbum'] = metadata['title']
    if metadata.get('albumartist'):
        metadata['_original_albumartist'] = metadata['albumartist']
        metadata['originalalbumartist'] = metadata['albumartist']

    to_convert = {}
    for key in ['title', 'album', 'albumartist']:
        val = metadata.get(key)
        if val and contains_japanese(val):
            to_convert[key] = val
    if to_convert:
        converted = romanize_dict(to_convert)
        for k, v in converted.items():
            metadata[k] = v

register_track_metadata_processor(process_track)
register_album_metadata_processor(process_album)
