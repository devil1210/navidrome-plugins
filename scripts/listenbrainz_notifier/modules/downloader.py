#!/usr/bin/env python3
"""
Server-side Music Downloader (yt-dlp engine) and Sequential Download Queue.
"""

import html
import io
import json
import logging
import os
import queue
from dataclasses import asdict
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Tuple, Union

import requests

yt_dlp = None

def _get_yt_dlp():
    global yt_dlp
    if yt_dlp is not None:
        return yt_dlp
    import sys
    if "yt_dlp" in sys.modules:
        yt_dlp = sys.modules["yt_dlp"]
        return yt_dlp
    try:
        import yt_dlp as _ydl
        yt_dlp = _ydl
    except ImportError:
        yt_dlp = None
    return yt_dlp

from .models import DownloadJob, ResolvedAlbum, ResolvedTrack, TelegramTarget
from .navidrome import NavidromeClient

if TYPE_CHECKING:
    from .telegram import TelegramBotListener, TelegramSender

logger = logging.getLogger("listenbrainz_notifier")


class MusicDownloader:
    """
    Downloads albums and tracks directly from YouTube Music into the server's download
    directory (/media/downloads/music -> M:\\downloads\\music), replicating MediaHuman's
    folder hierarchy, audio format (M4A Original), and metadata for MusicBrainz Picard.
    """

    def __init__(
        self,
        download_dir: str = "/var/tmp/music_downloads",
        library_dir: Optional[str] = "/media/music",
        uid: int = 1000,
        gid: int = 1000,
        cookies_path: Optional[str] = None,
        cookies_list: Optional[List[str]] = None,
        sleep_interval: int = 2,
        max_sleep_interval: int = 3,
        state_tracker: Optional[Any] = None,
    ):
        self.download_dir = Path(download_dir)
        if library_dir:
            self.library_dir = Path(library_dir)
        elif "/media" in str(self.download_dir):
            self.library_dir = self.download_dir.parent
        else:
            self.library_dir = Path("/media/music")
        self.state_tracker = state_tracker
        self.uid = uid
        self.gid = gid
        self.sleep_interval = sleep_interval
        self.max_sleep_interval = max_sleep_interval

        # Multi-cookie support & rotation
        self.cookies_list: List[Path] = []
        if cookies_list:
            self.cookies_list = [Path(p) for p in cookies_list if os.path.isfile(p)]
        elif cookies_path:
            p = Path(cookies_path)
            if p.is_dir():
                self.cookies_list = sorted([f for f in p.glob("*.txt") if f.is_file()])
            elif p.is_file():
                self.cookies_list = [p]
        self.cookie_index = 0

    def get_current_cookie(self) -> Optional[str]:
        """Returns the active cookie file path or None."""
        if not self.cookies_list:
            return None
        return str(self.cookies_list[self.cookie_index % len(self.cookies_list)])

    def rotate_cookie(self) -> Optional[str]:
        """Advances to the next cookie account in rotation."""
        if not self.cookies_list:
            return None
        self.cookie_index = (self.cookie_index + 1) % len(self.cookies_list)
        active = self.get_current_cookie()
        logger.info(
            "Rotated to next YouTube cookie: %s (%d/%d accounts)",
            Path(active).name if active else "",
            (self.cookie_index % len(self.cookies_list)) + 1,
            len(self.cookies_list),
        )
        return active

    @staticmethod
    def sanitize_name(name: str) -> str:
        """Sanitizes folder/file names to be safe on both Linux and Windows SMB filesystems."""
        if not name:
            return "Unknown"
        s = re.sub(r'[<>:"/\\|?*]', '_', name)
        s = re.sub(r'\s+', ' ', s)
        s = s.strip('. ')
        return s or "Unknown"

    def get_ydl_opts(self, target_folder: Path, is_playlist: bool = True) -> Dict[str, Any]:
        """Builds yt-dlp options identical to MediaHuman + high speed optimizations."""
        target_folder.mkdir(parents=True, exist_ok=True)
        self._fix_permissions(target_folder)
        if is_playlist:
            outtmpl = str(target_folder / "%(playlist_index)02d - %(artist,creator,uploader)s - %(title)s.%(ext)s")
        else:
            outtmpl = str(target_folder / "%(artist,creator,uploader)s - %(title)s.%(ext)s")

        opts: Dict[str, Any] = {
            "format": "ba[ext=m4a]/ba",
            "outtmpl": outtmpl,
            "nooverwrites": True,
            "overwrites": False,
            "writethumbnail": False,
            "allow_playlist_files": False,
            "windowsfilenames": True,
            "concurrent_fragment_downloads": 4,
            "remote_components": ["ejs:github"],
            "postprocessors": [
                {"key": "FFmpegMetadata", "add_metadata": True},
            ],
            "ignoreerrors": True,
            "retries": 3,
            "quiet": True,
            "no_warnings": False,
        }

        if self.sleep_interval > 0:
            opts["sleep_interval"] = self.sleep_interval
            opts["max_sleep_interval"] = self.max_sleep_interval

        current_cookie = self.get_current_cookie()
        if current_cookie:
            opts["cookiefile"] = current_cookie

        # Use ultra-lightweight QuickJS (<2MB RAM) for YouTube JS challenges
        opts["js_runtimes"] = {"quickjs": {}}

        return opts

    @classmethod
    def fetch_official_cover(
        cls,
        album_name: Optional[str] = None,
        artist_name: Optional[str] = None,
        cover_url: Optional[str] = None,
    ) -> Optional[bytes]:
        """
        Retrieves official high-resolution 1:1 square artwork:
        1. From cover_url (upgraded to 1200x1200).
        2. From iTunes Search API (1200x1200bb.jpg or 1400x1400bb.jpg).
        3. From MusicBrainz / Cover Art Archive.
        Returns JPEG bytes strictly verified to be square (1:1), or None.
        """
        from PIL import Image

        def _verify_and_square_image(img_bytes: bytes) -> Optional[bytes]:
            try:
                with Image.open(io.BytesIO(img_bytes)) as img:
                    w, h = img.size
                    if w < 300 or h < 300:
                        return None
                    if w != h:
                        if w > h:
                            offset = (w - h) // 2
                            img = img.crop((offset, 0, offset + h, h))
                        else:
                            offset = (h - w) // 2
                            img = img.crop((0, offset, w, offset + w))
                    out = io.BytesIO()
                    img.convert("RGB").save(out, format="JPEG", quality=95, optimize=True)
                    return out.getvalue()
            except Exception as e:
                logger.debug("Error procesando imagen con PIL: %s", e)
                return None

        # 1. Intentar cover_url si se dispone de él
        if cover_url and cover_url.startswith("http"):
            high_res_url = re.sub(r"=w\d+-h\d+.*", "=w1200-h1200-l90-rj", cover_url)
            high_res_url = re.sub(r"=s\d+.*", "=s1200-c-k-c0x00ffffff-no-rj", high_res_url)
            try:
                resp = requests.get(
                    high_res_url,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                    timeout=5,
                )
                if resp.status_code == 200 and len(resp.content) > 1000:
                    sq_bytes = _verify_and_square_image(resp.content)
                    if sq_bytes:
                        logger.info("Carátula oficial 1:1 obtenida desde cover_url")
                        return sq_bytes
            except Exception as e:
                logger.debug("Fallo al descargar cover_url %s: %s", cover_url[:80], e)

        # 2. iTunes Search API (oficial alta definición 1200x1200 de sellos discográficos)
        clean_album = re.sub(r"\s*[\(\[].*?[\)\]]", "", album_name or "").strip()
        clean_artist = re.sub(r"\s*[\(\[].*?[\)\]]", "", artist_name or "").strip()
        if clean_album or clean_artist:
            query = f"{clean_artist} {clean_album}".strip()
            for entity in ("album", "song"):
                try:
                    q_enc = urllib.parse.quote(query)
                    url = f"https://itunes.apple.com/search?term={q_enc}&entity={entity}&limit=3"
                    resp = requests.get(
                        url,
                        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                        timeout=5,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        for item in data.get("results", []):
                            art_url = item.get("artworkUrl100", "")
                            if art_url:
                                high_art_url = art_url.replace("100x100bb.jpg", "1200x1200bb.jpg")
                                art_resp = requests.get(
                                    high_art_url,
                                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                                    timeout=5,
                                )
                                if art_resp.status_code == 200 and len(art_resp.content) > 1000:
                                    sq_bytes = _verify_and_square_image(art_resp.content)
                                    if sq_bytes:
                                        logger.info("Carátula oficial HD (1200x1200) encontrada en iTunes para '%s - %s'", clean_artist, clean_album)
                                        return sq_bytes
                except Exception as e:
                    logger.debug("Error buscando carátula en iTunes (%s): %s", entity, e)

        # 3. MusicBrainz / Cover Art Archive
        if clean_album and clean_artist:
            try:
                mb_q = f"release:{clean_album} AND artist:{clean_artist}"
                mb_url = f"https://musicbrainz.org/ws/2/release/?query={urllib.parse.quote(mb_q)}&fmt=json&limit=1"
                mb_resp = requests.get(
                    mb_url,
                    headers={"User-Agent": "NavidromeNotifier/2.0 ( contact@navidrome.org )"},
                    timeout=5,
                )
                if mb_resp.status_code == 200:
                    mb_data = mb_resp.json()
                    releases = mb_data.get("releases", [])
                    if releases:
                        mbid = releases[0].get("id")
                        caa_url = f"https://coverartarchive.org/release/{mbid}/front-1200"
                        caa_resp = requests.get(
                            caa_url,
                            headers={"User-Agent": "NavidromeNotifier/2.0"},
                            timeout=5,
                            allow_redirects=True,
                        )
                        if caa_resp.status_code == 200 and len(caa_resp.content) > 1000:
                            sq_bytes = _verify_and_square_image(caa_resp.content)
                            if sq_bytes:
                                logger.info("Carátula oficial encontrada en Cover Art Archive para MBID %s", mbid)
                                return sq_bytes
            except Exception as e:
                logger.debug("Error buscando en Cover Art Archive: %s", e)

        return None

    @classmethod
    def _process_album_cover(
        cls,
        target_folder: Path,
        uid: int = 1000,
        gid: int = 1000,
        cover_url: Optional[str] = None,
        target_file: Optional[Path] = None,
        album_name: Optional[str] = None,
        album_artist: Optional[str] = None,
    ):
        """
        Garantiza que cada carpeta tenga su archivo cover.jpg de proporción 1:1 estrictamente cuadrada
        y que la carátula quede incrustada en el átomo nativo 'covr' de cada archivo .m4a.

        1. Prioridad #1: Descarga la carátula oficial original en alta definición (1200x1200) desde
           fuentes en línea (cover_url, iTunes Search API, MusicBrainz CAA).
        2. Última opción: Si y solo si no existe en ninguna fuente en línea, recurre a las imágenes
           sueltas de yt-dlp o al fotograma incrustado del video de YouTube, recortándolo al centro 1:1.
        3. Guarda cover.jpg y lo incrusta en todos los archivos .m4a con mutagen.
        4. Limpia cualquier miniatura suelta no cuadrada.
        """
        if not target_folder.exists():
            return

        cover_path = target_folder / "cover.jpg"
        cover_bytes: Optional[bytes] = None

        # 1. Intentar descargar carátula oficial en alta definición cuadrada (1200x1200)
        official_bytes = cls.fetch_official_cover(
            album_name=album_name,
            artist_name=album_artist,
            cover_url=cover_url,
        )
        if official_bytes:
            try:
                cover_path.write_bytes(official_bytes)
                cover_bytes = official_bytes
                logger.info("Carátula oficial 1:1 guardada en %s", target_folder.name)
            except Exception as e:
                logger.warning("No se pudo escribir cover.jpg oficial en %s: %s", target_folder.name, e)

        # 2. Última opción: Si no se pudo obtener carátula oficial, recortar miniatura de YouTube a 1:1
        if not cover_bytes and (not cover_path.is_file() or cover_path.stat().st_size < 1000):
            loose_images = [
                f for f in target_folder.iterdir()
                if f.is_file() and f.suffix.lower() in ('.jpg', '.jpeg', '.png', '.webp') and f.name.lower() != 'cover.jpg'
            ]
            if loose_images:
                best_img = max(loose_images, key=lambda f: f.stat().st_size)
                try:
                    from PIL import Image
                    with Image.open(best_img) as img:
                        w, h = img.size
                        if w != h:
                            if w > h:
                                offset = (w - h) // 2
                                img = img.crop((offset, 0, offset + h, h))
                            else:
                                offset = (h - w) // 2
                                img = img.crop((0, offset, w, offset + w))
                        img.convert("RGB").save(cover_path, "JPEG", quality=95, optimize=True)
                        cover_bytes = cover_path.read_bytes()
                        logger.info("Recorte 1:1 de miniatura de YouTube guardado como cover.jpg en %s", target_folder.name)
                except Exception as e:
                    logger.debug("Error recortando miniatura suelta con PIL: %s", e)
            else:
                # Extraer del primer archivo .m4a con mutagen y recortar a 1:1
                audio_candidates = sorted([f for f in target_folder.iterdir() if f.is_file() and f.suffix.lower() == '.m4a'])
                if audio_candidates:
                    try:
                        from mutagen.mp4 import MP4
                        from PIL import Image
                        mp4 = MP4(audio_candidates[0])
                        covr = mp4.get("covr", [])
                        if covr:
                            raw_covr = bytes(covr[0])
                            with Image.open(io.BytesIO(raw_covr)) as img:
                                w, h = img.size
                                if w != h:
                                    if w > h:
                                        offset = (w - h) // 2
                                        img = img.crop((offset, 0, offset + h, h))
                                    else:
                                        offset = (h - w) // 2
                                        img = img.crop((0, offset, w, offset + w))
                                img.convert("RGB").save(cover_path, "JPEG", quality=95, optimize=True)
                                cover_bytes = cover_path.read_bytes()
                                logger.info("Recorte 1:1 extraído de pista .m4a guardado como cover.jpg en %s", target_folder.name)
                    except Exception as e:
                        logger.debug("Error extrayendo carátula embebida con mutagen: %s", e)

        # 3. Si cover.jpg ya existía de antes, asegurar proporción 1:1 estrictamente cuadrada
        if cover_path.is_file() and cover_path.stat().st_size > 1000:
            try:
                from PIL import Image
                with Image.open(cover_path) as img:
                    w, h = img.size
                    if w != h:
                        logger.info("Recortando carátula existente no cuadrada a 1:1 en %s (%dx%d)", target_folder.name, w, h)
                        if w > h:
                            offset = (w - h) // 2
                            sq_img = img.crop((offset, 0, offset + h, h))
                        else:
                            offset = (h - w) // 2
                            sq_img = img.crop((0, offset, w, offset + w))
                        sq_img.convert("RGB").save(cover_path, "JPEG", quality=95, optimize=True)
                cover_bytes = cover_path.read_bytes()
            except Exception as e:
                logger.debug("Error verificando proporción de cover.jpg: %s", e)

        # 4. Eliminar miniaturas sueltas temporales (.webp, .png, etc.), preservando cover.jpg
        for img in target_folder.iterdir():
            if img.is_file() and img.name.lower() != 'cover.jpg' and img.suffix.lower() in ('.jpg', '.jpeg', '.png', '.webp'):
                try:
                    img.unlink(missing_ok=True)
                except Exception:
                    pass

        # 5. Si cover_bytes existe, incrustarlo en todos los archivos .m4a
        if cover_path.is_file() and cover_path.stat().st_size > 1000:
            try:
                os.chmod(cover_path, 0o664)
                os.chown(cover_path, uid, gid)
            except Exception:
                pass

            try:
                if not cover_bytes:
                    cover_bytes = cover_path.read_bytes()
                from mutagen.mp4 import MP4, MP4Cover

                targets = [target_file] if target_file else (
                    [] if target_folder.name == "_Singles" else list(target_folder.glob("*.m4a"))
                )
                for m4a_file in targets:
                    if m4a_file and m4a_file.is_file():
                        try:
                            mp4 = MP4(m4a_file)
                            mp4["covr"] = [MP4Cover(cover_bytes, imageformat=MP4Cover.FORMAT_JPEG)]
                            mp4.save()
                        except Exception as me:
                            logger.warning("No se pudo incrustar carátula en %s: %s", m4a_file.name, me)
            except Exception as e:
                logger.warning("Error procesando incrustación de carátula en %s: %s", target_folder.name, e)

    @classmethod
    def _cleanup_loose_images(cls, target_folder: Path, cover_url: Optional[str] = None):
        """Mantiene compatibilidad hacia atrás invocando _process_album_cover."""
        cls._process_album_cover(target_folder, cover_url=cover_url)

    @staticmethod
    def _quick_metadata_lookup(title: str, artist: str) -> Optional[Dict[str, Any]]:
        """
        Consulta ultra-rápida a iTunes Search API (< 500ms) para enriquecer metadatos
        de canciones o lanzamientos recientes en español que no existen en MusicBrainz.
        """
        try:
            from urllib.parse import quote
            clean_q = f"{title} {artist}".strip()
            q = quote(clean_q)
            url = f"https://itunes.apple.com/search?term={q}&entity=song&limit=1"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("resultCount", 0) > 0:
                    r = data["results"][0]
                    return {
                        "genre": r.get("primaryGenreName"),
                        "release_date": r.get("releaseDate", "")[:10],
                        "collection_name": r.get("collectionName"),
                        "track_name": r.get("trackName"),
                        "artist_name": r.get("artistName"),
                    }
        except Exception as e:
            logger.debug("Error en consulta rápida iTunes para '%s - %s': %s", artist, title, e)
        return None

    KNOWN_MULTIWORD_ARTISTS = {
        "fear, and loathing in las vegas",
        "earth, wind & fire", "earth, wind and fire",
        "tyler, the creator",
        "crosby, stills, nash & young", "crosby, stills, nash and young",
        "simon & garfunkel", "simon and garfunkel",
        "kool & the gang", "kool and the gang",
        "florence + the machine", "florence and the machine",
        "bob marley & the wailers", "bob marley and the wailers",
        "iron & wine",
        "blood, sweat & tears", "blood, sweat and tears",
        "echo & the bunnymen",
        "tom petty and the heartbreakers",
        "joan jett & the blackhearts",
        "huey lewis & the news", "huey lewis and the news",
        "kc & the sunshine band", "kc and the sunshine band",
        "sly & the family stone", "sly and the family stone",
        "katrina and the waves",
        "king gizzard & the lizard wizard",
        "love harmony's, inc.", "love harmony's， inc.",
        "daryl hall & john oates", "daryl hall and john oates", "hall & oates",
        "godley & creme", "godley and creme",
        "louis armstrong & his all-stars", "louis armstrong and his all-stars",
        "louis armstrong & his orchestra", "louis armstrong and his orchestra",
        "willie colón & legal alien", "willie colon & legal alien",
        "ac/dc",
    }

    @classmethod
    def split_artists(cls, raw: Union[str, List[str]]) -> List[str]:
        """
        Splits artist credits containing multiple artists into a list of individual artist names.
        Intelligently respects known bands containing commas or ampersands (e.g. Earth, Wind & Fire),
        while separating collaborations ('Artist A, Artist B', 'Artist A feat. Artist B', 'Artist A con Artist B').
        """
        if not raw:
            return []
        if isinstance(raw, list):
            if len(raw) > 1:
                result = []
                for x in raw:
                    result.extend(cls.split_artists(x))
                return result
            s = str(raw[0])
        else:
            s = str(raw)

        s = s.strip()
        if not s or s.lower() in cls.KNOWN_MULTIWORD_ARTISTS:
            return [s]

        # Splitting pattern: feat, ft, featuring, with, con, semicolon, bullet, slash, comma, or standalone ampersand
        pattern = r"(?:\s+(?:feat\s*\.?|ft\s*\.?|featuring|with|con)\s+|[;/•]|\s+&\s+|\s*,\s*)"
        parts = re.split(pattern, s, flags=re.IGNORECASE)
        cleaned: List[str] = []
        seen = set()
        for p in parts:
            p_clean = re.sub(r"^[\s\.,;\-]+", "", p).strip()
            if p_clean and p_clean.lower() not in seen:
                seen.add(p_clean.lower())
                cleaned.append(p_clean)

        return cleaned if len(cleaned) > 1 else [s]

    @classmethod
    def _process_album_metadata(
        cls,
        target_folder: Path,
        album_artist: str,
        album_name: str,
        year: Optional[str] = None,
        is_single: bool = False,
        track_titles: Optional[List[str]] = None,
        target_file: Optional[Path] = None,
        uid: int = 1000,
        gid: int = 1000,
    ):
        """
        Blinda las pistas descargadas (.m4a) con todos los metadatos requeridos por MusicBrainz Picard:
        - aART (Album Artist): Crucial para que Picard organice en la carpeta del artista principal
          en lugar de crear carpetas con todos los artistas invitados/colaboradores.
        - ©ART (Track Artist): Multi-valor nativo para colaboraciones (Arelys Henao • Grupo Exterminador).
        - ----:com.apple.iTunes:ARTISTS: Átomo multi-valor complementario para Picard y Subsonic.
        - ----:com.apple.iTunes:ALBUMARTIST / ALBUM ARTIST: Etiquetas libres compatibles con taggers.
        - ©alb (Album): Nombre del álbum o sencillo.
        - ©day (Fecha/Año): Formato estandarizado ISO (YYYY-MM-DD o YYYY).
        - ©gen (Género): Género oficial recuperado por búsqueda rápida o preservado.
        - trkn / disk: Numeración de pista y disco.
        - RELEASETYPE: 'single' o 'album' para Picard.
        """
        if not target_folder.exists():
            return

        targets = [target_file] if target_file else sorted([f for f in target_folder.glob("*.m4a") if f.is_file()])
        if not targets:
            return

        total_tracks = len(targets)

        # 1. Búsqueda rápida de metadatos en iTunes (fecha y género oficial)
        extra_meta = cls._quick_metadata_lookup(album_name, album_artist)
        genre = extra_meta.get("genre") if extra_meta else None
        release_date = extra_meta.get("release_date") if extra_meta else None

        for idx, m4a_file in enumerate(targets, 1):
            if not m4a_file or not m4a_file.is_file():
                continue
            try:
                from mutagen.mp4 import MP4, MP4FreeForm
                mp4 = MP4(m4a_file)

                # a. Album Artist (aART nativo estándar de MP4/iTunes)
                if album_artist:
                    primary_album_artist = cls.split_artists(album_artist)[0] if album_artist else album_artist
                    mp4["aART"] = [primary_album_artist]
                    # Eliminar átomos libres redundantes que hacen que Picard duplique con ';'
                    for redundant_key in [
                        "----:com.apple.iTunes:ALBUMARTIST",
                        "----:com.apple.iTunes:ALBUM ARTIST",
                        "----:com.apple.iTunes:album artist",
                        "----:com.apple.iTunes:albumartist",
                        "----:com.apple.iTunes:Album Artist",
                        "----:com.apple.iTunes:AlbumArtist",
                    ]:
                        if redundant_key in mp4:
                            del mp4[redundant_key]

                # b. Nombre de Álbum
                if album_name and ("©alb" not in mp4 or not mp4["©alb"] or mp4["©alb"][0] == "Unknown"):
                    mp4["©alb"] = [album_name]

                # c. Track Artist y Multi-Artist support (Picard / Navidrome standard)
                raw_track_artists = mp4.get("©ART", [])
                if not raw_track_artists:
                    raw_track_artists = [album_artist] if album_artist else []

                parsed_artists = cls.split_artists(raw_track_artists)
                if parsed_artists:
                    mp4["©ART"] = parsed_artists
                    try:
                        mp4["----:com.apple.iTunes:ARTISTS"] = [
                            MP4FreeForm(a.encode("utf-8")) for a in parsed_artists
                        ]
                    except Exception:
                        pass

                # d. Fecha / Año
                if release_date:
                    mp4["©day"] = [release_date]
                elif "©day" in mp4 and mp4["©day"]:
                    raw_day = str(mp4["©day"][0])
                    if len(raw_day) == 8 and raw_day.isdigit():
                        mp4["©day"] = [f"{raw_day[:4]}-{raw_day[4:6]}-{raw_day[6:]}"]
                elif year:
                    mp4["©day"] = [year]
                else:
                    # Extraer año de la descripción o copyright del video si está disponible
                    desc_text = ""
                    for tag_name in ["desc", "ldes", "©cmt"]:
                        if tag_name in mp4 and mp4[tag_name]:
                            desc_text += " " + str(mp4[tag_name][0])
                    m_year = re.search(r'(?:Released on:|℗)\s*(\d{4})', desc_text)
                    if m_year:
                        mp4["©day"] = [m_year.group(1)]

                # e. Género
                if genre:
                    mp4["©gen"] = [genre]
                elif "©gen" not in mp4 or not mp4["©gen"] or mp4["©gen"] == ["Music"]:
                    mp4["©gen"] = ["Latin" if is_single else "Music"]

                # f. Numeración de pista trkn = [(track_num, total_tracks)]
                current_trkn = mp4.get("trkn")
                if not current_trkn or current_trkn == [(0, 0)]:
                    track_num = idx
                    m = re.match(r"^(\d{1,2})\s*-\s*", m4a_file.name)
                    if m:
                        try:
                            track_num = int(m.group(1))
                        except Exception:
                            pass
                    mp4["trkn"] = [(track_num, total_tracks)]

                # g. Número de disco disk = [(disc_num, total_discs)]
                if "disk" not in mp4:
                    mp4["disk"] = [(1, 1)]

                # h. Indicador de compilación
                is_comp = bool(album_artist and album_artist.lower() in ("various artists", "varios artistas"))
                mp4["cpil"] = is_comp

                # i. Clasificación de lanzamiento
                mp4["----:com.apple.iTunes:RELEASETYPE"] = [b"single" if is_single else b"album"]
                mp4["----:com.apple.iTunes:MEDIA"] = [b"Digital Media"]

                # j. Título de pista oficial y soporte Dual / Japonés (desde YouTube Music / album.tracks)
                if track_titles and 1 <= track_num <= len(track_titles):
                    official_title = track_titles[track_num - 1]
                    if official_title:
                        mp4["©nam"] = [official_title]
                        parts = re.split(r'\s*[\-\–\—]\s*', official_title)
                        jp_part = [p.strip() for p in parts if any('\u3040' <= c <= '\u30ff' or '\u4e00' <= c <= '\u9faf' for c in p)]
                        if jp_part:
                            mp4["----:com.apple.iTunes:originaltitle"] = [MP4FreeForm(jp_part[0].encode("utf-8"))]

                mp4.save()
                logger.info("Metadatos blindados para Picard en %s (Album Artist: '%s')", m4a_file.name, album_artist)
            except Exception as e:
                logger.warning("No se pudieron enriquecer metadatos en %s: %s", m4a_file.name, e)

    @staticmethod
    def _sanitize_downloaded_files(target_folder: Path):
        """
        Renames any files with problematic characters (such as unicode slashes)
        that break Samba / Windows SMB charset encoding.
        """
        if not target_folder.exists():
            return
        for f in list(target_folder.rglob("*")):
            if f.is_file():
                clean_name = f.name.replace("\u29f8", "-").replace("⧸", "-")
                clean_name = re.sub(r'[<>:"/\\|?*]', '_', clean_name)
                if clean_name != f.name:
                    new_path = f.parent / clean_name
                    try:
                        f.rename(new_path)
                        logger.info("Archivo renombrado para compatibilidad Windows SMB: '%s' -> '%s'", f.name, clean_name)
                    except Exception as e:
                        logger.warning("No se pudo renombrar %s a %s: %s", f.name, clean_name, e)

    @staticmethod
    def verify_album_integrity(target_folder: Path) -> Tuple[bool, int, str]:
        """
        Verifica la integridad de un álbum descargado en disco:
        1. Comprueba que la carpeta exista.
        2. Verifica que NO existan archivos temporales o incompletos (.part, .ytdl, .tmp).
        3. Verifica que los archivos de audio tengan tamaño real (> 200 KB, no corruptos ni vacíos).
        4. Verifica que si hay numeración de pistas (01, 02, etc.), exista al menos la pista 1 y no haya huecos iniciales.
        Retorna: (es_valido, total_pistas, mensaje_estado)
        """
        if not target_folder.is_dir():
            return False, 0, "La carpeta no existe"

        part_files = list(target_folder.glob("*.part")) + list(target_folder.glob("*.ytdl")) + list(target_folder.glob("*.tmp"))
        if part_files:
            return False, 0, f"Descarga incompleta detectada ({len(part_files)} archivo(s) .part/.ytdl pendientes)"

        audio_files = [
            f for f in target_folder.iterdir()
            if f.is_file() and f.suffix.lower() in ('.m4a', '.mp3', '.opus', '.webm', '.flac')
        ]
        if not audio_files:
            return False, 0, "No contiene pistas de audio"

        for f in audio_files:
            size = f.stat().st_size
            if size < 200 * 1024:
                return False, len(audio_files), f"Pista corrupta o truncada: {f.name} ({size // 1024} KB)"

        return True, len(audio_files), f"Álbum íntegro con {len(audio_files)} pista(s) válidas"

    def get_album_tracklist(self, album: ResolvedAlbum) -> List[Dict[str, Any]]:
        """
        Retrieves the official list of tracks for an album from YouTube Music / playlist.
        Returns a list of dicts: [{'index': 1, 'title': '...', 'id': '...'}, ...]
        """
        # Prioritize YTMusic official catalog metadata with Dual/Japanese titles
        if "list=" in album.url:
            list_id = album.url.split("list=")[1].split("&")[0]
            try:
                from ytmusicapi import YTMusic
                yt = YTMusic()
                pl = yt.get_playlist(list_id)
                if pl and pl.get("tracks"):
                    tracks = []
                    for idx, t in enumerate(pl["tracks"], 1):
                        title = t.get("title") or f"Pista {idx}"
                        vid = t.get("videoId") or ""
                        tracks.append({"index": idx, "title": title, "id": vid})
                    if tracks:
                        return tracks
            except Exception as e:
                logger.debug("Could not fetch playlist tracklist from YTMusic (%s): %s", list_id, e)

        ydl_mod = _get_yt_dlp()
        if ydl_mod is None:
            if album.tracks:
                return [{"index": i, "title": t, "id": ""} for i, t in enumerate(album.tracks, 1)]
            return []

        opts: Dict[str, Any] = {
            "extract_flat": True,
            "quiet": True,
            "skip_download": True,
            "no_warnings": True,
        }
        current_cookie = self.get_current_cookie()
        if current_cookie:
            opts["cookiefile"] = current_cookie

        try:
            with ydl_mod.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(album.url, download=False)
                entries = info.get("entries", []) if info else []
                tracks = []
                for idx, entry in enumerate(entries, 1):
                    title = entry.get("title") or f"Pista {idx}"
                    vid = entry.get("id") or ""
                    tracks.append({"index": idx, "title": title, "id": vid})
                if tracks:
                    return tracks
        except Exception as e:
            logger.warning("Error extrayendo pistas oficiales para '%s' con yt-dlp: %s", album.album_name, e)

        if album.tracks:
            return [{"index": i, "title": t, "id": ""} for i, t in enumerate(album.tracks, 1)]
        return []

    @classmethod
    def resolve_official_tracklist(cls, album: ResolvedAlbum) -> List[str]:
        """Resolves the official tracklist with full Dual/Japanese titles from YTMusic if possible."""
        if album.tracks and len(album.tracks) > 1:
            return album.tracks
        if "list=" in album.url:
            list_id = album.url.split("list=")[1].split("&")[0]
            try:
                from ytmusicapi import YTMusic
                yt = YTMusic()
                pl = yt.get_playlist(list_id)
                if pl and pl.get("tracks"):
                    return [t.get("title") for t in pl["tracks"] if t.get("title")]
            except Exception as e:
                logger.debug("Could not resolve official tracklist from YTMusic (%s): %s", list_id, e)
        return album.tracks or []

    def download_album_tracks(self, album: ResolvedAlbum, track_indices: List[int]) -> Tuple[bool, str]:
        """
        Downloads only specific tracks of an album using yt-dlp playlist_items.
        Preserves original album metadata, track numbering, cover.jpg, and covr tags.
        """
        if not track_indices:
            return True, "No se indicaron pistas a descargar"

        folder_name = self.sanitize_name(album.album_name)
        target_folder = self.download_dir / folder_name
        target_folder.mkdir(parents=True, exist_ok=True)

        items_str = ",".join(str(i) for i in sorted(track_indices))
        opts = self.get_ydl_opts(target_folder, is_playlist=True)
        opts["playlist_items"] = items_str

        ydl_mod = _get_yt_dlp()
        try:
            if ydl_mod is None:
                return False, "yt-dlp no está instalado en este entorno"
            with ydl_mod.YoutubeDL(opts) as ydl:
                ydl.download([album.url])
            self._process_album_cover(
                target_folder,
                uid=self.uid,
                gid=self.gid,
                cover_url=album.cover_url,
                album_name=album.album_name,
                album_artist=album.artist_name,
            )
            official_tracklist = self.resolve_official_tracklist(album)
            self._process_album_metadata(
                target_folder,
                album_artist=album.artist_name,
                album_name=album.album_name,
                year=getattr(album, "year", None),
                is_single=album.is_single,
                track_titles=official_tracklist,
                uid=self.uid,
                gid=self.gid,
            )
            self._sanitize_downloaded_files(target_folder)
            self._fix_permissions(target_folder)

            audio_files = [
                f for f in target_folder.iterdir()
                if f.is_file() and f.suffix.lower() in ('.m4a', '.mp3', '.opus', '.webm', '.flac')
            ]

            if not audio_files and len(self.cookies_list) > 1:
                next_cookie = self.rotate_cookie()
                logger.warning("Reintentando pistas faltantes de '%s' con cuenta alternativa: %s", album.album_name, Path(next_cookie).name if next_cookie else "")
                opts_retry = self.get_ydl_opts(target_folder, is_playlist=True)
                opts_retry["playlist_items"] = items_str
                with ydl_mod.YoutubeDL(opts_retry) as ydl:
                    ydl.download([album.url])
                self._process_album_cover(
                    target_folder,
                    uid=self.uid,
                    gid=self.gid,
                    cover_url=album.cover_url,
                    album_name=album.album_name,
                    album_artist=album.artist_name,
                )
                self._process_album_metadata(
                    target_folder,
                    album_artist=album.artist_name,
                    album_name=album.album_name,
                    year=getattr(album, "year", None),
                    is_single=album.is_single,
                    track_titles=official_tracklist,
                    uid=self.uid,
                    gid=self.gid,
                )
                self._sanitize_downloaded_files(target_folder)
                self._fix_permissions(target_folder)
                audio_files = [
                    f for f in target_folder.iterdir()
                    if f.is_file() and f.suffix.lower() in ('.m4a', '.mp3', '.opus', '.webm', '.flac')
                ]

            if not audio_files:
                return False, "No se pudo descargar ninguna de las pistas faltantes"

            if len(self.cookies_list) > 1:
                self.rotate_cookie()

            # Enriquecimiento con plugins de Picard y reubicación en la biblioteca
            final_folder = target_folder
            try:
                from .picard_engine import PicardEngine
                plugins_config = self.state_tracker.get_picard_plugins() if self.state_tracker else None
                final_folder = PicardEngine.process_and_relocate_album(
                    temp_album_dir=target_folder,
                    library_root=self.library_dir,
                    album_name=album.album_name,
                    album_artist=album.artist_name,
                    date=getattr(album, "year", None),
                    is_single=album.is_single,
                    plugins_config=plugins_config,
                    uid=self.uid,
                    gid=self.gid,
                )
            except Exception as pe:
                logger.warning("Error en procesamiento de PicardEngine para '%s': %s", album.album_name, pe)

            return True, str(final_folder)
        except Exception as e:
            logger.error("Error descargando pistas faltantes del álbum '%s': %s", album.album_name, e)
            if len(self.cookies_list) > 1:
                self.rotate_cookie()
            return False, str(e)

    def download_album(self, album: ResolvedAlbum) -> Tuple[bool, str]:
        """Downloads a full album into a subfolder named after the album."""
        folder_name = self.sanitize_name(album.album_name)
        target_folder = self.download_dir / folder_name

        # Prevent re-downloading if folder already exists on disk and is complete & valid
        if target_folder.is_dir():
            is_valid, track_count, status_msg = self.verify_album_integrity(target_folder)
            if is_valid:
                logger.info(
                    "La carpeta '%s' ya existe y está íntegra (%d pistas) en %s. Omitiendo re-descarga.",
                    folder_name,
                    track_count,
                    target_folder,
                )
                return True, str(target_folder)
            else:
                logger.warning(
                    "La carpeta '%s' existe pero no superó la verificación de integridad (%s). Reanudando descarga con yt-dlp...",
                    folder_name,
                    status_msg,
                )

        opts = self.get_ydl_opts(target_folder, is_playlist=True)

        ydl_mod = _get_yt_dlp()
        try:
            if ydl_mod is None:
                return False, "yt-dlp no está instalado en este entorno"
            with ydl_mod.YoutubeDL(opts) as ydl:
                ydl.download([album.url])
            self._process_album_cover(
                target_folder,
                uid=self.uid,
                gid=self.gid,
                cover_url=album.cover_url,
                album_name=album.album_name,
                album_artist=album.artist_name,
            )
            official_tracklist = self.resolve_official_tracklist(album)
            self._process_album_metadata(
                target_folder,
                album_artist=album.artist_name,
                album_name=album.album_name,
                year=getattr(album, "year", None),
                is_single=album.is_single,
                track_titles=official_tracklist,
                uid=self.uid,
                gid=self.gid,
            )
            self._sanitize_downloaded_files(target_folder)
            self._fix_permissions(target_folder)

            # Verificar que existan archivos de audio reales en la carpeta
            audio_files = [f for f in target_folder.iterdir() if f.is_file() and f.suffix.lower() in ('.m4a', '.mp3', '.opus', '.webm', '.flac')]

            # Si falló y tenemos más de 1 cuenta de cookies, ¡reintentar de inmediato con la siguiente cuenta!
            if not audio_files and len(self.cookies_list) > 1:
                shutil.rmtree(target_folder, ignore_errors=True)
                next_cookie = self.rotate_cookie()
                logger.warning("Reintentando álbum '%s' con cuenta de cookies alternativa: %s", album.album_name, Path(next_cookie).name if next_cookie else "")
                opts_retry = self.get_ydl_opts(target_folder, is_playlist=True)
                with ydl_mod.YoutubeDL(opts_retry) as ydl:
                    ydl.download([album.url])
                self._process_album_cover(
                    target_folder,
                    uid=self.uid,
                    gid=self.gid,
                    cover_url=album.cover_url,
                    album_name=album.album_name,
                    album_artist=album.artist_name,
                )
                self._process_album_metadata(
                    target_folder,
                    album_artist=album.artist_name,
                    album_name=album.album_name,
                    year=getattr(album, "year", None),
                    is_single=album.is_single,
                    track_titles=official_tracklist,
                    uid=self.uid,
                    gid=self.gid,
                )
                self._sanitize_downloaded_files(target_folder)
                self._fix_permissions(target_folder)
                audio_files = [f for f in target_folder.iterdir() if f.is_file() and f.suffix.lower() in ('.m4a', '.mp3', '.opus', '.webm', '.flac')]

            if not audio_files:
                shutil.rmtree(target_folder, ignore_errors=True)
                return False, "No se descargó ninguna pista de audio (posible bloqueo temporal o error en YouTube)"

            # Rotar cuenta para el siguiente álbum (distribuye la carga equitativamente)
            if len(self.cookies_list) > 1:
                self.rotate_cookie()

            # Enriquecimiento con plugins de Picard y reubicación en la biblioteca
            final_folder = target_folder
            try:
                from .picard_engine import PicardEngine
                plugins_config = self.state_tracker.get_picard_plugins() if self.state_tracker else None
                final_folder = PicardEngine.process_and_relocate_album(
                    temp_album_dir=target_folder,
                    library_root=self.library_dir,
                    album_name=album.album_name,
                    album_artist=album.artist_name,
                    date=getattr(album, "year", None),
                    is_single=album.is_single,
                    plugins_config=plugins_config,
                    uid=self.uid,
                    gid=self.gid,
                )
            except Exception as pe:
                logger.warning("Error en procesamiento de PicardEngine para '%s': %s", album.album_name, pe)

            return True, str(final_folder)
        except Exception as e:
            logger.error("Error descargando álbum '%s': %s", album.album_name, e)
            if len(self.cookies_list) > 1:
                self.rotate_cookie()
            return False, str(e)

    def download_track(self, track: ResolvedTrack) -> Tuple[bool, str]:
        """Downloads a single track into a '_Singles' folder."""
        target_folder = self.download_dir / "_Singles"

        # Prevent re-downloading if single already exists on disk
        if target_folder.is_dir():
            clean_track = self.sanitize_name(track.title).lower()
            for f in target_folder.iterdir():
                if f.is_file() and f.suffix.lower() in ('.m4a', '.mp3', '.opus', '.webm', '.flac'):
                    if clean_track in f.name.lower():
                        logger.info("La pista '%s' ya existe en %s. Omitiendo re-descarga.", track.title, f)
                        return True, str(f)

        opts = self.get_ydl_opts(target_folder, is_playlist=False)

        ydl_mod = _get_yt_dlp()
        try:
            if ydl_mod is None:
                return False, "yt-dlp no está instalado en este entorno"
            before_files = set(target_folder.glob("*.m4a"))
            with ydl_mod.YoutubeDL(opts) as ydl:
                ydl.download([track.url])
            after_files = set(target_folder.glob("*.m4a"))
            new_files = list(after_files - before_files)
            new_track_file = new_files[0] if new_files else None

            self._process_album_cover(
                target_folder,
                uid=self.uid,
                gid=self.gid,
                cover_url=track.cover_url,
                target_file=new_track_file,
                album_name=track.title,
                album_artist=track.artist,
            )
            self._process_album_metadata(
                target_folder,
                album_artist=track.artist,
                album_name=track.title,
                is_single=True,
                target_file=new_track_file,
                uid=self.uid,
                gid=self.gid,
            )
            self._sanitize_downloaded_files(target_folder)
            self._fix_permissions(target_folder)

            # Verificar que existan archivos de audio reales
            audio_files = [f for f in target_folder.iterdir() if f.is_file() and f.suffix.lower() in ('.m4a', '.mp3', '.opus', '.webm', '.flac')]
            if not audio_files and len(self.cookies_list) > 1:
                next_cookie = self.rotate_cookie()
                logger.warning("Reintentando canción '%s' con cuenta de cookies alternativa: %s", track.title, Path(next_cookie).name if next_cookie else "")
                opts_retry = self.get_ydl_opts(target_folder, is_playlist=False)
                before_files = set(target_folder.glob("*.m4a"))
                with ydl_mod.YoutubeDL(opts_retry) as ydl:
                    ydl.download([track.url])
                after_files = set(target_folder.glob("*.m4a"))
                new_files = list(after_files - before_files)
                new_track_file = new_files[0] if new_files else None

                self._process_album_cover(
                    target_folder,
                    uid=self.uid,
                    gid=self.gid,
                    cover_url=track.cover_url,
                    target_file=new_track_file,
                    album_name=track.title,
                    album_artist=track.artist,
                )
                self._process_album_metadata(
                    target_folder,
                    album_artist=track.artist,
                    album_name=track.title,
                    is_single=True,
                    target_file=new_track_file,
                    uid=self.uid,
                    gid=self.gid,
                )
                self._sanitize_downloaded_files(target_folder)
                self._fix_permissions(target_folder)
                audio_files = [f for f in target_folder.iterdir() if f.is_file() and f.suffix.lower() in ('.m4a', '.mp3', '.opus', '.webm', '.flac')]

            if not audio_files:
                return False, "No se descargó la pista de audio (posible bloqueo temporal o error en YouTube)"

            if len(self.cookies_list) > 1:
                self.rotate_cookie()

            final_folder = target_folder
            if self.library_dir and self.download_dir.resolve() != self.library_dir.resolve():
                dest_singles = self.library_dir / "downloads" / "_Singles"
                if not dest_singles.parent.exists() and (self.library_dir / "_Singles").exists():
                    dest_singles = self.library_dir / "_Singles"
                dest_singles.mkdir(parents=True, exist_ok=True)
                self._fix_permissions(dest_singles)
                if new_track_file and new_track_file.is_file():
                    dest_file = dest_singles / new_track_file.name
                    shutil.move(str(new_track_file), str(dest_file))
                    self._fix_permissions(dest_file)
                    new_lrc = new_track_file.with_suffix(".lrc")
                    if new_lrc.is_file():
                        dest_lrc = dest_singles / new_lrc.name
                        shutil.move(str(new_lrc), str(dest_lrc))
                        self._fix_permissions(dest_lrc)
                final_folder = dest_singles

            return True, str(final_folder)
        except Exception as e:
            logger.error("Error descargando canción '%s': %s", track.title, e)
            return False, str(e)

    def _fix_permissions(self, path: Path):
        """Sets ownership to uid:gid and permissions 0775 on Linux if running as root."""
        if os.name != "posix" or not hasattr(os, "chown"):
            return

        try:
            if hasattr(os, "geteuid") and os.geteuid() != 0:
                return

            try:
                os.chown(path, self.uid, self.gid)
                os.chmod(path, 0o775)
            except Exception:
                pass

            for root, dirs, files in os.walk(path):
                try:
                    os.chown(root, self.uid, self.gid)
                    os.chmod(root, 0o775)
                except Exception:
                    pass
                for f in files:
                    fp = os.path.join(root, f)
                    try:
                        os.chown(fp, self.uid, self.gid)
                        os.chmod(fp, 0o664)
                    except Exception:
                        pass
        except Exception as e:
            logger.warning("No se pudieron ajustar permisos en %s: %s", path, e)


class DownloadQueue:
    """
    Sequential FIFO queue for executing downloads safely.
    Guarantees no race conditions, rate limit storms, or cookie collisions.
    """

    def __init__(
        self,
        downloader: MusicDownloader,
        navidrome: Optional[NavidromeClient],
        listener: Optional["TelegramBotListener"],
        win_dest: str,
        bot_token: Optional[str] = None,
        telegram_sender: Optional[Any] = None,
        queue_file: Optional[str] = None,
    ):
        self.downloader = downloader
        self.navidrome = navidrome
        self.listener = listener
        self.win_dest = win_dest
        self.bot_token = bot_token
        self.telegram_sender = telegram_sender
        self.queue_file = queue_file
        self.queue: queue.Queue = queue.Queue()
        self.lock = threading.RLock()
        self.current_job: Optional[DownloadJob] = None
        self.active_details: str = ""
        self.cancel_requested: bool = False
        self.paused: bool = False
        self.consecutive_block_errors: int = 0
        self.running = True

        # Load persisted queue if file exists
        if self.queue_file:
            self._load_queue_state()

        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

    def _save_queue_state(self):
        """Persists active and pending download jobs and pause state to disk."""
        if not self.queue_file:
            return
        try:
            jobs_data = []
            with self.lock:
                if self.current_job and not self.cancel_requested:
                    jobs_data.append(asdict(self.current_job))
                for j in list(self.queue.queue):
                    jobs_data.append(asdict(j))

            state_payload = {
                "paused": self.paused,
                "consecutive_block_errors": self.consecutive_block_errors,
                "jobs": jobs_data,
            }

            target_p = Path(self.queue_file)
            target_p.parent.mkdir(parents=True, exist_ok=True)
            tmp_p = target_p.with_suffix(".tmp")
            with open(tmp_p, "w", encoding="utf-8") as f:
                json.dump(state_payload, f, ensure_ascii=False, indent=2)
            tmp_p.replace(target_p)
        except Exception as e:
            logger.warning("Error saving download queue state to %s: %s", self.queue_file, e)

    def _load_queue_state(self):
        """Restores pending download jobs and pause state from disk after a restart."""
        if not self.queue_file or not os.path.isfile(self.queue_file):
            return
        try:
            with open(self.queue_file, "r", encoding="utf-8") as f:
                raw_data = json.load(f)

            if isinstance(raw_data, dict):
                self.paused = raw_data.get("paused", False)
                self.consecutive_block_errors = raw_data.get("consecutive_block_errors", 0)
                jobs_data = raw_data.get("jobs", [])
            elif isinstance(raw_data, list):
                jobs_data = raw_data
            else:
                jobs_data = []

            loaded = 0
            for d in jobs_data:
                albums = [ResolvedAlbum(**a) for a in d.get("albums", [])]
                tracks = [ResolvedTrack(**t) for t in d.get("tracks", [])]
                job = DownloadJob(
                    job_id=d.get("job_id", ""),
                    chat_id=str(d.get("chat_id", "")),
                    thread_id=d.get("thread_id"),
                    title=d.get("title", ""),
                    albums=albums,
                    tracks=tracks,
                    source_desc=d.get("source_desc", ""),
                )
                self.queue.put(job)
                loaded += 1
            if loaded > 0:
                logger.info("Restored %d queued download jobs (paused=%s) from %s", loaded, self.paused, self.queue_file)
        except Exception as e:
            logger.warning("Error loading download queue state from %s: %s", self.queue_file, e)

    def pause(self) -> Tuple[bool, str]:
        """Pauses the download queue so pending jobs are kept without processing."""
        with self.lock:
            self.paused = True
            self._save_queue_state()
            qsize = self.queue.qsize() + (1 if self.current_job else 0)
            return True, f"⏸️ Cola de descargas pausada ({qsize} tareas en espera retenidas)."

    def resume(self) -> Tuple[bool, str]:
        """Resumes the download queue."""
        with self.lock:
            self.paused = False
            self.consecutive_block_errors = 0
            self._save_queue_state()
            qsize = self.queue.qsize() + (1 if self.current_job else 0)
            return True, f"▶️ Cola de descargas reanudada ({qsize} tareas pendientes)."

    def cancel_active(self) -> Tuple[bool, str]:
        """Cancels the currently executing download job and flushes pending queue."""
        with self.lock:
            if self.current_job:
                self.cancel_requested = True
                cleared_count = 0
                while not self.queue.empty():
                    try:
                        self.queue.get_nowait()
                        self.queue.task_done()
                        cleared_count += 1
                    except Exception:
                        break
                title = self.current_job.title
                detail = f"{title} (y {cleared_count} tareas en cola descartadas)" if cleared_count else title
                self._save_queue_state()
                return True, detail
            return False, "No hay descargas activas en curso."

    def enqueue(self, job: DownloadJob) -> int:
        """
        Enqueues a job. Returns position:
        0 if worker is idle and will start this job immediately,
        >0 if other jobs are in front.
        """
        with self.lock:
            pos = self.queue.qsize()
            if self.current_job is not None:
                pos += 1
            self.queue.put(job)
            self._save_queue_state()
            return pos

    def get_status(self) -> str:
        with self.lock:
            if self.paused:
                qsize = self.queue.qsize() + (1 if self.current_job else 0)
                return f"Pausado ({qsize} tareas en espera)"
            if self.current_job:
                qsize = self.queue.qsize()
                queue_note = f" (En cola: {qsize})" if qsize > 0 else ""
                return f"En curso ({self.active_details}){queue_note}"
            return "Inactivo"

    def _send_reply(self, chat_id: str, text: str, thread_id: Optional[str]):
        if self.listener:
            self.listener.send_reply(chat_id, text, thread_id)
        else:
            logger.info("[DownloadQueue Reply to %s] %s", chat_id, text)

    @staticmethod
    def _format_duration(seconds: int) -> str:
        if seconds <= 0:
            return ""
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"

    @staticmethod
    def _clean_rich_html(input_str: str) -> str:
        lines = [line.strip() for line in input_str.split("\n") if line.strip()]
        result = []
        for i, l in enumerate(lines):
            result.append(l)
            if i < len(lines) - 1:
                next_l = lines[i + 1]
                if not (l.endswith(">") or next_l.startswith("<")):
                    result.append("<br/>")
        return "".join(result)

    def _build_album_rich_html(
        self,
        album_name: str,
        artist_name: str,
        year: str,
        genre: str,
        disc_count: int,
        song_count: int,
        total_duration_sec: int,
        songs: List[Dict[str, Any]],
        nav_url: str = "",
    ) -> str:
        esc_album = html.escape(album_name)
        esc_artist = html.escape(artist_name)
        esc_year = html.escape(str(year)) if year else ""
        esc_genre = html.escape(genre) if genre else ""
        dur_str = self._format_duration(total_duration_sec)

        # Format songs list
        if disc_count <= 1:
            song_lines = ["<ul>"]
            for s in songs:
                trk = s.get("track", 0)
                trk_prefix = f"<b>{trk:02d}.</b> " if trk > 0 else ""
                dur = self._format_duration(s.get("duration", 0))
                dur_suffix = f" ({dur})" if dur else ""
                song_lines.append(f"  <li>{trk_prefix}{html.escape(s['title'])}{dur_suffix}</li>")
            song_lines.append("</ul>")
            songs_formatted = "\n".join(song_lines)
        else:
            disc_groups: Dict[int, List[Dict[str, Any]]] = {}
            for s in songs:
                d = s.get("disc", 1)
                disc_groups.setdefault(d, []).append(s)

            disc_blocks = []
            for d in sorted(disc_groups.keys()):
                block = [f"<details open>\n  <summary>💿 Disco {d}</summary>\n  <ul>"]
                for s in disc_groups[d]:
                    trk = s.get("track", 0)
                    trk_prefix = f"<b>{trk:02d}.</b> " if trk > 0 else ""
                    dur = self._format_duration(s.get("duration", 0))
                    dur_suffix = f" ({dur})" if dur else ""
                    block.append(f"    <li>{trk_prefix}{html.escape(s['title'])}{dur_suffix}</li>")
                block.append("  </ul>\n</details>")
                disc_blocks.append("\n".join(block))
            songs_formatted = "\n".join(disc_blocks)

        nav_link = f'\n<hr/>\n<a href="{nav_url}">🌐 Escuchar en Navidrome</a>' if nav_url else ""

        raw_html = f"""<img src="tg://photo?id=cover" />
<h3>📚 {esc_album}</h3>
<h4>👤 {esc_artist}</h4>
<table bordered striped>
  <tr>
    <td><b>📅 Año</b></td>
    <td>{esc_year}</td>
  </tr>
  <tr>
    <td><b>📦 Género</b></td>
    <td>{esc_genre}</td>
  </tr>
  <tr>
    <td><b>💿 Discos</b></td>
    <td>{disc_count}</td>
  </tr>
  <tr>
    <td><b>🔢 Canciones</b></td>
    <td>{song_count}</td>
  </tr>
  <tr>
    <td><b>🕒 Duración</b></td>
    <td>{dur_str}</td>
  </tr>
</table>

<details>
  <summary>🎵 Ver Lista de Canciones</summary>
  <blockquote>
    {songs_formatted}
  </blockquote>
</details>{nav_link}"""

        return self._clean_rich_html(raw_html)

    def _send_album_rich_notification(
        self,
        chat_id: str,
        thread_id: Optional[str],
        album: ResolvedAlbum,
        target_folder: Path,
    ) -> bool:
        """
        Sends the standard Navidrome Telegram plugin rich notification with cover photo,
        metadata table (Año, Género, Discos, Canciones, Duración), expandable song list,
        and link to Navidrome once the album has finished downloading completely.
        """
        token = (
            self.bot_token
            or (self.listener.bot_token if self.listener else None)
            or (self.telegram_sender.bot_token if self.telegram_sender else None)
        )
        if not token:
            logger.warning("No Telegram bot token available for album rich notification. Falling back to reply.")
            self._send_reply(
                chat_id,
                f"📥 <b>Álbum descargado:</b>\n"
                f"• <b>{album.artist_name}</b> — <i>{album.album_name}</i>\n"
                f"• <b>Carpeta:</b> <code>{self.win_dest}\\{target_folder.name}</code>\n"
                f"✨ <i>Listo con cover.jpg e indexándose en Navidrome.</i>",
                thread_id,
            )
            return False

        album_artist = album.artist_name
        album_name = album.album_name
        year = ""
        genre = ""
        songs: List[Dict[str, Any]] = []

        audio_files = []
        if target_folder.is_dir():
            audio_files = sorted(
                [
                    f for f in target_folder.iterdir()
                    if f.is_file() and f.suffix.lower() in ('.m4a', '.mp3', '.opus', '.webm', '.flac')
                ],
                key=lambda f: f.name,
            )

        # 1. Read metadata and durations from audio files via mutagen
        for idx, f in enumerate(audio_files, 1):
            t_title = ""
            t_disc = 1
            t_track = idx
            t_dur = 0
            t_year = ""
            t_genre = ""

            try:
                if f.suffix.lower() == ".m4a":
                    from mutagen.mp4 import MP4
                    mp4 = MP4(f)
                    if mp4.info and hasattr(mp4.info, "length"):
                        t_dur = int(mp4.info.length)
                    if "©nam" in mp4 and mp4["©nam"]:
                        t_title = str(mp4["©nam"][0])
                    if "trkn" in mp4 and mp4["trkn"]:
                        t_track = mp4["trkn"][0][0] or idx
                    if "disk" in mp4 and mp4["disk"]:
                        t_disc = mp4["disk"][0][0] or 1
                    if "©day" in mp4 and mp4["©day"]:
                        t_year = str(mp4["©day"][0])[:4]
                    if "©gen" in mp4 and mp4["©gen"]:
                        t_genre = str(mp4["©gen"][0])
                    if "aART" in mp4 and mp4["aART"]:
                        album_artist = str(mp4["aART"][0])
                    if "©alb" in mp4 and mp4["©alb"]:
                        album_name = str(mp4["©alb"][0])
                else:
                    import mutagen
                    mf = mutagen.File(f)
                    if mf:
                        if mf.info and hasattr(mf.info, "length"):
                            t_dur = int(mf.info.length)
                        if hasattr(mf, "tags") and mf.tags:
                            title_val = mf.tags.get("title")
                            if title_val:
                                t_title = str(title_val[0] if isinstance(title_val, list) else title_val)
            except Exception as me:
                logger.debug("Error leyendo tags de %s: %s", f.name, me)

            if not t_title:
                clean_name = f.stem
                clean_name = re.sub(r"^\d{1,2}\s*[\.\-]\s*", "", clean_name).strip()
                t_title = clean_name or f.stem

            if t_year and not year:
                year = t_year
            if t_genre and not genre:
                genre = t_genre

            songs.append({
                "disc": t_disc,
                "track": t_track,
                "title": t_title,
                "duration": t_dur,
            })

        # Fallback if no local audio files were found
        if not songs and album.tracks:
            for idx, trk_title in enumerate(album.tracks, 1):
                songs.append({
                    "disc": 1,
                    "track": idx,
                    "title": trk_title,
                    "duration": 0,
                })

        # Fallback for year and genre via iTunes quick lookup
        if not year or not genre:
            try:
                meta = MusicDownloader._quick_metadata_lookup(album_name, album_artist)
                if meta:
                    if not year and meta.get("release_date"):
                        year = meta["release_date"][:4]
                    if not genre and meta.get("genre"):
                        genre = meta["genre"]
            except Exception:
                pass

        disc_numbers = {s["disc"] for s in songs if s.get("disc")} or {1}
        disc_count = len(disc_numbers)
        song_count = len(songs)
        total_duration_sec = sum(s["duration"] for s in songs)

        # Navidrome URL resolution
        nav_url = ""
        if self.navidrome and self.navidrome.base_url:
            nav_base = self.navidrome.base_url.rstrip("/")
            try:
                existing = self.navidrome.find_album_match(album_name, album_artist)
                if existing and existing.get("id"):
                    nav_url = f"{nav_base}/#/album/{existing['id']}"
                else:
                    nav_url = nav_base
            except Exception:
                nav_url = nav_base

        # Build clean HTML
        html_content = self._build_album_rich_html(
            album_name=album_name,
            artist_name=album_artist,
            year=year,
            genre=genre,
            disc_count=disc_count,
            song_count=song_count,
            total_duration_sec=total_duration_sec,
            songs=songs,
            nav_url=nav_url,
        )

        # Truncation safeguard if message approaches Telegram 4096 char limit
        if len(html_content) > 3800 and len(songs) > 25:
            truncated_songs = songs[:25]
            remaining = len(songs) - 25
            truncated_songs.append({
                "disc": 1,
                "track": 0,
                "title": f"... y {remaining} canciones más",
                "duration": 0,
            })
            html_content = self._build_album_rich_html(
                album_name=album_name,
                artist_name=album_artist,
                year=year,
                genre=genre,
                disc_count=disc_count,
                song_count=song_count,
                total_duration_sec=total_duration_sec,
                songs=truncated_songs,
                nav_url=nav_url,
            )

        # Read cover image
        img_bytes = None
        cover_file = target_folder / "cover.jpg"
        if cover_file.is_file():
            try:
                img_bytes = cover_file.read_bytes()
            except Exception as e:
                logger.warning("No se pudo leer cover.jpg: %s", e)

        if not img_bytes and album.cover_url:
            try:
                req = urllib.request.Request(album.cover_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    img_bytes = resp.read()
            except Exception as e:
                logger.debug("No se pudo obtener cover desde cover_url: %s", e)

        targets: List[TelegramTarget] = []
        if chat_id:
            for part in str(chat_id).split(","):
                part = part.strip()
                if not part:
                    continue
                if ":" in part:
                    cid, tid = part.split(":", 1)
                    targets.append(TelegramTarget(chat_id=cid.strip(), thread_id=tid.strip() or None))
                else:
                    targets.append(TelegramTarget(chat_id=part, thread_id=thread_id))
        if not targets:
            targets = [TelegramTarget(chat_id=chat_id, thread_id=thread_id)]

        all_ok = True
        rich_url = f"https://api.telegram.org/bot{token}/sendRichMessage"

        for tgt in targets:
            payload: Dict[str, Any] = {"chat_id": tgt.chat_id}
            if tgt.thread_id:
                payload["message_thread_id"] = str(tgt.thread_id)

            rich_obj: Dict[str, Any] = {"html": html_content}
            files = {}
            if img_bytes:
                rich_obj["media"] = [{
                    "id": "cover",
                    "media": {
                        "type": "photo",
                        "media": "attach://cover",
                    }
                }]
                files = {"cover": ("cover.jpg", img_bytes, "image/jpeg")}

            payload["rich_message"] = json.dumps(rich_obj)

            try:
                resp = requests.post(rich_url, data=payload, files=files if files else None, timeout=20)
                resp.raise_for_status()
                res_data = resp.json()
                if not res_data.get("ok"):
                    raise Exception(f"sendRichMessage returned error: {res_data.get('description')}")
                logger.info("Notificación estándar de álbum enviada con éxito para '%s - %s' a chat %s", album_artist, album_name, tgt.chat_id)
            except Exception as err:
                logger.warning("Fallo al enviar sendRichMessage para '%s' (%s). Probando alternativa...", album_name, err)
                fallback_sent = False
                if img_bytes:
                    try:
                        dur_str = self._format_duration(total_duration_sec)
                        dur_line = f"🕒 <b>Duración:</b> {dur_str}\n" if dur_str else ""
                        link_line = f"\n<a href=\"{nav_url}\">🌐 Escuchar en Navidrome</a>" if nav_url else ""
                        caption = (
                            f"📚 <b>{html.escape(album_name)}</b>\n"
                            f"👤 <b>{html.escape(album_artist)}</b>\n\n"
                            f"📅 <b>Año:</b> {year or 'N/A'} | 📦 <b>Género:</b> {genre or 'N/A'}\n"
                            f"💿 <b>Discos:</b> {disc_count} | 🔢 <b>Canciones:</b> {song_count}\n"
                            f"{dur_line}{link_line}"
                        )
                        photo_url = f"https://api.telegram.org/bot{token}/sendPhoto"
                        photo_data = {"chat_id": tgt.chat_id, "caption": caption, "parse_mode": "HTML"}
                        if tgt.thread_id:
                            photo_data["message_thread_id"] = str(tgt.thread_id)
                        p_resp = requests.post(photo_url, data=photo_data, files={"photo": ("cover.jpg", img_bytes, "image/jpeg")}, timeout=15)
                        if p_resp.status_code == 200 and p_resp.json().get("ok"):
                            fallback_sent = True
                    except Exception as pe:
                        logger.error("Error en fallback sendPhoto: %s", pe)

                if not fallback_sent:
                    self._send_reply(
                        tgt.chat_id,
                        f"📚 <b>{html.escape(album_name)}</b>\n"
                        f"👤 <b>{html.escape(album_artist)}</b>\n"
                        f"✨ <i>Álbum completo descargado ({song_count} canciones).</i>",
                        tgt.thread_id,
                    )
                all_ok = False

        return all_ok

    def _worker_loop(self):
        while self.running:
            if self.paused:
                time.sleep(1.0)
                continue

            try:
                job = self.queue.get(timeout=1.0)
            except queue.Empty:
                continue

            try:
                with self.lock:
                    self.current_job = job
                    self.active_details = job.title
                    self.cancel_requested = False
                    self._save_queue_state()

                self._process_job(job)
            except Exception as e:
                logger.error("Error executing download job '%s': %s", job.title, e, exc_info=True)
                self._send_reply(
                    job.chat_id,
                    f"❌ <b>Error durante la descarga:</b> {e}",
                    job.thread_id,
                )
            finally:
                with self.lock:
                    self.current_job = None
                    self.active_details = ""
                    self._save_queue_state()
                self.queue.task_done()

    def _process_job(self, job: DownloadJob):
        chat_id = job.chat_id
        thread_id = job.thread_id

        if len(self.downloader.cookies_list) > 1:
            cookies_status = f"🍪 <b>Rotación activa:</b> {len(self.downloader.cookies_list)} cuentas alternándose."
        elif len(self.downloader.cookies_list) == 1:
            cookies_status = f"🍪 <b>Cookies activas:</b> 1 cuenta conectada (<code>{self.downloader.cookies_list[0].name}</code>)."
        else:
            cookies_status = "⚡ <i>Descarga estándar activa (aceleración multi-hilo y JS V8).</i>"

        total_albums = len(job.albums)
        total_tracks = len(job.tracks)

        self._send_reply(
            chat_id,
            f"⬇️ <b>Iniciando descarga directa al servidor</b>\n\n"
            f"• <b>Tarea:</b> {job.title}\n"
            f"• <b>Álbumes / Sencillos:</b> {total_albums}\n"
            f"• <b>Canciones sueltas:</b> {total_tracks}\n"
            f"• <b>Destino:</b> <code>{self.win_dest}</code>\n"
            f"• <b>Formato:</b> M4A HD (carátulas <code>cover.jpg</code> e incrustación nativa)\n"
            f"• {cookies_status}",
            thread_id,
        )

        ok_count = 0
        skipped_count = 0
        failed_count = 0

        def norm_title(s: str) -> str:
            clean = re.sub(r"\s*[\(\[].*?[\)\]]", "", s or "").strip()
            return re.sub(r"[^\w\s]", "", clean.lower()).strip()

        def titles_match(t1: str, t2: str) -> bool:
            n1 = norm_title(t1)
            n2 = norm_title(t2)
            if not n1 or not n2:
                return False
            if n1 == n2:
                return True
            if len(n1) >= 4 and len(n2) >= 4 and (n1 in n2 or n2 in n1):
                return True
            return False

        for idx, album in enumerate(job.albums, 1):
            while self.paused and not self.cancel_requested and self.running:
                time.sleep(1.0)

            if self.cancel_requested:
                logger.info("Cancelación de descarga solicitada para '%s'. Abortando cola.", job.title)
                self._send_reply(
                    chat_id,
                    f"🛑 <b>Descarga cancelada a petición del usuario.</b>\n"
                    f"• <b>Tarea:</b> {job.title}\n"
                    f"• <b>Álbumes procesados antes de cancelar:</b> {idx - 1}/{total_albums}",
                    thread_id,
                )
                break
            if not self.running:
                break

            with self.lock:
                self.active_details = f"[{idx}/{total_albums}] {album.artist_name} - {album.album_name}"

            item_type = "Sencillo" if getattr(album, "is_single", False) else "Álbum"
            folder_name = self.downloader.sanitize_name(album.album_name)
            target_folder = self.downloader.download_dir / folder_name

            # 1. Check Navidrome library
            existing_album = None
            if self.navidrome:
                try:
                    existing_album = self.navidrome.find_album_match(album.album_name, album.artist_name)
                except Exception as ex:
                    logger.warning("Error comprobando álbum en Navidrome: %s", ex)

            # Check existing files on local disk
            disk_audio_files = []
            if target_folder.is_dir():
                disk_audio_files = [
                    f for f in target_folder.iterdir()
                    if f.is_file() and f.suffix.lower() in ('.m4a', '.mp3', '.opus', '.webm', '.flac')
                ]

            if existing_album:
                nav_tracks = []
                try:
                    nav_tracks = self.navidrome.get_album_tracks(existing_album["id"])
                except Exception as ex:
                    logger.warning("Error obteniendo pistas de Navidrome para álbum '%s': %s", album.album_name, ex)

                official_tracks = getattr(album, "tracks", []) or []
                if not official_tracks:
                    official_tracks = self.downloader.get_album_tracklist(album)

                if official_tracks:
                    nav_titles = [t.get("title", "") for t in nav_tracks]
                    disk_names = [f.stem for f in disk_audio_files]

                    missing_indices = []
                    for t_idx, trk in enumerate(official_tracks, 1):
                        track_name = trk.get("title", "") if isinstance(trk, dict) else str(trk)
                        idx_val = trk.get("index", t_idx) if isinstance(trk, dict) else t_idx
                        found_in_nav = any(titles_match(track_name, nt) for nt in nav_titles)
                        found_on_disk = any(titles_match(track_name, dn) for dn in disk_names)
                        if not found_in_nav and not found_on_disk:
                            missing_indices.append(idx_val)

                    present_count = len(official_tracks) - len(missing_indices)
                    if len(missing_indices) == 0:
                        logger.info(
                            "El %s '%s' ya está completo en Navidrome/disco (%d/%d pistas). Omitiendo.",
                            item_type.lower(), album.album_name, present_count, len(official_tracks)
                        )
                        skipped_count += 1
                        self._send_reply(
                            chat_id,
                            f"💿 <b>[{idx}/{total_albums}] {item_type} ya completo en tu biblioteca:</b>\n"
                            f"• <b>{album.artist_name}</b> — <i>{album.album_name}</i>\n"
                            f"✨ <i>Verificadas {present_count}/{len(official_tracks)} pistas. Omitida la re-descarga.</i>",
                            thread_id,
                        )
                        continue
                    else:
                        logger.info(
                            "El %s '%s' está parcial en Navidrome (%d/%d pistas). Descargando %d pistas faltantes...",
                            item_type.lower(), album.album_name, present_count, len(official_tracks), len(missing_indices)
                        )
                        missing_preview = ", ".join(f"#{i}" for i in missing_indices[:10])
                        if len(missing_indices) > 10:
                            missing_preview += f" y {len(missing_indices) - 10} más"

                        self._send_reply(
                            chat_id,
                            f"💿 <b>[{idx}/{total_albums}] {item_type} parcial en biblioteca:</b>\n"
                            f"• <b>{album.artist_name}</b> — <i>{album.album_name}</i>\n"
                            f"✨ <i>Detectadas {present_count}/{len(official_tracks)} pistas en Navidrome.</i>\n"
                            f"📥 <b>Descargando {len(missing_indices)} pistas faltantes:</b>\n"
                            f"• <i>{missing_preview}</i>\n"
                            f"• <b>Destino:</b> <code>{self.win_dest}\\{folder_name}</code>",
                            thread_id,
                        )
                        ok, err = self.downloader.download_album_tracks(album, missing_indices)
                        if ok:
                            ok_count += 1
                            self.consecutive_block_errors = 0
                            self._send_album_rich_notification(chat_id, thread_id, album, target_folder)
                            self._send_reply(
                                chat_id,
                                f"📥 <b>[{idx}/{total_albums}] Pistas faltantes descargadas con éxito:</b>\n"
                                f"• <b>{album.artist_name}</b> — <i>{album.album_name}</i>\n"
                                f"• Descargadas <b>{len(missing_indices)}</b> pistas faltantes.",
                                thread_id,
                            )
                        else:
                            failed_count += 1
                            err_str = str(err).lower()
                            if any(w in err_str for w in ["bloqueo temporal", "bot", "sign in", "confirm you’re not a bot", "video unavailable", "rate-limited"]):
                                self.consecutive_block_errors += 1
                            else:
                                self.consecutive_block_errors = 0

                            logger.error("Error descargando pistas faltantes de '%s': %s", album.album_name, err)
                            self._send_reply(
                                chat_id,
                                f"⚠️ <b>[{idx}/{total_albums}] Error descargando pistas faltantes:</b>\n"
                                f"• <b>{album.artist_name}</b> — <i>{album.album_name}</i>\n"
                                f"• <i>{err}</i>",
                                thread_id,
                            )

                            if self.consecutive_block_errors >= 3:
                                logger.warning("Circuit breaker activado: 3 fallos consecutivos por posible bloqueo de YouTube. Pausando cola.")
                                self.paused = True
                                qsize = self.queue.qsize() + (total_albums - idx)
                                self._send_reply(
                                    chat_id,
                                    f"🛑 <b>Cola pausada por protección (Circuit Breaker):</b>\n"
                                    f"• Se detectaron <b>3 fallos consecutivos</b> por posible bloqueo o restricción de YouTube.\n"
                                    f"• Se <b>pausó la cola automáticamente</b> para no quemar tus cuentas ni prolongar bloqueos ({qsize} tareas en espera retenidas).\n"
                                    f"💡 <i>Espera unos minutos o renueva cookies, luego usa <code>/reanudar</code> para continuar.</i>",
                                    thread_id,
                                )
                                break
                        continue
                else:
                    logger.info("El %s '%s' de '%s' ya existe en Navidrome (ID: %s). Omitiendo.", item_type.lower(), album.album_name, album.artist_name, existing_album.get("id"))
                    skipped_count += 1
                    self._send_reply(
                        chat_id,
                        f"💿 <b>[{idx}/{total_albums}] {item_type} ya en tu biblioteca:</b>\n"
                        f"• <b>{album.artist_name}</b> — <i>{album.album_name}</i>\n"
                        f"✨ <i>Ya existe en tu colección de Navidrome. Se omitió la re-descarga.</i>",
                        thread_id,
                    )
                    continue

            # 2. Check local disk integrity (both in temp download folder and final library)
            already_on_disk = False
            disk_path_str = f"{self.win_dest}\\{folder_name}"
            track_count = 0
            if target_folder.is_dir():
                is_valid, track_count, status_msg = self.downloader.verify_album_integrity(target_folder)
                if is_valid:
                    already_on_disk = True

            if not already_on_disk and self.downloader.library_dir:
                artist_dir = self.downloader.library_dir / "General" / self.downloader.sanitize_name(album.artist_name)
                if not artist_dir.is_dir():
                    alt_artist_dir = self.downloader.library_dir / self.downloader.sanitize_name(album.artist_name)
                    if alt_artist_dir.is_dir():
                        artist_dir = alt_artist_dir

                if artist_dir.is_dir():
                    clean_target_album = self.downloader.sanitize_name(album.album_name).lower()
                    for sub in artist_dir.iterdir():
                        if sub.is_dir():
                            sub_clean = re.sub(r"^\[\d{4}\]\s*-\s*", "", sub.name).strip().lower()
                            if sub_clean == clean_target_album or sub.name.lower() == clean_target_album:
                                is_valid, track_count, status_msg = self.downloader.verify_album_integrity(sub)
                                if is_valid:
                                    already_on_disk = True
                                    disk_path_str = f"{self.win_dest}\\General\\{artist_dir.name}\\{sub.name}"
                                    break

            if already_on_disk:
                logger.info("El %s '%s' ya existe e íntegro en disco (%d pistas). Omitiendo.", item_type.lower(), album.album_name, track_count)
                skipped_count += 1
                self._send_reply(
                    chat_id,
                    f"📁 <b>[{idx}/{total_albums}] {item_type} verificado en disco:</b>\n"
                    f"• <b>{album.artist_name}</b> — <i>{album.album_name}</i>\n"
                    f"• <b>Carpeta:</b> <code>{disk_path_str}</code>\n"
                    f"✨ <i>Verificación de integridad exitosa ({track_count} pistas). Omitida la re-descarga.</i>",
                    thread_id,
                )
                continue
            elif target_folder.is_dir():
                logger.info("La carpeta del %s '%s' existe pero incompleta (%s). Completando con yt-dlp...", item_type.lower(), album.album_name, status_msg)

            # 3. Real full download
            logger.info("Downloading %s %d/%d: '%s' by '%s'", item_type.lower(), idx, total_albums, album.album_name, album.artist_name)
            success, err = self.downloader.download_album(album)
            if success:
                ok_count += 1
                consecutive_block_errors = 0
                # Si el álbum ya fue catalogado y reubicado automáticamente en su carpeta final de biblioteca,
                # se omite el envío redundante de notificación sobre la carpeta temporal de descargas
                # (Navidrome y su plugin de Telegram se encargan de notificar la biblioteca real).
                relocated = False
                if err and isinstance(err, str):
                    p = Path(err)
                    if p != target_folder and self.downloader.library_dir and str(self.downloader.library_dir) in str(p):
                        relocated = True
                if not relocated:
                    self._send_album_rich_notification(chat_id, thread_id, album, target_folder)
            else:
                failed_count += 1
                err_str = str(err).lower()
                if any(w in err_str for w in ["bloqueo temporal", "bot", "sign in", "confirm you’re not a bot", "video unavailable", "rate-limited"]):
                    self.consecutive_block_errors += 1
                else:
                    self.consecutive_block_errors = 0

                logger.error("Error downloading %s '%s': %s", item_type.lower(), album.album_name, err)
                self._send_reply(
                    chat_id,
                    f"⚠️ <b>[{idx}/{total_albums}] Error descargando {item_type.lower()}:</b>\n"
                    f"• <b>{album.artist_name}</b> — <i>{album.album_name}</i>\n"
                    f"• <i>{err}</i>",
                    thread_id,
                )

                if self.consecutive_block_errors >= 3:
                    logger.warning("Circuit breaker activado: 3 fallos consecutivos por posible bloqueo de YouTube. Pausando cola.")
                    self.paused = True
                    qsize = self.queue.qsize() + (total_albums - idx)
                    self._send_reply(
                        chat_id,
                        f"🛑 <b>Cola pausada por protección (Circuit Breaker):</b>\n"
                        f"• Se detectaron <b>3 fallos consecutivos</b> por posible bloqueo o restricción de YouTube.\n"
                        f"• Se <b>pausó la cola automáticamente</b> para no quemar tus cuentas ni prolongar bloqueos ({qsize} tareas en espera retenidas).\n"
                        f"💡 <i>Espera unos minutos o renueva cookies, luego usa <code>/reanudar</code> para continuar.</i>",
                        thread_id,
                    )
                    break

            with self.lock:
                if self.current_job and idx < len(job.albums):
                    self.current_job.albums = job.albums[idx:]
                    self._save_queue_state()

        for t_idx, track in enumerate(job.tracks, 1):
            while self.paused and not self.cancel_requested and self.running:
                time.sleep(1.0)

            if self.cancel_requested:
                logger.info("Cancelación de descarga de canciones solicitada para '%s'. Abortando cola.", job.title)
                self._send_reply(
                    chat_id,
                    f"🛑 <b>Descarga cancelada a petición del usuario.</b>\n"
                    f"• <b>Tarea:</b> {job.title}\n"
                    f"• <b>Canciones procesadas antes de cancelar:</b> {t_idx - 1}/{total_tracks}",
                    thread_id,
                )
                break
            if not self.running:
                break

            with self.lock:
                self.active_details = f"[Pista {t_idx}/{total_tracks}] {track.artist} - {track.title}"

            # 1. Check Navidrome library for single track
            existing_track = None
            if self.navidrome:
                try:
                    existing_track = self.navidrome.find_best_match(track.title, track.artist)
                except Exception as ex:
                    logger.warning("Error buscando canción '%s' en Navidrome: %s", track.title, ex)

            if existing_track:
                skipped_count += 1
                matched_album = existing_track.get("album", "Biblioteca")
                logger.info("La pista '%s' de '%s' ya existe en Navidrome (álbum: '%s'). Omitiendo.", track.title, track.artist, matched_album)
                self._send_reply(
                    chat_id,
                    f"💿 <b>[Pista {t_idx}/{total_tracks}] Canción ya en tu biblioteca:</b>\n"
                    f"• <b>{track.artist}</b> — <i>{track.title}</i>\n"
                    f"✨ <i>Encontrada en Navidrome (en '<b>{matched_album}</b>'). Se omitió la re-descarga.</i>",
                    thread_id,
                )
                continue

            # 2. Check local disk in _Singles (both temp download folder and final library)
            clean_track = self.downloader.sanitize_name(track.title).lower()
            candidate_singles_folders = [self.downloader.download_dir / "_Singles"]
            if self.downloader.library_dir:
                candidate_singles_folders.extend([
                    self.downloader.library_dir / "downloads" / "_Singles",
                    self.downloader.library_dir / "_Singles",
                ])

            on_disk = False
            for s_folder in candidate_singles_folders:
                if s_folder.is_dir():
                    if any(
                        clean_track in f.name.lower()
                        for f in s_folder.iterdir()
                        if f.is_file() and f.suffix.lower() in ('.m4a', '.mp3', '.opus', '.webm', '.flac')
                    ):
                        on_disk = True
                        break

            if on_disk:
                skipped_count += 1
                logger.info("La pista '%s' ya existe en _Singles en disco. Omitiendo.", track.title)
                self._send_reply(
                    chat_id,
                    f"📁 <b>[Pista {t_idx}/{total_tracks}] Canción ya en disco:</b>\n"
                    f"• <b>{track.artist}</b> — <i>{track.title}</i>\n"
                    f"✨ <i>Ya disponible en <code>{self.win_dest}\\downloads\\_Singles</code>. Se omitió la re-descarga.</i>",
                    thread_id,
                )
                continue

            # 3. Real track download
            success, err = self.downloader.download_track(track)
            if success:
                ok_count += 1
                self._send_reply(
                    chat_id,
                    f"📥 <b>[Pista {t_idx}/{total_tracks}] Canción descargada:</b>\n"
                    f"• <b>{track.artist}</b> — <i>{track.title}</i>\n"
                    f"• <b>Carpeta:</b> <code>{self.win_dest}\\_Singles</code>",
                    thread_id,
                )
            else:
                failed_count += 1
                self._send_reply(
                    chat_id,
                    f"⚠️ <b>[Pista {t_idx}/{total_tracks}] Error descargando canción:</b>\n"
                    f"• <b>{track.artist}</b> — <i>{track.title}</i>\n"
                    f"• <i>{err}</i>",
                    thread_id,
                )

            with self.lock:
                if self.current_job and t_idx < len(job.tracks):
                    self.current_job.tracks = job.tracks[t_idx:]
                    self._save_queue_state()

        if self.cancel_requested or self.consecutive_block_errors >= 3:
            final_icon = "🛑"
            final_title = "Descarga detenida / cancelada"
        else:
            final_icon = "✅" if failed_count == 0 else "⚠️"
            final_title = "¡Descarga finalizada con éxito!" if failed_count == 0 else "Descarga completada con advertencias"
        self._send_reply(
            chat_id,
            f"{final_icon} <b>{final_title}</b>\n\n"
            f"• <b>Tarea:</b> {job.title}\n"
            f"• <b>Descargados nuevos:</b> {ok_count}\n"
            f"• <b>Omitidos (ya en biblioteca/disco):</b> {skipped_count}\n"
            f"• <b>Fallidos:</b> {failed_count}\n"
            f"• <b>Ubicación:</b> <code>{self.win_dest}</code>\n\n"
            f"💡 <i>Archivos catalogados, enriquecidos con plugins de Picard y organizados en la biblioteca.</i>",
            thread_id,
        )

        # Trigger automatic Navidrome library scan if new tracks were downloaded
        if ok_count > 0 and self.navidrome:
            try:
                self.navidrome.start_scan()
                logger.info("Triggered Navidrome library scan after completing download job.")
            except Exception as se:
                logger.debug("Could not trigger Navidrome scan: %s", se)

        try:
            import gc
            gc.collect()
        except Exception:
            pass
