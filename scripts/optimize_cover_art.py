#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script de Automatización: Optimización y Sincronización de Carátulas Musicales
==============================================================================
Reglas:
- Dimensión máxima: 1200x1200px (manteniendo relación de aspecto).
- Peso máximo: <= 500 KB (512,000 bytes).
- Formato de salida: JPEG (~85% calidad óptima con compresión adaptativa).
- Si supera límites: optimiza imagen, guarda cover.jpg y reincrusta en pistas.
- Si cumple límites: NO toca pistas de audio; extrae/copia cover.jpg si no existía.
- Estados de log: [OPTIMIZADO], [EXTRAÍDO], [OMITIDO], [ERROR].
"""

import os
import sys
import io
import time
import base64
import argparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Tuple, List, Dict, Any

from PIL import Image, ImageOps
import mutagen
from mutagen.flac import FLAC, Picture
from mutagen.id3 import ID3, APIC, ID3NoHeaderError
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggvorbis import OggVorbis
from mutagen.oggopus import OggOpus
from mutagen.oggflac import OggFLAC
from mutagen.wave import WAVE
from mutagen.aiff import AIFF

# Configuración por defecto
DEFAULT_MAX_DIM = 1200
DEFAULT_MAX_KB = 500
DEFAULT_MAX_BYTES = DEFAULT_MAX_KB * 1024  # 512,000 bytes
DEFAULT_QUALITY = 85
AUDIO_EXTENSIONS = {'.flac', '.mp3', '.m4a', '.mp4', '.m4b', '.ogg', '.opus', '.oga', '.wav', '.aiff', '.aif'}
IMAGE_CANDIDATES = [
    'cover.jpg', 'cover.jpeg', 'folder.jpg', 'folder.jpeg',
    'front.jpg', 'front.jpeg', 'cover.png', 'folder.png', 'front.png',
    'album.jpg', 'album.png'
]

# Códigos ANSI para formato de consola
class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    RED = "\033[31m"
    GRAY = "\033[90m"

# Habilitar soporte UTF-8 y ANSI en Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    os.system("color")


class ImageOptimizer:
    """Gestiona la inspección, redimensión y compresión inteligente de imágenes."""

    @staticmethod
    def is_jpeg(data: bytes) -> bool:
        return data.startswith(b'\xff\xd8\xff')

    @staticmethod
    def inspect(image_data: bytes) -> Tuple[int, int, int]:
        """Devuelve (ancho, alto, peso_en_bytes)."""
        with Image.open(io.BytesIO(image_data)) as img:
            return img.width, img.height, len(image_data)

    @staticmethod
    def optimize(
        image_data: bytes,
        max_dim: int = DEFAULT_MAX_DIM,
        max_bytes: int = DEFAULT_MAX_BYTES,
        base_quality: int = DEFAULT_QUALITY
    ) -> Tuple[bytes, int, int, int]:
        """
        Redimensiona manteniendo relación de aspecto y comprime a JPEG <= max_bytes.
        Devuelve (bytes_optimizados, nuevo_ancho, nuevo_alto, nuevo_peso).
        """
        with Image.open(io.BytesIO(image_data)) as raw_img:
            # Respetar orientación EXIF
            img = ImageOps.exif_transpose(raw_img)

            # Normalizar modos de color a RGB para compatibilidad JPEG
            if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
                # Fondo blanco para imágenes con transparencia
                rgba_img = img.convert('RGBA')
                bg = Image.new('RGB', rgba_img.size, (255, 255, 255))
                bg.paste(rgba_img, mask=rgba_img.split()[3])
                img = bg
            elif img.mode != 'RGB':
                img = img.convert('RGB')

            # Redimensionar si supera el límite de dimensiones
            if img.width > max_dim or img.height > max_dim:
                img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

            # Compresión adaptativa por pasos de calidad y resolución
            target_img = img.copy()
            current_quality = base_quality
            
            while True:
                buf = io.BytesIO()
                target_img.save(
                    buf,
                    format='JPEG',
                    quality=current_quality,
                    optimize=True,
                    progressive=True
                )
                output_bytes = buf.getvalue()
                output_size = len(output_bytes)

                if output_size <= max_bytes:
                    return output_bytes, target_img.width, target_img.height, output_size

                # Si aún supera max_bytes, reducir calidad progresivamente
                if current_quality > 40:
                    current_quality -= 5
                elif current_quality > 25:
                    current_quality -= 3
                else:
                    # Si incluso en calidad baja supera max_bytes, reducir dimensiones 10%
                    new_w = max(int(target_img.width * 0.9), 300)
                    new_h = max(int(target_img.height * 0.9), 300)
                    if new_w == target_img.width and new_h == target_img.height:
                        # No se puede reducir más
                        return output_bytes, target_img.width, target_img.height, output_size
                    target_img = target_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                    current_quality = base_quality  # Reiniciar calidad con nueva dimensión


class AudioCoverManager:
    """Maneja la extracción e incrustación de carátulas en múltiples formatos de audio."""

    @staticmethod
    def extract_cover(file_path: str) -> Optional[Tuple[bytes, str]]:
        """Extrae la carátula incrustada devolviendo (bytes_imagen, mime_type) o None."""
        ext = os.path.splitext(file_path)[1].lower()
        try:
            if ext == '.flac':
                audio = FLAC(file_path)
                if audio.pictures:
                    # Preferir portada frontal (type 3)
                    for pic in audio.pictures:
                        if pic.type == 3:
                            return pic.data, pic.mime
                    return audio.pictures[0].data, audio.pictures[0].mime

            elif ext == '.mp3':
                try:
                    audio = ID3(file_path)
                except ID3NoHeaderError:
                    return None
                for key in audio.keys():
                    if key.startswith('APIC'):
                        apic = audio[key]
                        return apic.data, apic.mime

            elif ext in ('.m4a', '.mp4', '.m4b'):
                audio = MP4(file_path)
                if audio.tags and 'covr' in audio.tags and audio.tags['covr']:
                    covr = audio.tags['covr'][0]
                    mime = 'image/jpeg' if ImageOptimizer.is_jpeg(bytes(covr)) else 'image/png'
                    return bytes(covr), mime

            elif ext in ('.ogg', '.opus', '.oga'):
                audio = mutagen.File(file_path)
                if audio:
                    # VorbisComment METADATA_BLOCK_PICTURE
                    if 'metadata_block_picture' in audio:
                        for b64data in audio['metadata_block_picture']:
                            raw_block = base64.b64decode(b64data)
                            pic = Picture(raw_block)
                            return pic.data, pic.mime
                    # Legacy COVERART
                    if 'coverart' in audio:
                        raw_data = base64.b64decode(audio['coverart'][0])
                        mime = audio.get('coverartmime', ['image/jpeg'])[0]
                        return raw_data, mime

            elif ext in ('.wav', '.aiff', '.aif'):
                audio = mutagen.File(file_path)
                if audio and hasattr(audio, 'tags') and audio.tags:
                    for key in audio.tags.keys():
                        if key.startswith('APIC'):
                            apic = audio.tags[key]
                            return apic.data, apic.mime

        except Exception:
            return None

        return None

    @staticmethod
    def embed_cover(file_path: str, jpeg_bytes: bytes, dry_run: bool = False) -> bool:
        """Incrusta la carátula JPEG optimizada en el archivo de audio especificado."""
        if dry_run:
            return True

        ext = os.path.splitext(file_path)[1].lower()
        try:
            if ext == '.flac':
                audio = FLAC(file_path)
                pic = Picture()
                pic.type = 3  # Front Cover
                pic.mime = "image/jpeg"
                pic.desc = "Front Cover"
                pic.data = jpeg_bytes
                audio.clear_pictures()
                audio.add_picture(pic)
                audio.save()
                return True

            elif ext == '.mp3':
                try:
                    audio = MP3(file_path, ID3=ID3)
                    if audio.tags is None:
                        audio.add_tags()
                except ID3NoHeaderError:
                    audio = MP3(file_path)
                    audio.add_tags()
                audio.tags.delall('APIC')
                audio.tags.add(APIC(
                    encoding=3,  # UTF-8
                    mime='image/jpeg',
                    type=3,  # Front cover
                    desc='Cover',
                    data=jpeg_bytes
                ))
                audio.save()
                return True

            elif ext in ('.m4a', '.mp4', '.m4b'):
                audio = MP4(file_path)
                if audio.tags is None:
                    audio.add_tags()
                audio['covr'] = [MP4Cover(jpeg_bytes, imageformat=MP4Cover.FORMAT_JPEG)]
                audio.save()
                return True

            elif ext in ('.ogg', '.opus', '.oga'):
                audio = mutagen.File(file_path)
                if audio is not None:
                    pic = Picture()
                    pic.type = 3
                    pic.mime = "image/jpeg"
                    pic.desc = "Front Cover"
                    pic.data = jpeg_bytes
                    pic_b64 = base64.b64encode(pic.write()).decode('ascii')
                    audio['metadata_block_picture'] = [pic_b64]
                    if 'coverart' in audio:
                        del audio['coverart']
                    if 'coverartmime' in audio:
                        del audio['coverartmime']
                    audio.save()
                    return True

            elif ext in ('.wav', '.aiff', '.aif'):
                audio = mutagen.File(file_path)
                if audio and hasattr(audio, 'tags') and audio.tags is not None:
                    audio.tags.delall('APIC')
                    audio.tags.add(APIC(
                        encoding=3,
                        mime='image/jpeg',
                        type=3,
                        desc='Cover',
                        data=jpeg_bytes
                    ))
                    audio.save()
                    return True

        except Exception as e:
            raise RuntimeError(f"Error incrustando en {os.path.basename(file_path)}: {e}")

        return False


class AlbumProcessor:
    """Procesa una carpeta de álbum aplicando las reglas exactas del proyecto."""

    def __init__(
        self,
        max_dim: int = DEFAULT_MAX_DIM,
        max_bytes: int = DEFAULT_MAX_BYTES,
        quality: int = DEFAULT_QUALITY,
        dry_run: bool = False
    ):
        self.max_dim = max_dim
        self.max_bytes = max_bytes
        self.quality = quality
        self.dry_run = dry_run

    def process_folder(self, folder_path: str) -> Dict[str, Any]:
        """
        Ejecuta la evaluación y optimización en un directorio de álbum.
        Retorna diccionario con estado ('OPTIMIZADO', 'EXTRAÍDO', 'OMITIDO', 'ERROR') y detalles.
        """
        result = {
            'path': folder_path,
            'status': 'OMITIDO',
            'details': '',
            'tracks_count': 0,
            'error': None
        }

        try:
            # 1. Identificar pistas de audio en la carpeta
            with os.scandir(folder_path) as it:
                audio_files = [
                    entry.path for entry in it
                    if entry.is_file() and os.path.splitext(entry.name)[1].lower() in AUDIO_EXTENSIONS
                ]

            if not audio_files:
                result['details'] = 'No contiene archivos de audio'
                return result

            result['tracks_count'] = len(audio_files)

            # 2. Localizar carátula existente (disco y/o pistas)
            disk_image_data = None
            disk_source = None
            track_image_data = None
            track_source = None

            # 2.1 Buscar en disco
            for cand in IMAGE_CANDIDATES:
                cand_path = os.path.join(folder_path, cand)
                if os.path.exists(cand_path):
                    try:
                        with open(cand_path, 'rb') as f:
                            disk_image_data = f.read()
                        disk_source = f"archivo '{cand}'"
                        break
                    except Exception:
                        pass

            # 2.2 Buscar en pistas de audio
            for audio_path in audio_files:
                extracted = AudioCoverManager.extract_cover(audio_path)
                if extracted:
                    track_image_data, _ = extracted
                    track_source = f"pista '{os.path.basename(audio_path)}'"
                    break

            # 2.3 Si no se encontró ninguna carátula en ningún lugar
            if disk_image_data is None and track_image_data is None:
                result['status'] = 'OMITIDO'
                result['details'] = 'Sin carátula encontrada en disco ni en pistas'
                return result

            # 2.4 Evaluar dimensiones y peso de las fuentes disponibles
            disk_oversized = False
            disk_dim = (0, 0, 0)
            if disk_image_data is not None:
                try:
                    w, h, sz = ImageOptimizer.inspect(disk_image_data)
                    disk_dim = (w, h, sz)
                    disk_oversized = (w > self.max_dim or h > self.max_dim or sz > self.max_bytes)
                except Exception:
                    disk_image_data = None

            track_oversized = False
            track_dim = (0, 0, 0)
            if track_image_data is not None:
                try:
                    w, h, sz = ImageOptimizer.inspect(track_image_data)
                    track_dim = (w, h, sz)
                    track_oversized = (w > self.max_dim or h > self.max_dim or sz > self.max_bytes)
                except Exception:
                    track_image_data = None

            if disk_image_data is None and track_image_data is None:
                result['status'] = 'ERROR'
                result['details'] = 'Imágenes encontradas están corruptas o ilegibles'
                return result

            # Determinar si se requiere optimización (si cualquiera de las fuentes supera los límites)
            needs_optimization = disk_oversized or track_oversized

            # Seleccionar la mejor fuente para la imagen (mayor resolución / calidad)
            if disk_image_data and track_image_data:
                # Si ambas existen, elegir la de mayor resolución en píxeles
                if (disk_dim[0] * disk_dim[1]) >= (track_dim[0] * track_dim[1]):
                    source_data = disk_image_data
                    source_info = disk_source
                    source_dim = disk_dim
                else:
                    source_data = track_image_data
                    source_info = track_source
                    source_dim = track_dim
            elif disk_image_data:
                source_data = disk_image_data
                source_info = disk_source
                source_dim = disk_dim
            else:
                source_data = track_image_data
                source_info = track_source
                source_dim = track_dim

            width, height, size_bytes = source_dim
            size_kb = size_bytes / 1024.0
            cover_jpg_path = os.path.join(folder_path, 'cover.jpg')
            cover_jpg_exists = os.path.exists(cover_jpg_path)

            # 4. APLICAR REGLAS

            # CASO A: Supera límites -> OPTIMIZAR
            if needs_optimization:
                opt_bytes, new_w, new_h, new_size = ImageOptimizer.optimize(
                    source_data,
                    max_dim=self.max_dim,
                    max_bytes=self.max_bytes,
                    base_quality=self.quality
                )
                new_size_kb = new_size / 1024.0

                # Guardar cover.jpg optimizado
                if not self.dry_run:
                    with open(cover_jpg_path, 'wb') as f:
                        f.write(opt_bytes)

                # Reincrustar en todas las pistas de audio
                updated_tracks = 0
                for audio_path in audio_files:
                    try:
                        AudioCoverManager.embed_cover(audio_path, opt_bytes, dry_run=self.dry_run)
                        updated_tracks += 1
                    except Exception:
                        pass

                result['status'] = 'OPTIMIZADO'
                result['details'] = (
                    f"Fuente: {source_info} ({width}x{height}, {size_kb:.1f} KB) -> "
                    f"Optimizado a {new_w}x{new_h} ({new_size_kb:.1f} KB) | "
                    f"Pistas reincrustadas: {updated_tracks}/{len(audio_files)}"
                )
                return result

            # CASO B: Cumple límites -> NO TOCAR AUDIO, ASEGURAR cover.jpg
            else:
                if not cover_jpg_exists:
                    # Extraer/guardar cover.jpg sin recodificar si ya es JPEG
                    if not self.dry_run:
                        if ImageOptimizer.is_jpeg(source_data):
                            with open(cover_jpg_path, 'wb') as f:
                                f.write(source_data)
                        else:
                            # Si era PNG u otro formato, convertir a JPEG
                            opt_bytes, _, _, _ = ImageOptimizer.optimize(
                                source_data,
                                max_dim=self.max_dim,
                                max_bytes=self.max_bytes,
                                base_quality=self.quality
                            )
                            with open(cover_jpg_path, 'wb') as f:
                                f.write(opt_bytes)

                    result['status'] = 'EXTRAÍDO'
                    result['details'] = (
                        f"cover.jpg creado desde {source_info} "
                        f"({width}x{height}, {size_kb:.1f} KB) | Audio intacto"
                    )
                    return result
                else:
                    result['status'] = 'OMITIDO'
                    result['details'] = (
                        f"Cumple especificaciones ({width}x{height}, {size_kb:.1f} KB) "
                        f"y cover.jpg ya existe | Audio intacto"
                    )
                    return result

        except Exception as e:
            result['status'] = 'ERROR'
            result['details'] = f"Excepción general en carpeta: {e}"
            result['error'] = str(e)
            return result


def find_music_root() -> str:
    """Detecta automáticamente la ruta de la biblioteca musical."""
    candidates = [
        r'H:\media_data\music',
        r'/media/music',
        r'C:\media_data\music',
        r'D:\media_data\music',
        r'E:\media_data\music'
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return r'H:\media_data\music'


def print_banner(root_path: str, dry_run: bool, max_dim: int, max_kb: int, workers: int):
    mode_str = f"{Colors.YELLOW}[MODO SIMULACIÓN / DRY-RUN]{Colors.RESET}" if dry_run else f"{Colors.GREEN}[MODO REAL / ESCRITURA]{Colors.RESET}"
    print(f"\n{Colors.BOLD}{Colors.CYAN}{'='*80}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.CYAN}    OPTIMIZADOR Y SINCRONIZADOR DE CARÁTULAS MUSICALES{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.CYAN}{'='*80}{Colors.RESET}")
    print(f" {Colors.BOLD}Ruta base:{Colors.RESET}       {root_path}")
    print(f" {Colors.BOLD}Estado:{Colors.RESET}          {mode_str}")
    print(f" {Colors.BOLD}Límites:{Colors.RESET}         Máx {max_dim}x{max_dim}px | Máx {max_kb} KB | JPEG ~{DEFAULT_QUALITY}%")
    print(f" {Colors.BOLD}Hilos (Workers):{Colors.RESET} {workers}")
    print(f"{Colors.CYAN}{'-'*80}{Colors.RESET}\n")


def format_log(result: Dict[str, Any]) -> str:
    """Genera una línea de log clara y formateada para la consola."""
    status = result['status']
    path = result['path']
    details = result['details']

    if status == 'OPTIMIZADO':
        badge = f"{Colors.BOLD}{Colors.GREEN}[OPTIMIZADO]{Colors.RESET}"
    elif status == 'EXTRAÍDO':
        badge = f"{Colors.BOLD}{Colors.CYAN}[EXTRAÍDO]{Colors.RESET}"
    elif status == 'OMITIDO':
        badge = f"{Colors.GRAY}[OMITIDO]{Colors.RESET}"
    elif status == 'ERROR':
        badge = f"{Colors.BOLD}{Colors.RED}[ERROR]{Colors.RESET}"
    else:
        badge = f"[{status}]"

    return f"{badge} {path}\n       └─ {details}"


def main():
    parser = argparse.ArgumentParser(
        description="Optimiza carátulas (<=1200px, <=500KB) e incrusta en pistas de audio recursivamente."
    )
    parser.add_argument(
        '--path', '-p',
        type=str,
        default=None,
        help="Ruta raíz de la biblioteca musical (por defecto busca H:\\media_data\\music)"
    )
    parser.add_argument(
        '--dry-run', '-d',
        action='store_true',
        help="Simula todas las operaciones sin modificar ningún archivo"
    )
    parser.add_argument(
        '--max-dim',
        type=int,
        default=DEFAULT_MAX_DIM,
        help=f"Dimensión máxima en píxeles (default: {DEFAULT_MAX_DIM})"
    )
    parser.add_argument(
        '--max-kb',
        type=int,
        default=DEFAULT_MAX_KB,
        help=f"Peso máximo en KB (default: {DEFAULT_MAX_KB})"
    )
    parser.add_argument(
        '--quality', '-q',
        type=int,
        default=DEFAULT_QUALITY,
        help=f"Calidad inicial JPEG (default: {DEFAULT_QUALITY})"
    )
    parser.add_argument(
        '--workers', '-w',
        type=int,
        default=4,
        help="Número de hilos concurrentes para procesar carpetas (default: 4)"
    )
    parser.add_argument(
        '--subfolder', '-s',
        type=str,
        default=None,
        help="Subcarpeta específica dentro de la ruta base para procesar"
    )

    args = parser.parse_args()

    # Determinar ruta raíz
    root_path = args.path if args.path else find_music_root()
    if args.subfolder:
        root_path = os.path.join(root_path, args.subfolder)

    if not os.path.exists(root_path):
        print(f"{Colors.BOLD}{Colors.RED}[ERROR CRÍTICO]{Colors.RESET} La ruta especificada no existe: {root_path}")
        sys.exit(1)

    max_bytes = args.max_kb * 1024
    print_banner(root_path, args.dry_run, args.max_dim, args.max_kb, args.workers)

    processor = AlbumProcessor(
        max_dim=args.max_dim,
        max_bytes=max_bytes,
        quality=args.quality,
        dry_run=args.dry_run
    )

    start_time = time.time()
    stats = {
        'total_folders': 0,
        'album_folders': 0,
        'OPTIMIZADO': 0,
        'EXTRAÍDO': 0,
        'OMITIDO': 0,
        'ERROR': 0
    }

    # 1. Recolección de carpetas que contienen archivos de audio
    print(f"{Colors.BLUE}[*]{Colors.RESET} Escaneando estructura de carpetas en {root_path}...")
    candidate_folders = []
    
    for dirpath, _, filenames in os.walk(root_path):
        stats['total_folders'] += 1
        has_audio = any(os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS for f in filenames)
        if has_audio:
            candidate_folders.append(dirpath)

    stats['album_folders'] = len(candidate_folders)
    print(f"{Colors.GREEN}[✓]{Colors.RESET} Encontradas {len(candidate_folders)} carpetas de álbum para procesar.\n")

    if not candidate_folders:
        print(f"{Colors.YELLOW}[!] No se encontraron carpetas con archivos de audio compatibles.{Colors.RESET}")
        return

    # 2. Procesamiento concurrente o secuencial
    if args.workers > 1:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            future_to_folder = {
                executor.submit(processor.process_folder, folder): folder
                for folder in candidate_folders
            }
            for future in as_completed(future_to_folder):
                try:
                    res = future.result()
                    stats[res['status']] = stats.get(res['status'], 0) + 1
                    print(format_log(res))
                except Exception as exc:
                    folder = future_to_folder[future]
                    stats['ERROR'] += 1
                    print(f"{Colors.BOLD}{Colors.RED}[ERROR]{Colors.RESET} {folder}\n       └─ Error inesperado en hilo: {exc}")
    else:
        for folder in candidate_folders:
            res = processor.process_folder(folder)
            stats[res['status']] = stats.get(res['status'], 0) + 1
            print(format_log(res))

    # 3. Resumen final
    elapsed_time = time.time() - start_time
    print(f"\n{Colors.BOLD}{Colors.CYAN}{'='*80}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.CYAN}    RESUMEN DE EJECUCIÓN{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.CYAN}{'='*80}{Colors.RESET}")
    print(f" Tiempo total transcurrido:    {elapsed_time:.2f} segundos")
    print(f" Carpetas totales escaneadas: {stats['total_folders']}")
    print(f" Álbumes evaluados:           {stats['album_folders']}")
    print(f" {Colors.GREEN}● Optimizados [OPTIMIZADO]:{Colors.RESET}   {stats['OPTIMIZADO']}")
    print(f" {Colors.CYAN}● Extraídos   [EXTRAÍDO]:{Colors.RESET}     {stats['EXTRAÍDO']}")
    print(f" {Colors.GRAY}● Omitidos    [OMITIDO]:{Colors.RESET}      {stats['OMITIDO']}")
    print(f" {Colors.RED}● Errores     [ERROR]:{Colors.RESET}        {stats['ERROR']}")
    print(f"{Colors.CYAN}{'='*80}{Colors.RESET}\n")


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}[!] Ejecución interrumpida por el usuario.{Colors.RESET}")
        sys.exit(0)
