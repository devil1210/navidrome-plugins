import os
import sys
import json
import re
import mutagen
from mutagen.easyid3 import EasyID3
from mutagen.flac import FLAC
from mutagen.mp4 import MP4
from mutagen.oggvorbis import OggVorbis
from pykakasi import kakasi

# Inicializar convertidor de japonés a Romaji (Hepburn)
kks = kakasi()

def contains_japanese(text: str) -> bool:
    """Detecta si una cadena contiene caracteres japoneses (Hiragana, Katakana o Kanji)."""
    for char in text:
        if ('\u3040' <= char <= '\u309f') or ('\u30a0' <= char <= '\u30ff') or ('\u4e00' <= char <= '\u9faf'):
            return True
    return False


# Bloque de caracteres japoneses (Hiragana, Katakana, Kanji, y signos JP)
_JP_SEG = re.compile(
    r'[\u3040-\u309f'   # Hiragana
    r'\u30a0-\u30ff'    # Katakana (incluye ー y ・ \u30fb)
    r'\u4e00-\u9faf'    # Kanji CJK
    r'\uff00-\uffef'    # Full-width Latin / Katakana
    r'\u300c\u300d'     # 「」
    r'\u300e\u300f'     # 『』
    r'\u3010\u3011'     # 【】
    r'\u3001\u3002'     # 、。 (coma y punto japoneses)
    r']+'
)

# Caracteres que actúan como separadores (no requieren espacio adicional junto al romaji)
_SEPARATORS = set(' \t\n\r-–—/|()\u301c~.')

def _convert_jp_segment(segment: str) -> str:
    """Convierte un segmento puramente japonés a Romaji sin espacios internos."""
    result = kks.convert(segment)
    parts = []
    for item in result:
        orig = item.get('orig', '')
        h    = item.get('hepburn', orig)
        if orig == '\u30fb':        # ・ punto medio katakana -> middle dot ASCII
            parts.append(' \u00b7 ')
        else:
            parts.append(h)
    rom = ''.join(parts)
    # Normalizar puntuación japonesa residual
    rom = rom.replace('\u3001', ', ').replace('\u3002', '.')
    rom = re.sub(r' {2,}', ' ', rom).strip()
    if rom:
        rom = rom.capitalize()
    return rom.strip()

def to_romaji(text: str) -> str:
    """
    Convierte SOLO los bloques japoneses de un texto a Romaji (Hepburn).
    El texto no japonés (inglés, números, símbolos, etc.) se preserva tal cual.
    """
    if not text:
        return text

    non_jp_parts = _JP_SEG.split(text)
    jp_matches   = _JP_SEG.findall(text)

    # Sin japonés: retornar intacto
    if not jp_matches:
        return text

    result_parts = []
    for i, part in enumerate(non_jp_parts):
        result_parts.append(part)
        if i < len(jp_matches):
            rom = _convert_jp_segment(jp_matches[i])
            if not rom:
                continue

            left  = part
            right = non_jp_parts[i + 1] if i + 1 < len(non_jp_parts) else ''

            last_char  = left[-1]  if left  else ''
            first_char = right[0]  if right else ''

            # Añadir espacio solo si el caracter adyacente no es ya un separador
            sp_left  = '' if (not last_char  or last_char  in _SEPARATORS) else ' '
            sp_right = '' if (not first_char or first_char in _SEPARATORS) else ' '

            result_parts.append(sp_left + rom + sp_right)

    result = ''.join(result_parts)
    result = re.sub(r' {2,}', ' ', result)
    return result.strip()

def romanize_tags(filepath):
    try:
        # 1. MP3 (ID3)
        if filepath.lower().endswith('.mp3'):
            try:
                audio = EasyID3(filepath)
            except mutagen.id3.ID3NoHeaderError:
                audio = mutagen.File(filepath, easy=True)
                audio.add_tags()
            
            tags = ['title', 'artist', 'album', 'albumartist']
            for tag in tags:
                if tag in audio and audio[tag]:
                    audio[tag] = [to_romaji(audio[tag][0])]
            audio.save()
            
        # 2. FLAC u OGG
        elif filepath.lower().endswith(('.flac', '.ogg')):
            audio = mutagen.File(filepath)
            if audio is not None:
                for tag in ['title', 'artist', 'album', 'albumartist']:
                    if tag in audio and audio[tag]:
                        audio[tag] = [to_romaji(audio[tag][0])]
                audio.save()
                
        # 3. M4A / MP4 (AAC)
        elif filepath.lower().endswith(('.m4a', '.mp4')):
            audio = MP4(filepath)
            if audio is not None:
                # Mapeo de tags atómicos estándar de MP4
                mapping = {
                    '\xa9nam': 'title', 
                    '\xa9ART': 'artist', 
                    '\xa9alb': 'album', 
                    'aART': 'albumartist'
                }
                for key in mapping.keys():
                    if key in audio and audio[key]:
                        val = audio[key][0]
                        if isinstance(val, str):
                            audio[key] = [to_romaji(val)]
                audio.save()
    except Exception as e:
        print(f"Error en tags de {os.path.basename(filepath)}: {e}")

def scan_directory(root_dir):
    unromanized_subdirs = []
    
    try:
        subdirs = [os.path.join(root_dir, d) for d in os.listdir(root_dir) if os.path.isdir(os.path.join(root_dir, d))]
    except Exception as e:
        return {"success": False, "error": str(e)}

    # Si no hay subcarpetas directas, escanear el root_dir mismo
    if not subdirs:
        subdirs = [root_dir]

    for subdir in subdirs:
        has_japanese = False
        # Verificar si la subcarpeta en sí tiene japonés en el nombre
        if contains_japanese(os.path.basename(subdir)):
            has_japanese = True
        else:
            # Escanear recursivamente el interior de esta subcarpeta
            for dirpath, dirnames, filenames in os.walk(subdir):
                if any(contains_japanese(f) for f in filenames if os.path.splitext(f)[1].lower() in ['.mp3', '.flac', '.ogg', '.m4a', '.mp4']):
                    has_japanese = True
                    break
                if any(contains_japanese(d) for d in dirnames):
                    has_japanese = True
                    break
        
        if has_japanese:
            unromanized_subdirs.append(subdir)
            
    return {
        "success": True,
        "detected": len(unromanized_subdirs) > 0,
        "subdirs": unromanized_subdirs,
        "subdirs_count": len(unromanized_subdirs)
    }

def process_directory(root_dir):
    # Usamos topdown=False para renombrar de abajo hacia arriba.
    # Así evitamos que cambiar el nombre de una carpeta madre rompa la ruta de los archivos hijos.
    for dirpath, dirnames, filenames in os.walk(root_dir, topdown=False):
        
        # 1. Romanizar tags y renombrar archivos individuales
        for filename in filenames:
            ext = os.path.splitext(filename)[1].lower()
            if ext in ['.mp3', '.flac', '.ogg', '.m4a', '.mp4']:
                full_path = os.path.join(dirpath, filename)
                
                # Modificar metadatos internos
                romanize_tags(full_path)
                
                # Cambiar nombre físico del archivo
                name, file_ext = os.path.splitext(filename)
                new_name = to_romaji(name)
                new_filename = new_name + file_ext
                if new_filename != filename:
                    new_path = os.path.join(dirpath, new_filename)
                    os.rename(full_path, new_path)
                    print(f"[Archivo] Renombrado: {filename} -> {new_filename}")
                    
        # 2. Romanizar nombres de carpetas
        for dirname in dirnames:
            full_dir_path = os.path.join(dirpath, dirname)
            new_dirname = to_romaji(dirname)
            if new_dirname != dirname:
                new_dir_path = os.path.join(dirpath, new_dirname)
                os.rename(full_dir_path, new_dir_path)
                print(f"[Carpeta] Renombrada: {dirname} -> {new_dirname}")

    # 3. Romanizar la propia carpeta raíz si es necesario
    parent_dir = os.path.dirname(root_dir)
    base_name = os.path.basename(root_dir)
    if base_name:
        new_base_name = to_romaji(base_name)
        if new_base_name != base_name:
            new_root_path = os.path.join(parent_dir, new_base_name)
            try:
                os.rename(root_dir, new_root_path)
                print(f"[Carpeta] Renombrada raíz: {base_name} -> {new_base_name}")
            except Exception as e:
                print(f"[Error] No se pudo renombrar la carpeta raíz {root_dir}: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(1)

    if sys.argv[1] == "--json-dict":
        if len(sys.argv) < 3:
            sys.stdout.buffer.write(b"{}")
            sys.exit(1)
        try:
            raw_input = sys.argv[2]
            input_dict = json.loads(raw_input)
            res_dict = {}
            for k, v in input_dict.items():
                if isinstance(v, str):
                    res_dict[k] = to_romaji(v)
                else:
                    res_dict[k] = v
            sys.stdout.buffer.write(json.dumps(res_dict, ensure_ascii=False).encode('utf-8'))
        except Exception as e:
            err_res = {"error": str(e)}
            sys.stdout.buffer.write(json.dumps(err_res, ensure_ascii=False).encode('utf-8'))
        sys.exit(0)

    if sys.argv[1] == "--scan":
        if len(sys.argv) < 3:
            print(json.dumps({"success": False, "error": "Ruta no especificada para escaneo"}))
            sys.exit(1)
        target_dir = sys.argv[2]
        if os.path.isdir(target_dir):
            result = scan_directory(target_dir)
            print(json.dumps(result))
        else:
            print(json.dumps({"success": False, "error": "La ruta especificada no es válida."}))
        sys.exit(0)

    target_dir = sys.argv[1]
    if os.path.isdir(target_dir):
        print(f"Iniciando romanización en: {target_dir}\n" + "-"*50)
        process_directory(target_dir)
        print("-"*50 + "\n¡Proceso completado con éxito!")
    else:
        print("La ruta especificada no es válida.")
