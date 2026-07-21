# -*- coding: utf-8 -*-
PLUGIN_NAME = "File Deduplicator"
PLUGIN_AUTHOR = "SPbot / devil1210"
PLUGIN_DESCRIPTION = "Previene y limpia automáticamente archivos duplicados con sufijos (1), (2) al guardar en Picard o mediante la acción del menú."
PLUGIN_VERSION = "1.0"
PLUGIN_API_VERSIONS = ["2.0", "2.1", "2.2", "2.3", "2.4", "2.5", "2.6", "2.7", "2.8", "2.9", "2.10", "2.11", "2.12", "2.13"]
PLUGIN_LICENSE = "GPL-2.0"

import os
import re
import shutil
from picard import log
from picard.file import register_file_post_save_processor
from picard.ui.itemviews import BaseAction, register_album_action, register_track_action

DUP_PATTERN = re.compile(r'\s*\(\d+\)$')

def remove_duplicate_artifacts(file):
    """
    Al guardar una pista en Picard, busca si existen duplicados con sufijo (1), (2)...
    de esa misma canción o su archivo .lrc en la misma carpeta y los limpia automáticamente.
    """
    try:
        filepath = file.filename
        if not filepath or not os.path.exists(filepath):
            return
        
        dirpath = os.path.dirname(filepath)
        base_name, ext = os.path.splitext(os.path.basename(filepath))
        
        for item in os.listdir(dirpath):
            item_base, item_ext = os.path.splitext(item)
            cleaned_base = DUP_PATTERN.sub('', item_base)
            if cleaned_base == base_name and item_base != base_name:
                dup_file = os.path.join(dirpath, item)
                try:
                    os.remove(dup_file)
                    log.info("File Deduplicator: Se eliminó duplicado automático %s", dup_file)
                except Exception as e:
                    log.error("File Deduplicator: Error al eliminar %s: %s", dup_file, e)
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
            if not os.path.exists(d):
                continue
            backup_dir = os.path.join(d, '_duplicados_backup')
            for f in os.listdir(d):
                file_base, ext = os.path.splitext(f)
                if DUP_PATTERN.search(file_base):
                    orig_name = DUP_PATTERN.sub('', file_base) + ext
                    if os.path.exists(os.path.join(d, orig_name)):
                        src = os.path.join(d, f)
                        dst = os.path.join(backup_dir, f)
                        try:
                            os.makedirs(backup_dir, exist_ok=True)
                            shutil.move(src, dst)
                            count += 1
                        except Exception as e:
                            log.error("File Deduplicator: Error al mover %s: %s", src, e)

        log.info("File Deduplicator: Se movieron %d archivos duplicados.", count)

register_file_post_save_processor(remove_duplicate_artifacts)
register_album_action(MoveDuplicatesAction())
register_track_action(MoveDuplicatesAction())
