# -*- coding: utf-8 -*-
PLUGIN_NAME = "Auto Romanizer"
PLUGIN_AUTHOR = "SPbot"
PLUGIN_DESCRIPTION = "Romaniza automáticamente títulos, artistas y álbumes de japonés a Romaji preservando metadatos originales. Conserva títulos que ya tienen traducción al inglés/Romaji."
PLUGIN_VERSION = "3.7"
PLUGIN_API_VERSIONS = ["2.0", "2.1", "2.2", "2.3", "2.4", "2.5", "2.6", "2.7", "2.8", "2.9", "2.10", "2.11", "2.12", "2.13"]
PLUGIN_LICENSE = "GPL-2.0"

import os
import re
import json
import subprocess

from picard import log
from picard.metadata import register_track_metadata_processor, register_album_metadata_processor

LOCAL_SCRIPT = os.path.join(os.path.dirname(__file__), "romanizer.py")
SPBOT_SCRIPT = r"E:\Descargas\SPbot\scripts\romanizer.py"
SCRIPT_PATH = LOCAL_SCRIPT if os.path.exists(LOCAL_SCRIPT) else SPBOT_SCRIPT
PYTHON_PATH = r"python"

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

def process_track(tagger, metadata, track, release):
    # Preserve a dual-language title from the original file on disk
    file_dual_title = None
    if track and hasattr(track, 'files'):
        for f in track.files:
            file_title = f.metadata.get('title') if hasattr(f, 'metadata') else None
            filename_base = os.path.splitext(os.path.basename(f.filename))[0] if hasattr(f, 'filename') else ''
            clean_name = re.sub(r'^\d+[\s\.\-_]+', '', filename_base).strip()
            if file_title and already_has_latin_translation(file_title):
                file_dual_title = file_title
                break
            elif clean_name and already_has_latin_translation(clean_name):
                file_dual_title = clean_name
                break

    if file_dual_title:
        metadata['title'] = file_dual_title
        metadata['originaltitle'] = file_dual_title
        _clean_internal_tags(metadata)
        return

    # Save originals before converting
    if metadata.get('title') and 'originaltitle' not in metadata:
        metadata['originaltitle'] = metadata['title']
    if metadata.get('artist') and 'originalartist' not in metadata:
        metadata['originalartist'] = metadata['artist']
    if metadata.get('album') and 'originalalbum' not in metadata:
        metadata['originalalbum'] = metadata['album']
    _clean_internal_tags(metadata)

    # Convert Japanese to Romaji
    to_convert = {k: v for k in ['title', 'artist', 'album', 'albumartist']
                  if (v := metadata.get(k)) and contains_japanese(v)}
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

    to_convert = {k: v for k in ['title', 'album', 'albumartist']
                  if (v := metadata.get(k)) and contains_japanese(v)}
    if to_convert:
        converted = romanize_dict(to_convert)
        for k, v in converted.items():
            metadata[k] = v

register_track_metadata_processor(process_track)
register_album_metadata_processor(process_album)
