# -*- coding: utf-8 -*-
import os
import re
import shutil
from picard import log
from picard.file import register_file_post_save_processor
from picard.ui.itemviews import BaseAction, register_album_action, register_track_action

PLUGIN_NAME = "File Deduplicator (Quality & Root Backup)"
PLUGIN_AUTHOR = "SPbot / devil1210"
PLUGIN_DESCRIPTION = "Mueve duplicados (1) a M:\\music\\_duplicados_backup y asegura que las letras .lrc queden siempre con el nombre limpio sin (1)."
PLUGIN_VERSION = "1.4"
PLUGIN_API_VERSIONS = ["2.0", "2.1", "2.2", "2.3", "2.4", "2.5", "2.6", "2.7", "2.8", "2.9", "2.10", "2.11", "2.12", "2.13", "3.0"]
PLUGIN_LICENSE = "GPL-2.0"

DUP_PATTERN = re.compile(r'\s*\(\d+\)$')

def get_backup_dir(dirpath):
    norm = os.path.normpath(dirpath)
    if norm.lower().startswith(r"m:\music"):
        rel = os.path.relpath(norm, r"M:\music")
        return os.path.join(r"M:\music\_duplicados_backup", rel)
    return os.path.join(dirpath, "_duplicados_backup")

def get_quality_score(filepath):
    if not os.path.exists(filepath):
        return 0
    ext = os.path.splitext(filepath)[1].lower()
    size = os.path.getsize(filepath)
    
    if ext in ['.flac', '.alac', '.wav', '.aiff']:
        base = 10000000000
    elif ext in ['.m4a', '.aac', '.opus']:
        base = 5000000000
    else:
        base = 1000000000
        
    return base + size

def sync_lrc_files(dirpath, clean_base, dup_base):
    clean_lrc = os.path.join(dirpath, clean_base + ".lrc")
    dup_lrc = os.path.join(dirpath, dup_base + ".lrc")
    backup_dir = get_backup_dir(dirpath)

    if os.path.exists(dup_lrc):
        if not os.path.exists(clean_lrc):
            try:
                os.rename(dup_lrc, clean_lrc)
                log.info("File Deduplicator: Renombrada letra %s.lrc -> %s.lrc", dup_base, clean_base)
            except Exception as e:
                log.error("File Deduplicator Error renombrando letra: %s", e)
        else:
            try:
                os.makedirs(backup_dir, exist_ok=True)
                dst = os.path.join(backup_dir, dup_base + ".lrc")
                shutil.move(dup_lrc, dst)
                log.info("File Deduplicator: Se movió letra duplicada %s.lrc -> %s", dup_base, dst)
            except Exception as e:
                log.error("File Deduplicator Error moviendo letra: %s", e)

def handle_duplicate_pair(dirpath, item):
    file_base, ext = os.path.splitext(item)
    if not DUP_PATTERN.search(file_base):
        return False
        
    clean_base = DUP_PATTERN.sub('', file_base)
    orig_name = clean_base + ext
    dup_file = os.path.join(dirpath, item)
    orig_file = os.path.join(dirpath, orig_name)
    
    backup_dir = get_backup_dir(dirpath)
    os.makedirs(backup_dir, exist_ok=True)

    # Para archivos de letras .lrc sueltos
    if ext.lower() == '.lrc':
        sync_lrc_files(dirpath, clean_base, file_base)
        return True

    # Si el original de audio no existe, renombrar el duplicado al nombre limpio
    if not os.path.exists(orig_file):
        try:
            os.rename(dup_file, orig_file)
            log.info("File Deduplicator: Renombrado audio %s -> %s", item, orig_name)
            sync_lrc_files(dirpath, clean_base, file_base)
            return True
        except Exception as e:
            log.error("File Deduplicator Error: %s", e)
            return False

    # Comparar calidad
    score_dup = get_quality_score(dup_file)
    score_orig = get_quality_score(orig_file)
    
    if score_dup > score_orig:
        # Nuevo archivo es de MEJOR calidad
        try:
            old_backup_dst = os.path.join(backup_dir, orig_name)
            shutil.move(orig_file, old_backup_dst)
            os.rename(dup_file, orig_file)
            log.info("File Deduplicator: ¡Nuevo archivo %s tiene MEJOR calidad! Se guardó como principal y el viejo se movió a %s.", item, old_backup_dst)
            sync_lrc_files(dirpath, clean_base, file_base)
            return True
        except Exception as e:
            log.error("File Deduplicator Error intercambiando mejor calidad: %s", e)
            return False
    else:
        # Nuevo archivo es de MENOR o igual calidad
        try:
            dup_backup_dst = os.path.join(backup_dir, item)
            shutil.move(dup_file, dup_backup_dst)
            log.info("File Deduplicator: Archivo %s es de MENOR calidad. Conservando original y moviendo duplicado a %s.", item, dup_backup_dst)
            sync_lrc_files(dirpath, clean_base, file_base)
            return True
        except Exception as e:
            log.error("File Deduplicator Error moviendo menor calidad: %s", e)
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
            if handle_duplicate_pair(dirpath, item):
                moved_count += 1
    return moved_count

def remove_duplicate_artifacts(file):
    try:
        filepath = file.filename
        if not filepath or not os.path.exists(filepath):
            return
        dirpath = os.path.dirname(filepath)
        process_directory_duplicates(dirpath)
    except Exception as e:
        log.error("File Deduplicator Error: %s", e)

class MoveDuplicatesAction(BaseAction):
    NAME = 'Mover duplicados (1) a M:\\music\\_duplicados_backup'

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

        log.info("File Deduplicator: Se procesaron %d archivos duplicados.", count)

register_file_post_save_processor(remove_duplicate_artifacts)
register_album_action(MoveDuplicatesAction())
register_track_action(MoveDuplicatesAction())
