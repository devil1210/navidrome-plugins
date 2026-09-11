#!/usr/bin/env python3
"""
YouTube Music Discoverer for personalized recommendations and new releases.
"""

import json
import logging
import re
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

import sys
from ytmusicapi import YTMusic

if TYPE_CHECKING:
    from .navidrome import NavidromeClient

logger = logging.getLogger("listenbrainz_notifier")


def _get_ytmusic_cls():
    notifier_mod = sys.modules.get("notifier")
    if notifier_mod and hasattr(notifier_mod, "YTMusic"):
        return getattr(notifier_mod, "YTMusic")
    return YTMusic


class YouTubeMusicDiscoverer:
    """
    Connects to YouTube Music using session cookies,
    extracts personalized recommendations ('Albums for you', 'Quick picks')
    and fresh new releases ('New releases' / explore),
    resolves them to downloadable albums/playlists,
    and filters out anything already present in the Navidrome library or local disk.
    """

    def __init__(
        self,
        cookie_file: Optional[Union[str, Path]] = None,
        music_dir: Optional[Union[str, Path]] = None,
    ):
        self.cookie_file = Path(cookie_file) if cookie_file else None
        self.music_dir = Path(music_dir) if music_dir else None
        self.yt: Optional[YTMusic] = None
        self.is_authenticated = False
        self.account_name = ""
        self._lock = threading.Lock()
        self._album_cache: Dict[str, Optional[Dict[str, Any]]] = {}
        self._init_client()

    def _init_client(self):
        """Initializes the YTMusic client with browser auth if cookie is valid, or falls back to public API."""
        yt_cls = _get_ytmusic_cls()
        if self.cookie_file and self.cookie_file.is_file():
            try:
                headers = self._build_browser_headers(self.cookie_file)
                if headers:
                    self.yt = yt_cls(auth=json.dumps(headers))
                    self.is_authenticated = True
                    try:
                        acc = self.yt.get_account_info()
                        self.account_name = acc.get("accountName", "") or acc.get("channelHandle", "")
                        logger.info("YouTube Music Discoverer autenticado como '%s'", self.account_name)
                    except Exception:
                        self.account_name = "Usuario"
                        logger.info("YouTube Music Discoverer autenticado con cookies de sesión")
                    return
            except Exception as e:
                logger.warning("No se pudo autenticar YTMusic con cookies (%s). Usando modo público...", e)

        # Fallback to unauthenticated YTMusic
        self.yt = yt_cls()
        self.is_authenticated = False

    @staticmethod
    def _build_browser_headers(cookie_path: Path) -> Optional[Dict[str, str]]:
        """Parses Netscape cookie file and computes SAPISIDHASH for YouTube Music."""
        try:
            cookies = []
            sapisid = ""
            with open(cookie_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split("\t")
                    if len(parts) >= 7 and "youtube.com" in parts[0]:
                        c_name = parts[5].strip()
                        c_val = parts[6].strip()
                        cookies.append(f"{c_name}={c_val}")
                        if c_name in ("__Secure-3PAPISID", "__Secure-1PAPISID", "SAPISID"):
                            if not sapisid or c_name == "__Secure-3PAPISID":
                                sapisid = c_val

            if not cookies:
                return None

            cookie_header = "; ".join(cookies)
            origin = "https://music.youtube.com"

            if not sapisid:
                try:
                    from ytmusicapi.helpers import sapisid_from_cookie
                    sapisid = sapisid_from_cookie(cookie_header)
                except Exception:
                    pass

            if not sapisid:
                return None

            from ytmusicapi.helpers import get_authorization
            auth_header = get_authorization(f"{sapisid} {origin}")

            return {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "*/*",
                "Content-Type": "application/json",
                "Origin": origin,
                "x-origin": origin,
                "Authorization": auth_header,
                "Cookie": cookie_header,
                "x-goog-authuser": "0",
            }
        except Exception as e:
            logger.debug("Error procesando cookie para YTMusic: %s", e)
            return None

    def _get_album_details(self, browse_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            if browse_id in self._album_cache:
                return self._album_cache[browse_id]
        try:
            data = self.yt.get_album(browse_id)
            with self._lock:
                self._album_cache[browse_id] = data
            return data
        except Exception as e:
            logger.warning("Error obteniendo detalles del álbum %s: %s", browse_id, e)
            with self._lock:
                self._album_cache[browse_id] = None
            return None

    def get_recommendations(
        self,
        navidrome: Optional["NavidromeClient"] = None,
        limit: int = 8,
    ) -> List[Dict[str, Any]]:
        """
        Retrieves personalized recommendations from 'Albums for you', 'New releases',
        and 'Quick picks', filtering out anything already in Navidrome or on disk.
        """
        if not self.yt:
            return []

        candidates = []
        try:
            home = self.yt.get_home(limit=15)
        except Exception as e:
            logger.error("Error consultando home en YouTube Music: %s", e)
            home = []

        seen_keys = set()

        for section in home:
            sec_title = section.get("title", "")
            is_album_section = any(k in sec_title.lower() for k in ["album", "release", "novedad", "para ti", "for you"])
            is_track_section = any(k in sec_title.lower() for k in ["quick", "selecc", "video", "picks"])

            if not is_album_section and not is_track_section:
                continue

            for item in section.get("contents", []):
                bid = item.get("browseId")
                vid = item.get("videoId")
                title = item.get("title", "").strip()
                if not title:
                    continue

                artists_list = item.get("artists", [])
                if isinstance(artists_list, list):
                    artist = ", ".join([a.get("name", "") for a in artists_list if isinstance(a, dict) and a.get("name")])
                else:
                    artist = str(artists_list or "")

                key = f"{title.lower()}:{artist.lower()}"
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                thumbs = item.get("thumbnails", [])
                thumb_url = thumbs[-1].get("url") if thumbs else None
                if thumb_url:
                    thumb_url = re.sub(r"=w\d+-h\d+.*", "=w600-h600-l90-rj", thumb_url)

                if bid and (bid.startswith("MPREb_") or is_album_section):
                    candidates.append({
                        "kind": "album",
                        "title": title,
                        "artist": artist,
                        "browse_id": bid,
                        "cover_url": thumb_url,
                        "section": sec_title,
                    })
                elif vid:
                    candidates.append({
                        "kind": "track",
                        "title": title,
                        "artist": artist,
                        "video_id": vid,
                        "cover_url": thumb_url,
                        "section": sec_title,
                    })

        return self._filter_and_resolve_candidates(candidates, navidrome=navidrome, limit=limit)

    def get_new_releases(
        self,
        navidrome: Optional["NavidromeClient"] = None,
        limit: int = 8,
    ) -> List[Dict[str, Any]]:
        """
        Retrieves fresh new releases from artists the user listens to or trending explore,
        filtering out anything already in Navidrome or on disk.
        """
        if not self.yt:
            return []

        candidates = []
        seen_keys = set()

        # 1. From official New Releases Albums & Singles catalog (FEmusic_new_releases_albums)
        # Directly corresponds to https://music.youtube.com/new_releases/albums
        try:
            raw = self.yt._send_request("browse", {"browseId": "FEmusic_new_releases_albums"})
            section_list = raw.get("contents", {}).get("singleColumnBrowseResultsRenderer", {}).get("tabs", [{}])[0].get("tabRenderer", {}).get("content", {}).get("sectionListRenderer", {}).get("contents", [])
            for sec in section_list:
                grid = sec.get("gridRenderer", {}) or sec.get("musicGridRenderer", {})
                if not grid:
                    continue
                for it in grid.get("items", []):
                    renderer = it.get("musicTwoRowItemRenderer", {})
                    if not renderer:
                        continue
                    title = renderer.get("title", {}).get("runs", [{}])[0].get("text", "").strip()
                    bid = renderer.get("navigationEndpoint", {}).get("browseEndpoint", {}).get("browseId", "")
                    if not title or not bid:
                        continue
                    sub_runs = renderer.get("subtitle", {}).get("runs", [])
                    kind_text = ""
                    artist = ""
                    for r in sub_runs:
                        t = r.get("text", "").strip()
                        if not t or t == "•":
                            continue
                        if t.lower() in ["single", "álbum", "album", "ep"]:
                            kind_text = t
                        elif not t.isdigit() and not artist:
                            artist = t

                    key = f"{title.lower()}:{artist.lower()}"
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)

                    thumbs = renderer.get("thumbnailRenderer", {}).get("musicThumbnailRenderer", {}).get("thumbnail", {}).get("thumbnails", [])
                    thumb_url = thumbs[-1].get("url") if thumbs else None
                    if thumb_url:
                        thumb_url = re.sub(r"=w\d+-h\d+.*", "=w600-h600-l90-rj", thumb_url)

                    candidates.append({
                        "kind": "album",
                        "title": title,
                        "artist": artist or "Desconocido",
                        "browse_id": bid,
                        "cover_url": thumb_url,
                        "section": f"Nuevos Lanzamientos ({kind_text or 'Álbum'})",
                    })
        except Exception as e:
            logger.warning("Error consultando FEmusic_new_releases_albums: %s", e)

        # 2. Fallback: Explore new_releases if catalog empty
        if not candidates:
            try:
                explore = self.yt.get_explore()
                nr = explore.get("new_releases", [])
                for item in nr:
                    bid = item.get("browseId")
                    title = item.get("title", "").strip()
                    if not title or not bid:
                        continue
                    artists_list = item.get("artists", [])
                    if isinstance(artists_list, list):
                        artist = ", ".join([a.get("name", "") for a in artists_list if isinstance(a, dict) and a.get("name")])
                    else:
                        artist = str(artists_list or "")

                    key = f"{title.lower()}:{artist.lower()}"
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)

                    thumbs = item.get("thumbnails", [])
                    thumb_url = thumbs[-1].get("url") if thumbs else None
                    if thumb_url:
                        thumb_url = re.sub(r"=w\d+-h\d+.*", "=w600-h600-l90-rj", thumb_url)

                    candidates.append({
                        "kind": "album",
                        "title": title,
                        "artist": artist,
                        "browse_id": bid,
                        "cover_url": thumb_url,
                        "section": "Nuevos Lanzamientos",
                    })
            except Exception as e:
                logger.warning("Error consultando explore en YouTube Music: %s", e)

        # 3. Also check Home New releases section
        try:
            home = self.yt.get_home(limit=12)
            for section in home:
                sec_title = section.get("title", "")
                if any(k in sec_title.lower() for k in ["new release", "lanzamiento", "novedad"]):
                    for item in section.get("contents", []):
                        bid = item.get("browseId")
                        title = item.get("title", "").strip()
                        if not title or not bid:
                            continue
                        artists_list = item.get("artists", [])
                        if isinstance(artists_list, list):
                            artist = ", ".join([a.get("name", "") for a in artists_list if isinstance(a, dict) and a.get("name")])
                        else:
                            artist = str(artists_list or "")

                        key = f"{title.lower()}:{artist.lower()}"
                        if key in seen_keys:
                            continue
                        seen_keys.add(key)

                        thumbs = item.get("thumbnails", [])
                        thumb_url = thumbs[-1].get("url") if thumbs else None
                        if thumb_url:
                            thumb_url = re.sub(r"=w\d+-h\d+.*", "=w600-h600-l90-rj", thumb_url)

                        candidates.append({
                            "kind": "album",
                            "title": title,
                            "artist": artist,
                            "browse_id": bid,
                            "cover_url": thumb_url,
                            "section": "Novedades Personalizadas",
                        })
        except Exception as e:
            logger.debug("Error revisando home new releases: %s", e)

        return self._filter_and_resolve_candidates(candidates, navidrome=navidrome, limit=limit)

    def _filter_and_resolve_candidates(
        self,
        candidates: List[Dict[str, Any]],
        navidrome: Optional["NavidromeClient"] = None,
        limit: int = 8,
    ) -> List[Dict[str, Any]]:
        clean_results: List[Dict[str, Any]] = []

        def sanitize(name: str) -> str:
            clean = re.sub(r'[\\/*?:"<>|]', "", name).strip()
            clean = re.sub(r"\s+", " ", clean)
            return clean or "Desconocido"

        for item in candidates:
            if len(clean_results) >= limit:
                break

            title = item["title"]
            artist = item["artist"]
            kind = item["kind"]

            # If it's a track recommendation, resolve to its parent album to download the complete album
            if kind == "track":
                try:
                    s_hits = self.yt.search(f"{title} {artist}", filter="songs", limit=1)
                    if s_hits and isinstance(s_hits, list) and s_hits[0].get("album"):
                        alb_meta = s_hits[0]["album"]
                        alb_bid = alb_meta.get("id")
                        if alb_bid:
                            item["kind"] = "album"
                            item["source_track"] = title
                            item["title"] = alb_meta.get("name") or title
                            item["browse_id"] = alb_bid
                            kind = "album"
                            title = item["title"]
                except Exception as e:
                    logger.debug("Error buscando álbum correspondiente para '%s': %s", title, e)

            # 1. Filter local disk
            if self.music_dir and self.music_dir.is_dir():
                if kind == "album":
                    folder_name = sanitize(title)
                    if (self.music_dir / folder_name).is_dir():
                        logger.debug("Recomendación omitida (ya en disco): %s", title)
                        continue
                else:
                    singles_folder = self.music_dir / "_Singles"
                    if singles_folder.is_dir():
                        clean_t = sanitize(title).lower()
                        if any(clean_t in f.name.lower() for f in singles_folder.iterdir() if f.is_file()):
                            logger.debug("Recomendación de pista omitida (ya en disco): %s", title)
                            continue

            # 2. Filter Navidrome library
            if navidrome:
                try:
                    if kind == "album":
                        matched = navidrome.find_album_match(title, artist)
                        if matched:
                            logger.debug("Recomendación omitida (ya en Navidrome): %s por %s", title, artist)
                            continue
                    else:
                        matched = navidrome.find_best_match(title, artist)
                        if matched:
                            logger.debug("Pista recomendada omitida (ya en Navidrome): %s por %s", title, artist)
                            continue
                except Exception as ex:
                    logger.warning("Error comprobando recomendación en Navidrome: %s", ex)

            # 3. Resolve details
            if kind == "album":
                bid = item.get("browse_id")
                if bid:
                    alb_data = self._get_album_details(bid)
                    if alb_data:
                        playlist_id = alb_data.get("audioPlaylistId")
                        if playlist_id:
                            item["url"] = f"https://music.youtube.com/playlist?list={playlist_id}"
                            item["playlist_id"] = playlist_id
                            item["year"] = str(alb_data.get("year", ""))
                            item["track_count"] = alb_data.get("trackCount", 0)
                            item["tracks"] = [
                                t.get("title") for t in alb_data.get("tracks", []) if t.get("title")
                            ]
                        else:
                            continue
                    else:
                        continue
                else:
                    continue
            else:
                vid = item.get("video_id")
                if vid:
                    item["url"] = f"https://music.youtube.com/watch?v={vid}"
                    item["year"] = ""
                    item["track_count"] = 1
                    item["tracks"] = [title]
                else:
                    continue

            clean_results.append(item)

        return clean_results
