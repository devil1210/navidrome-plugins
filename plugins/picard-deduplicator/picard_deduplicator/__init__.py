# -*- coding: utf-8 -*-
import os
import re
import shutil
from picard import log
from picard.file import register_file_post_save_processor
from picard.ui.itemviews import BaseAction, register_album_action, register_track_action

PLUGIN_NAME = "File Deduplicator"
PLUGIN_AUTHOR = "SPbot / devil1210"
PLUGIN_DESCRIPTION = "Mueve automáticamente archivos duplicados (1), (2) y sus letras (.lrc) a _duplicados_backup al guardar en Picard o mediante la opción de menú."
PLUGIN_VERSION = "1.2"
PLUGIN_API_VERSIONS = ["2.0", "2.1", "2.2", "2.3", "2.4", "2.5", "2.6", "2.7", "2.8", "2.9", "2.10", "2.11", "2.12", "2.13", "3.0"]
PLUGIN_LICENSE = "GPL-2.0"

DUP_PATTERN = re.compile(r'\s*\(\d+\)$')

def handle_duplicate_file(dirpath, filename):
    file_base, ext = os.path.splitext(filename)
    if DUP_PATTERN.search(file_base):
        orig_name = DUP_PATTERN.sub('', file_base) + ext
        orig_file = os.path.join(dirpath, orig_name)
        dup_file = os.path.join(dirpath, filename)
        
        if os.path.exists(orig_file) and dup_file != orig_file:
            backup_dir = os.path.join(dirpath, "_duplicados_backup")
            dst = os.path.join(backup_dir, filename)
            try:
                os.makedirs(backup_dir, exist_ok=True)
                shutil.move(dup_file, dst)
                log.info("File Deduplicator: Se movió duplicado %s -> _duplicados_backup", filename)
                return True
            except Exception as e:
                log.error("File Deduplicator: Error al mover %s: %s", dup_file, e)
    return False

def process_directory_duplicates(dirpath):
    if not dirpath or not os.path.exists(dirpath):
        return 0
    
    moved_count = 0
    for item in os.listdir(dirpath):
        if item == "_duplicados_backup":
            continue
        full_path = os.path.join(dirpath, item)
        if os.path.isfile(full_path):
            if handle_duplicate_file(dirpath, item):
                moved_count += 1
    return moved_count

def remove_duplicate_artifacts(file):
    """
    Al guardar una pista en Picard, escanea la carpeta y mueve cualquier 
    archivo duplicado con sufijo (1), (2) (audio y letras .lrc) a la carpeta _duplicados_backup.
    """
    try:
        filepath = file.filename
        if not filepath or not os.path.exists(filepath):
            return
        
        dirpath = os.path.dirname(filepath)
        process_directory_duplicates(dirpath)
    except Exception as e:
        log.error("File Deduplicator Error: %s", e)

class MoveDuplicatesAction(BaseAction):
    NAME = 'Mover duplicados (1) a _duplicados_backup'

    def callback(self, objs):
        dirs_to_scan = set()
        for obj in objs:
            if hasattr(obj, 'files'):
                for f in obj.files:
                    dirs_to_scan.add(os.path.dirname(f.filename))
            elif hasattr(obj, 'filename'):
                dirs_to_scan.add(os.path.dirname(obj.filename))
        
        count = 0
        for d in dirs_to_scan:
            count += process_directory_duplicates(d)

        log.info("File Deduplicator: Se movieron %d archivos duplicados.", count)

register_file_post_save_processor(remove_duplicate_artifacts)
register_album_action(MoveDuplicatesAction())
register_track_action(MoveDuplicatesAction())
