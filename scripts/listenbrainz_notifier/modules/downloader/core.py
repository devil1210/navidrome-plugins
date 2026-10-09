#!/usr/bin/env python3
"""
Server-side Music Downloader (yt-dlp engine).
Replicates MediaHuman folder structure and prepares audio files for MusicBrainz Picard.
"""

import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from ..models import ResolvedAlbum, ResolvedTrack
from .tagger import (
    KNOWN_MULTIWORD_ARTISTS,
    cleanup_loose_images,
    fetch_official_cover,
    process_album_cover,
    process_album_metadata,
    quick_metadata_lookup,
    sanitize_downloaded_files,
    split_artists,
    verify_album_integrity,
)

logger = logging.getLogger("listenbrainz_notifier")

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


class MusicDownloader:
    """
    Downloads albums and tracks directly from YouTube Music into the server's download
    directory (/media/downloads/music -> M:\\downloads\\music), replicating MediaHuman's
    folder hierarchy, audio format (M4A Original), and metadata for MusicBrainz Picard.
    """

    KNOWN_MULTIWORD_ARTISTS = KNOWN_MULTIWORD_ARTISTS
    fetch_official_cover = staticmethod(fetch_official_cover)
    _process_album_cover = staticmethod(process_album_cover)
    _cleanup_loose_images = staticmethod(cleanup_loose_images)
    _quick_metadata_lookup = staticmethod(quick_metadata_lookup)
    split_artists = staticmethod(split_artists)
    _process_album_metadata = staticmethod(process_album_metadata)
    _sanitize_downloaded_files = staticmethod(sanitize_downloaded_files)
    verify_album_integrity = staticmethod(verify_album_integrity)

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
        self.progress_callback: Optional[Any] = None

        self.cookies_list: List[Path] = []
        raw_cookies: List[Path] = []
        if cookies_list:
            raw_cookies = [Path(p) for p in cookies_list if os.path.isfile(p)]
        elif cookies_path:
            p = Path(cookies_path)
            if p.is_dir():
                raw_cookies = sorted([f for f in p.glob("*.txt") if f.is_file()])
            elif p.is_file():
                raw_cookies = [p]
        # Security safeguard: never allow personal account or discovery cookies in download engine
        filtered_raw = [
            cp for cp in raw_cookies
            if not any(k in cp.name.lower() for k in ("discovery", "personal"))
        ]
        if len(filtered_raw) < len(raw_cookies):
            ignored = [cp.name for cp in raw_cookies if cp not in filtered_raw]
            logger.warning(
                "Seguridad: se excluyeron cookies de cuenta personal/descubrimiento del motor de descarga: %s",
                ignored,
            )
        raw_cookies = filtered_raw

        valid_auth_cookies = [cp for cp in raw_cookies if MusicDownloader.is_valid_cookie_file(cp)]
        if valid_auth_cookies:
            self.cookies_list = valid_auth_cookies
            discarded = set(raw_cookies) - set(valid_auth_cookies)
            if discarded:
                logger.info(
                    "Filtrando cookies: se descartaron %d archivo(s) sin sesión activa (guest/vacías): %s",
                    len(discarded),
                    [p.name for p in discarded],
                )
        else:
            self.cookies_list = raw_cookies
        self.cookie_index = 0

    @staticmethod
    def is_valid_cookie_file(p: Union[str, Path]) -> bool:
        """Verifies if a cookie file contains actual authenticated YouTube session tokens."""
        try:
            path = Path(p)
            if not path.is_file() or path.stat().st_size < 1000:
                return False
            content = path.read_text(encoding="utf-8", errors="ignore")
            auth_markers = ["__Secure-3PSID", "__Secure-1PSID", "LOGIN_INFO", "SAPISID", "SSID", "HSID"]
            return any(m in content for m in auth_markers)
        except Exception:
            return False

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
        s = re.sub(r'[/\\|]', '-', name)
        s = re.sub(r'[<>:"?*]', '_', s)
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
            "trim_file_name": 140,
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
            "source_address": "0.0.0.0",
        }

        if self.sleep_interval > 0:
            opts["sleep_interval"] = self.sleep_interval
            opts["max_sleep_interval"] = self.max_sleep_interval

        current_cookie = self.get_current_cookie()
        if current_cookie:
            opts["cookiefile"] = current_cookie

        opts["js_runtimes"] = {"quickjs": {}}

        if getattr(self, "progress_callback", None):
            opts["progress_hooks"] = [self._yt_dlp_progress_hook]

        return opts

    def _yt_dlp_progress_hook(self, d: Dict[str, Any]):
        cb = getattr(self, "progress_callback", None)
        if cb:
            try:
                cb(d)
            except Exception as e:
                logger.debug("Error in yt-dlp progress callback: %s", e)

    def get_album_tracklist(self, album: ResolvedAlbum) -> List[Dict[str, Any]]:
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
            "source_address": "0.0.0.0",
            "js_runtimes": {"quickjs": {}},
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
        if album.tracks and len(album.tracks) > 1:
            return album.tracks
        return album.tracks or []

    def download_album_tracks(self, album: ResolvedAlbum, track_indices: List[int]) -> Tuple[bool, str]:
        if not track_indices:
            return True, "No se indicaron pistas a descargar"

        if not album.url or "youtube.com/search" in album.url:
            logger.error("URL de descarga rechazada por ser consulta de búsqueda: %s", album.url)
            return False, "URL de búsqueda genérica rechazada para descarga"

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

            final_folder = target_folder
            try:
                try:
                    from ..picard_engine import PicardEngine
                except (ImportError, ValueError):
                    from modules.picard_engine import PicardEngine
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
        if not album.url or "youtube.com/search" in album.url:
            logger.error("URL de álbum rechazada por ser consulta de búsqueda: %s", album.url)
            return False, "URL de búsqueda genérica rechazada para descarga de álbum"

        if album.album_name and re.match(r"^álbum\s+de\s+\d+", album.album_name, re.IGNORECASE):
            logger.error("Nombre de álbum rechazado por ser placeholder numérico ('%s')", album.album_name)
            return False, "Nombre de álbum genérico/placeholder rechazado"

        folder_name = self.sanitize_name(album.album_name)
        target_folder = self.download_dir / folder_name

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

            audio_files = [f for f in target_folder.iterdir() if f.is_file() and f.suffix.lower() in ('.m4a', '.mp3', '.opus', '.webm', '.flac')]

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

            if len(self.cookies_list) > 1:
                self.rotate_cookie()

            final_folder = target_folder
            try:
                try:
                    from ..picard_engine import PicardEngine
                except (ImportError, ValueError):
                    from modules.picard_engine import PicardEngine
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
