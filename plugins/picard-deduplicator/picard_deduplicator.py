# -*- coding: utf-8 -*-
import os
import re
import shutil
from picard import log
from picard.file import register_file_post_save_processor
from picard.ui.itemviews import BaseAction, register_album_action, register_track_action

PLUGIN_NAME = "File Deduplicator (Quality Aware)"
PLUGIN_AUTHOR = "SPbot / devil1210"
PLUGIN_DESCRIPTION = "Compara la calidad/tamaño entre archivos duplicados (1) y conserva SIEMPRE el de mayor calidad, moviendo el de menor calidad y sus letras a _duplicados_backup."
PLUGIN_VERSION = "1.3"
PLUGIN_API_VERSIONS = ["2.0", "2.1", "2.2", "2.3", "2.4", "2.5", "2.6", "2.7", "2.8", "2.9", "2.10", "2.11", "2.12", "2.13", "3.0"]
PLUGIN_LICENSE = "GPL-2.0"

DUP_PATTERN = re.compile(r'\s*\(\d+\)$')

def get_quality_score(filepath):
    """
    Devuelve un puntaje de calidad basado en formato y tamaño de archivo.
    """
    if not os.path.exists(filepath):
        return 0
    ext = os.path.splitext(filepath)[1].lower()
    size = os.path.getsize(filepath)
    
    # Formatos sin pérdida reciben la máxima prioridad
    if ext in ['.flac', '.alac', '.wav', '.aiff']:
        base = 10000000000
    elif ext in ['.m4a', '.aac', '.opus']:
        base = 5000000000
    else:  # .mp3, .ogg
        base = 1000000000
        
    return base + size

def handle_duplicate_pair(dirpath, item):
    file_base, ext = os.path.splitext(item)
    if not DUP_PATTERN.search(file_base):
        return False
        
    orig_name = DUP_PATTERN.sub('', file_base) + ext
    dup_file = os.path.join(dirpath, item)
    orig_file = os.path.join(dirpath, orig_name)
    
    if not os.path.exists(orig_file) or dup_file == orig_file:
        return False
        
    backup_dir = os.path.join(dirpath, "_duplicados_backup")
    os.makedirs(backup_dir, exist_ok=True)
    
    # Para archivos de letras .lrc, mover la versión (1) a backup
    if ext.lower() == '.lrc':
        dst = os.path.join(backup_dir, item)
        try:
            shutil.move(dup_file, dst)
            log.info("File Deduplicator: Se movió letra duplicada %s -> _duplicados_backup", item)
            return True
        except Exception as e:
            log.error("File Deduplicator: Error al mover letra %s: %s", dup_file, e)
            return False
            
    # Para archivos de audio, comparar calidad
    score_dup = get_quality_score(dup_file)
    score_orig = get_quality_score(orig_file)
    
    if score_dup > score_orig:
        # El archivo nuevo (1) tiene MEJOR calidad -> Mover el original viejo a backup y renombrar el nuevo
        try:
            old_backup_dst = os.path.join(backup_dir, orig_name)
            shutil.move(orig_file, old_backup_dst)
            os.rename(dup_file, orig_file)
            log.info("File Deduplicator: ¡Nuevo archivo %s tiene MEJOR calidad! Se guardó como principal y el viejo se movió a backup.", item)
            return True
        except Exception as e:
            log.error("File Deduplicator: Error al intercambiar mejor calidad: %s", e)
            return False
    else:
        # El archivo nuevo (1) es de MENOR o IGUAL calidad -> Mover el nuevo (1) a backup y conservar el original
        try:
            dup_backup_dst = os.path.join(backup_dir, item)
            shutil.move(dup_file, dup_backup_dst)
            log.info("File Deduplicator: Archivo %s es de MENOR calidad. Conservando original y moviendo duplicado a backup.", item)
            return True
        except Exception as e:
            log.error("File Deduplicator: Error al mover menor calidad: %s", e)
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
    NAME = 'Mover duplicados (1) a _duplicados_backup (Inteligente por Calidad)'

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
