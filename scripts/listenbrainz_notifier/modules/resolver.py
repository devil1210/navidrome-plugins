#!/usr/bin/env python3
"""
YouTube Music resolvers for tracks, albums, playlists, and artist discographies.
"""

import concurrent.futures
import logging
import re
import sys
import threading
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import requests
from ytmusicapi import YTMusic

from .models import MissingTrack, ResolutionResult, ResolvedAlbum, ResolvedTrack

logger = logging.getLogger("listenbrainz_notifier")


def _get_ytmusic_cls():
    notifier_mod = sys.modules.get("notifier")
    if notifier_mod and hasattr(notifier_mod, "YTMusic"):
        return getattr(notifier_mod, "YTMusic")
    return YTMusic


class YouTubeMusicResolver:
    """Resolves missing tracks to official albums or tracks via YouTube Music."""

    def __init__(self, max_workers: int = 5):
        yt_cls = _get_ytmusic_cls()
        self.yt = yt_cls()
        self.max_workers = max_workers
        self._lock = threading.Lock()
        # In-memory cache for album metadata to avoid redundant calls
        self._album_cache: Dict[str, Optional[str]] = {}

    def _get_audio_playlist_id(self, album_browse_id: str) -> Optional[str]:
        """Fetches full album details to retrieve the audioPlaylistId (OLAK5uy_...)."""
        with self._lock:
            if album_browse_id in self._album_cache:
                return self._album_cache[album_browse_id]

        try:
            album_data = self.yt.get_album(album_browse_id)
            playlist_id = album_data.get("audioPlaylistId")
            with self._lock:
                self._album_cache[album_browse_id] = playlist_id
            return playlist_id
        except Exception as e:
            logger.warning("Could not fetch details for album %s: %s", album_browse_id, e)
            with self._lock:
                self._album_cache[album_browse_id] = None
            return None

    def _resolve_single_track(
        self,
        track: MissingTrack,
        fallback_to_track: bool = True,
    ) -> Dict[str, Any]:
        query = f"{track.title} {track.artist}".strip()
        try:
            search_results = self.yt.search(query, filter="songs", limit=1)
        except Exception as e:
            logger.error("Error searching for '%s': %s", query, e)
            return {"status": "unresolved", "track": track}

        if not search_results:
            logger.warning("No results found for '%s'", query)
            return {"status": "unresolved", "track": track}

        top_hit = search_results[0]
        album_info = top_hit.get("album")
        album_browse_id = album_info.get("id") if album_info else None

        thumb_url = None
        thumbs = top_hit.get("thumbnails", [])
        if thumbs:
            raw_thumb = thumbs[-1].get("url", "")
            if raw_thumb:
                thumb_url = re.sub(r"=w\d+-h\d+.*", "=w1200-h1200-l90-rj", raw_thumb)

        if album_browse_id:
            audio_playlist_id = self._get_audio_playlist_id(album_browse_id)
            if audio_playlist_id:
                playlist_url = f"https://music.youtube.com/playlist?list={audio_playlist_id}"
                album_name = album_info.get("name", "Unknown Album")
                artists = top_hit.get("artists", [])
                artist_name = artists[0].get("name", track.artist) if artists else track.artist
                resolved_song_title = top_hit.get("title", track.title)
                return {
                    "status": "album",
                    "audio_playlist_id": audio_playlist_id,
                    "url": playlist_url,
                    "album_name": album_name,
                    "artist_name": artist_name,
                    "track_title": track.title,
                    "resolved_track_title": resolved_song_title,
                    "cover_url": thumb_url,
                }

        if fallback_to_track and top_hit.get("videoId"):
            video_id = top_hit["videoId"]
            video_url = f"https://music.youtube.com/watch?v={video_id}"
            track_title = top_hit.get("title", track.title)
            artists = top_hit.get("artists", [])
            artist_name = artists[0].get("name", track.artist) if artists else track.artist
            return {
                "status": "track",
                "url": video_url,
                "title": track_title,
                "artist": artist_name,
                "cover_url": thumb_url,
            }

        return {"status": "unresolved", "track": track}

    def resolve(
        self,
        tracks: List[MissingTrack],
        fallback_to_track: bool = True,
    ) -> ResolutionResult:
        """
        Resolves a list of missing tracks in parallel, deduplicating albums.
        """
        result = ResolutionResult()
        albums_by_id: Dict[str, ResolvedAlbum] = {}

        if not tracks:
            return result

        logger.info("Resolving %d tracks with YouTube Music (concurrency: %d)...", len(tracks), self.max_workers)

        if len(tracks) <= 2:
            resolved_items = [(idx, self._resolve_single_track(t, fallback_to_track)) for idx, t in enumerate(tracks)]
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = [executor.submit(self._resolve_single_track, t, fallback_to_track) for t in tracks]
                resolved_items = [(idx, f.result()) for idx, f in enumerate(futures)]

        for idx, item in resolved_items:
            status = item.get("status")
            if status == "album":
                pid = item["audio_playlist_id"]
                track_title = item["track_title"]
                resolved_title = item.get("resolved_track_title") or track_title
                if pid in albums_by_id:
                    albums_by_id[pid].tracks.append(track_title)
                    if resolved_title not in albums_by_id[pid].resolved_tracks:
                        albums_by_id[pid].resolved_tracks.append(resolved_title)
                    albums_by_id[pid].first_index = min(albums_by_id[pid].first_index, idx)
                    if not albums_by_id[pid].cover_url and item.get("cover_url"):
                        albums_by_id[pid].cover_url = item.get("cover_url")
                    logger.debug("  -> Added to existing album: '%s' by %s", item["album_name"], item["artist_name"])
                else:
                    resolved_album = ResolvedAlbum(
                        url=item["url"],
                        album_name=item["album_name"],
                        artist_name=item["artist_name"],
                        tracks=[track_title],
                        resolved_tracks=[resolved_title],
                        first_index=idx,
                        cover_url=item.get("cover_url"),
                    )
                    albums_by_id[pid] = resolved_album
                    logger.debug("  -> Found album: '%s' by %s -> %s", item["album_name"], item["artist_name"], item["url"])
            elif status == "track":
                result.tracks.append(
                    ResolvedTrack(
                        url=item["url"],
                        title=item["title"],
                        artist=item["artist"],
                        first_index=idx,
                        cover_url=item.get("cover_url"),
                    )
                )
                logger.debug("  -> Fallback track URL: %s", item["url"])
            else:
                result.unresolved.append(item["track"])

        albums = list(albums_by_id.values())
        # Sort albums:
        # 1. Primary: Most missing tracks satisfied (-len(a.tracks))
        # 2. Secondary: Order of appearance in playlist (a.first_index)
        albums.sort(key=lambda a: (-len(a.tracks), a.first_index))
        result.albums = albums
        result.tracks.sort(key=lambda t: t.first_index)
        return result


class UniversalResolver:
    """
    Resolves arbitrary user inputs (URLs, artist discographies, albums, songs)
    into structured collections of ResolvedAlbum and ResolvedTrack.
    """

    def __init__(self, yt: Optional[YTMusic] = None):
        yt_cls = _get_ytmusic_cls()
        self.yt = yt or yt_cls()
        self._album_cache: Dict[str, Optional[Dict[str, Any]]] = {}
        self._lock = threading.Lock()

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
            logger.warning("Error fetching album details for %s: %s", browse_id, e)
            with self._lock:
                self._album_cache[browse_id] = None
            return None

    def resolve_url(self, url: str) -> Dict[str, Any]:
        """
        Parses and resolves YouTube / YouTube Music URLs.
        Supports:
        - Playlists / Albums (list=...)
        - Artist channels (/channel/... or /@...)
        - Songs (/watch?v=... or youtu.be/...)
        """
        parsed = urlparse(url.strip())
        qs = parse_qs(parsed.query)

        # 1. Check for playlist/album parameter
        list_id = qs.get("list", [None])[0]
        if list_id and not list_id.startswith("RD"):
            # If it's an official album playlist (OLAK5uy_...)
            if list_id.startswith("OLAK5uy_"):
                album_url = f"https://music.youtube.com/playlist?list={list_id}"
                try:
                    pl = self.yt.get_playlist(list_id)
                    title = pl.get("title") or "Álbum de YouTube"
                    author_obj = pl.get("author")
                    author = author_obj.get("name", "Various Artists") if isinstance(author_obj, dict) else (author_obj or "Various Artists")
                    tracks: List[str] = []
                    if pl.get("tracks"):
                        first_trk = pl["tracks"][0]
                        if first_trk.get("artists"):
                            author = first_trk["artists"][0].get("name", author)
                        tracks = [t.get("title") for t in pl["tracks"] if t.get("title")]

                    thumb_url = None
                    thumbs = pl.get("thumbnails", [])
                    if (not thumbs or not isinstance(thumbs, list)) and pl.get("tracks"):
                        thumbs = pl["tracks"][0].get("thumbnails", [])
                    if not thumbs or not isinstance(thumbs, list):
                        try:
                            s_res = self.yt.search(f"{title} {author}", filter="albums", limit=1)
                            if isinstance(s_res, list) and s_res and isinstance(s_res[0], dict) and s_res[0].get("thumbnails"):
                                thumbs = s_res[0]["thumbnails"]
                        except Exception:
                            pass
                    if isinstance(thumbs, list) and thumbs:
                        last_t = thumbs[-1]
                        if isinstance(last_t, dict):
                            raw_u = last_t.get("url", "")
                            if isinstance(raw_u, str) and raw_u:
                                thumb_url = re.sub(r"=w\d+-h\d+.*", "=w1200-h1200-l90-rj", raw_u)
                                thumb_url = re.sub(r"=s\d+.*", "=s1200-c-k-c0x00ffffff-no-rj", thumb_url)

                    resolved = ResolvedAlbum(
                        url=album_url,
                        album_name=title,
                        artist_name=author,
                        tracks=tracks,
                        is_single=False,
                        cover_url=thumb_url,
                    )
                    return {
                        "status": "success",
                        "kind": "album",
                        "title": title,
                        "artist": author,
                        "albums": [resolved],
                        "tracks": [],
                        "message": f"Álbum: <b>{title}</b> ({author})",
                    }
                except Exception as e:
                    logger.warning("Error resolviendo álbum con ytmusicapi (%s): %s", list_id, e)
                    resolved = ResolvedAlbum(
                        url=album_url,
                        album_name=f"Álbum {list_id}",
                        artist_name="YouTube",
                        tracks=[],
                        is_single=False,
                    )
                    return {
                        "status": "success",
                        "kind": "album",
                        "title": f"Álbum {list_id}",
                        "artist": "YouTube",
                        "albums": [resolved],
                        "tracks": [],
                        "message": f"Álbum: <code>{album_url}</code>",
                    }
            else:
                # User or curated playlist (PL..., VL..., etc.)
                # Resuelve cada canción de la lista a su correspondiente álbum oficial
                try:
                    pl = self.yt.get_playlist(list_id)
                    title = pl.get("title") or "Playlist de YouTube"
                    author_obj = pl.get("author")
                    author = author_obj.get("name", "YouTube") if isinstance(author_obj, dict) else (author_obj or "YouTube")
                    pl_tracks = pl.get("tracks", [])

                    missing_tracks: List[MissingTrack] = []
                    for t in pl_tracks:
                        t_title = t.get("title")
                        artists = t.get("artists", [])
                        t_artist = artists[0].get("name", "") if artists else ""
                        if t_title:
                            missing_tracks.append(MissingTrack(title=t_title, artist=t_artist))

                    if not missing_tracks:
                        return {"status": "error", "message": f"La lista de reproducción '{title}' no contiene canciones disponibles."}

                    resolver = YouTubeMusicResolver()
                    res = resolver.resolve(missing_tracks, fallback_to_track=True)
                    return {
                        "status": "success",
                        "kind": "playlist",
                        "title": title,
                        "artist": author,
                        "albums": res.albums,
                        "tracks": res.tracks,
                        "message": f"Lista <b>{title}</b>: {len(res.albums)} álbum(es)/sencillo(s) y {len(res.tracks)} canción(es) identificados.",
                    }
                except Exception as e:
                    logger.error("Error resolviendo lista de reproducción %s: %s", list_id, e)
                    return {"status": "error", "message": f"Error resolviendo lista de reproducción: {e}"}

        # 2. Check for artist channel
        if "/channel/" in parsed.path:
            channel_id = parsed.path.split("/channel/")[1].split("/")[0]
            return self.resolve_artist_discography(channel_id)
        if "/@" in parsed.path:
            handle = parsed.path.split("/@")[1].split("/")[0]
            return self.resolve_artist_discography(f"@{handle}")

        # 3. Check for single video/song
        video_id = qs.get("v", [None])[0]
        if not video_id and "youtu.be" in parsed.netloc:
            video_id = parsed.path.lstrip("/").split("/")[0].split("?")[0]

        if video_id:
            try:
                s = self.yt.get_song(video_id)
                details = s.get("videoDetails", {})
                title = details.get("title") or "Canción de YouTube"
                artist = details.get("author") or "YouTube"
                track_url = f"https://music.youtube.com/watch?v={video_id}"

                thumb_url = None
                t_thumbs = details.get("thumbnail", {}).get("thumbnails", [])
                if isinstance(t_thumbs, list) and t_thumbs:
                    last_t = t_thumbs[-1]
                    if isinstance(last_t, dict):
                        raw_u = last_t.get("url", "")
                        if isinstance(raw_u, str) and raw_u:
                            thumb_url = re.sub(r"=w\d+-h\d+.*", "=w1200-h1200-l90-rj", raw_u)
                            thumb_url = re.sub(r"=s\d+.*", "=s1200-c-k-c0x00ffffff-no-rj", thumb_url)

                resolved_trk = ResolvedTrack(
                    url=track_url,
                    title=title,
                    artist=artist,
                    cover_url=thumb_url,
                )
                return {
                    "status": "success",
                    "kind": "track",
                    "title": title,
                    "artist": artist,
                    "albums": [],
                    "tracks": [resolved_trk],
                    "message": f"Canción: <b>{title}</b> de <b>{artist}</b>",
                }
            except Exception as e:
                logger.warning("Error fetching song details (%s): %s", video_id, e)
                track_url = f"https://music.youtube.com/watch?v={video_id}"
                resolved_trk = ResolvedTrack(
                    url=track_url,
                    title=f"Video {video_id}",
                    artist="YouTube",
                )
                return {
                    "status": "success",
                    "kind": "track",
                    "title": f"Video {video_id}",
                    "artist": "YouTube",
                    "albums": [],
                    "tracks": [resolved_trk],
                    "message": f"Canción: <code>{track_url}</code>",
                }

        return {"status": "error", "message": f"URL no reconocida o formato no soportado: <code>{url}</code>"}

    @staticmethod
    def _resolve_handle_to_channel_id(handle: str) -> Optional[str]:
        """Resolves a YouTube @handle (e.g. @kokecantante) to its canonical channelId (UC...)."""
        h = handle.lstrip("@").strip()
        url = f"https://www.youtube.com/@{h}"
        try:
            r = requests.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                allow_redirects=True,
                timeout=10,
            )
            if r.status_code == 200:
                for pattern in [
                    r'<meta itemprop="channelId" content="(UC[a-zA-Z0-9_-]+)">',
                    r'"externalId":"(UC[a-zA-Z0-9_-]+)"',
                    r'channel_id=(UC[a-zA-Z0-9_-]+)',
                    r'"canonicalBaseUrl":"/channel/(UC[a-zA-Z0-9_-]+)"',
                ]:
                    m = re.search(pattern, r.text)
                    if m:
                        return m.group(1)
        except Exception as e:
            logger.warning("No se pudo resolver el handle @%s a channelId: %s", h, e)
        return None

    def resolve_artist_discography(self, query_or_id: str) -> Dict[str, Any]:
        """
        Retrieves all studio albums and singles for an artist.
        """
        channel_id = ""
        artist_name = query_or_id
        if query_or_id.startswith("UC") and len(query_or_id) > 15:
            channel_id = query_or_id
        elif query_or_id.startswith("@"):
            cid = self._resolve_handle_to_channel_id(query_or_id)
            if cid:
                channel_id = cid
            else:
                clean_q = query_or_id.lstrip("@")
                res = self.yt.search(clean_q, filter="artists", limit=3)
                if not res:
                    res = self.yt.search(clean_q, limit=3)
                if res and res[0].get("browseId"):
                    channel_id = res[0]["browseId"]
                    artist_name = res[0].get("artist") or res[0].get("title", clean_q)
                else:
                    return {"status": "error", "message": f"No se encontró al artista <b>{query_or_id}</b> en YouTube Music."}
        else:
            try:
                res = self.yt.search(query_or_id, filter="artists", limit=3)
                if res:
                    channel_id = res[0]["browseId"]
                    artist_name = res[0].get("artist", query_or_id)
                else:
                    return {"status": "error", "message": f"No se encontró al artista <b>{query_or_id}</b> en YouTube Music."}
            except Exception as e:
                logger.error("Error searching artist '%s': %s", query_or_id, e)
                return {"status": "error", "message": f"Error buscando artista: {e}"}

        try:
            artist_data = self.yt.get_artist(channel_id)
        except Exception as e:
            logger.error("Error fetching artist details (%s): %s", channel_id, e)
            return {"status": "error", "message": f"Error obteniendo perfil del artista: {e}"}

        real_artist = artist_data.get("name") or artist_name
        albums_raw = list(artist_data.get("albums", {}).get("results", []))

        # Try to expand albums if params exist
        albums_bid = artist_data.get("albums", {}).get("browseId") or channel_id
        params = artist_data.get("albums", {}).get("params")
        if params:
            try:
                extra = self.yt.get_artist_albums(channelId=albums_bid, params=params)
                if extra:
                    existing_bids = {a.get("browseId") for a in albums_raw if a.get("browseId")}
                    for ea in extra:
                        if ea.get("browseId") and ea["browseId"] not in existing_bids:
                            albums_raw.append(ea)
                            existing_bids.add(ea["browseId"])
            except Exception as e:
                logger.debug("Error expandiendo álbumes de '%s': %s", real_artist, e)

        singles_raw = list(artist_data.get("singles", {}).get("results", []))
        singles_bid = artist_data.get("singles", {}).get("browseId") or channel_id
        singles_params = artist_data.get("singles", {}).get("params")
        if singles_params:
            try:
                extra_s = self.yt.get_artist_albums(channelId=singles_bid, params=singles_params)
                if extra_s:
                    existing_s_bids = {s.get("browseId") for s in singles_raw if s.get("browseId")}
                    for es in extra_s:
                        if es.get("browseId") and es["browseId"] not in existing_s_bids:
                            singles_raw.append(es)
                            existing_s_bids.add(es["browseId"])
            except Exception as e:
                logger.debug("Error expandiendo sencillos de '%s': %s", real_artist, e)

        resolved_albums: List[ResolvedAlbum] = []

        # 1. Convert studio albums
        for alb in albums_raw:
            alb_title = alb.get("title", "Álbum")
            pid = alb.get("audioPlaylistId") or alb.get("playlistId")
            bid = alb.get("browseId")
            year = alb.get("year")
            if not pid and bid:
                details = self._get_album_details(bid)
                if details:
                    pid = details.get("audioPlaylistId")
                    if not year:
                        year = details.get("year")
            if pid:
                thumb_url = None
                thumbs = alb.get("thumbnails", [])
                if thumbs:
                    raw_thumb = thumbs[-1].get("url", "")
                    if raw_thumb:
                        thumb_url = re.sub(r"=w\d+-h\d+.*", "=w1200-h1200-l90-rj", raw_thumb)
                resolved_albums.append(
                    ResolvedAlbum(
                        url=f"https://music.youtube.com/playlist?list={pid}",
                        album_name=alb_title,
                        artist_name=real_artist,
                        is_single=False,
                        cover_url=thumb_url,
                        year=str(year) if year else None,
                    )
                )

        # 2. Convert singles in parallel (up to 5 workers)
        def process_single(s_item: Dict[str, Any]) -> Optional[ResolvedAlbum]:
            s_title = s_item.get("title", "Single")
            pid = s_item.get("audioPlaylistId") or s_item.get("playlistId")
            bid = s_item.get("browseId")
            year = s_item.get("year")
            if not pid and bid:
                details = self._get_album_details(bid)
                if details:
                    pid = details.get("audioPlaylistId")
                    if not year:
                        year = details.get("year")
            if pid:
                thumb_url = None
                thumbs = s_item.get("thumbnails", [])
                if thumbs:
                    raw_thumb = thumbs[-1].get("url", "")
                    if raw_thumb:
                        thumb_url = re.sub(r"=w\d+-h\d+.*", "=w1200-h1200-l90-rj", raw_thumb)
                return ResolvedAlbum(
                    url=f"https://music.youtube.com/playlist?list={pid}",
                    album_name=s_title,
                    artist_name=real_artist,
                    is_single=True,
                    cover_url=thumb_url,
                    year=str(year) if year else None,
                )
            return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            single_results = list(executor.map(process_single, singles_raw))

        resolved_singles = [s for s in single_results if s is not None]

        # Combine: albums first, then singles
        all_releases = resolved_albums + resolved_singles

        if not all_releases:
            return {
                "status": "error",
                "message": f"No se encontraron álbumes ni sencillos disponibles para <b>{real_artist}</b>.",
            }

        return {
            "status": "success",
            "kind": "artist",
            "title": real_artist,
            "artist": real_artist,
            "albums": all_releases,
            "tracks": [],
            "total_albums": len(resolved_albums),
            "total_singles": len(resolved_singles),
            "message": (
                f"Discografía de <b>{real_artist}</b>:\n"
                f"• <b>Álbumes de estudio:</b> {len(resolved_albums)}\n"
                f"• <b>Sencillos y EPs:</b> {len(resolved_singles)}"
            ),
        }

    def resolve_album(self, query: str) -> Dict[str, Any]:
        """
        Searches and resolves a specific album by title.
        """
        try:
            res = self.yt.search(query, filter="albums", limit=5)
            if not res:
                return {"status": "error", "message": f"No se encontró el álbum <b>{query}</b> en YouTube Music."}

            top = res[0]
            title = top.get("title", query)
            artists = top.get("artists", [])
            artist_name = artists[0].get("name", "Unknown Artist") if artists else "Unknown Artist"

            pid = top.get("playlistId")
            bid = top.get("browseId")
            if not pid and bid:
                details = self._get_album_details(bid)
                if details:
                    pid = details.get("audioPlaylistId")

            if not pid:
                return {"status": "error", "message": f"No se pudo obtener el identificador del álbum <b>{title}</b>."}

            thumb_url = None
            thumbs = top.get("thumbnails", [])
            if isinstance(thumbs, list) and thumbs:
                last_t = thumbs[-1]
                if isinstance(last_t, dict):
                    raw_u = last_t.get("url", "")
                    if isinstance(raw_u, str) and raw_u:
                        thumb_url = re.sub(r"=w\d+-h\d+.*", "=w1200-h1200-l90-rj", raw_u)
                        thumb_url = re.sub(r"=s\d+.*", "=s1200-c-k-c0x00ffffff-no-rj", thumb_url)

            year = top.get("year")
            album = ResolvedAlbum(
                url=f"https://music.youtube.com/playlist?list={pid}",
                album_name=title,
                artist_name=artist_name,
                is_single=False,
                cover_url=thumb_url,
                year=str(year) if year else None,
            )
            return {
                "status": "success",
                "kind": "album",
                "title": title,
                "artist": artist_name,
                "albums": [album],
                "tracks": [],
                "message": f"Álbum: <i>{title}</i> de <b>{artist_name}</b>",
            }
        except Exception as e:
            logger.error("Error searching album '%s': %s", query, e)
            return {"status": "error", "message": f"Error buscando álbum: {e}"}

    def resolve_song(self, query: str) -> Dict[str, Any]:
        """
        Searches and resolves a single track.
        """
        try:
            res = self.yt.search(query, filter="songs", limit=5)
            if not res:
                return {"status": "error", "message": f"No se encontró la canción <b>{query}</b> en YouTube Music."}

            top = res[0]
            title = top.get("title", query)
            artists = top.get("artists", [])
            artist_name = artists[0].get("name", "Unknown Artist") if artists else "Unknown Artist"
            video_id = top.get("videoId")

            if not video_id:
                return {"status": "error", "message": f"No se pudo obtener el enlace de la canción <b>{title}</b>."}

            thumb_url = None
            thumbs = top.get("thumbnails", [])
            if isinstance(thumbs, list) and thumbs:
                last_t = thumbs[-1]
                if isinstance(last_t, dict):
                    raw_u = last_t.get("url", "")
                    if isinstance(raw_u, str) and raw_u:
                        thumb_url = re.sub(r"=w\d+-h\d+.*", "=w1200-h1200-l90-rj", raw_u)
                        thumb_url = re.sub(r"=s\d+.*", "=s1200-c-k-c0x00ffffff-no-rj", thumb_url)

            track = ResolvedTrack(
                url=f"https://music.youtube.com/watch?v={video_id}",
                title=title,
                artist=artist_name,
                cover_url=thumb_url,
            )
            return {
                "status": "success",
                "kind": "track",
                "title": title,
                "artist": artist_name,
                "albums": [],
                "tracks": [track],
                "message": f"Canción: <i>{title}</i> de <b>{artist_name}</b>",
            }
        except Exception as e:
            logger.error("Error searching song '%s': %s", query, e)
            return {"status": "error", "message": f"Error buscando canción: {e}"}

    def resolve_input(self, raw_input: str) -> Dict[str, Any]:
        """
        Universal router: resolves URLs, prefixes ('artista ...', 'album ...', 'cancion ...'),
        or disambiguates free text automatically.
        """
        text = raw_input.strip()
        if not text:
            return {"status": "error", "message": "Texto de búsqueda vacío."}

        # 1. URLs
        if text.startswith("http://") or text.startswith("https://") or "youtube.com" in text or "youtu.be" in text:
            return self.resolve_url(text)

        # 2. Artist channel handle (@handle)
        if text.startswith("@"):
            return self.resolve_artist_discography(text)

        # 2. Explicit prefixes
        text_lower = text.lower()
        if text_lower.startswith(("artista ", "artist ")):
            arg = text.split(maxsplit=1)[1].strip()
            return self.resolve_artist_discography(arg)

        if text_lower.startswith(("album ", "álbum ")):
            arg = text.split(maxsplit=1)[1].strip()
            return self.resolve_album(arg)

        if text_lower.startswith(("cancion ", "canción ", "song ", "track ", "pista ", "single ")):
            arg = text.split(maxsplit=1)[1].strip()
            return self.resolve_song(arg)

        # 3. Disambiguation heuristic
        try:
            art_res = self.yt.search(text, filter="artists", limit=1)
            alb_res = self.yt.search(text, filter="albums", limit=1)

            norm_input = re.sub(r"[^\w\s]", "", text.lower()).strip()

            art_match = False
            if art_res:
                cand_art = re.sub(r"[^\w\s]", "", art_res[0].get("artist", "").lower()).strip()
                if cand_art == norm_input:
                    art_match = True

            alb_match = False
            if alb_res:
                cand_alb = re.sub(r"[^\w\s]", "", alb_res[0].get("title", "").lower()).strip()
                if cand_alb == norm_input:
                    alb_match = True

            # Exact match for artist and not album -> Artist discography!
            if art_match and not alb_match:
                return self.resolve_artist_discography(text)

            # Exact match for album -> Album!
            if alb_match and not art_match:
                return self.resolve_album(text)

            # If both or neither match exactly:
            if alb_res and not art_res:
                return self.resolve_album(text)
            if art_res and not alb_res:
                return self.resolve_artist_discography(text)

            # Default to album search first, fallback to artist
            if alb_res:
                return self.resolve_album(text)
            return self.resolve_artist_discography(text)

        except Exception as e:
            logger.error("Error during disambiguation for '%s': %s", text, e)
            return self.resolve_album(text)


def parse_sync_args(raw_args: Optional[str], default_limit: int = 20) -> Tuple[Optional[str], int, int]:
    """
    Parses command arguments for the /sync command.
    Returns: (playlist_name, batch_number, limit)
    """
    if not raw_args:
        return None, 1, default_limit

    text = raw_args.strip()
    batch_number = 1
    limit = default_limit

    # 1. Check for "all" / "todo" for unlimited mode
    all_match = re.search(r'\b(all|todo|todos|sin\s*l[ií]mite)\b', text, re.IGNORECASE)
    if all_match:
        limit = 0
        text = (text[:all_match.start()] + " " + text[all_match.end():]).strip()

    # 2. Check for explicit limit (e.g. limit=30, limit 30, top 30, max 30, cant 30, cantidad 30)
    limit_match = re.search(r'\b(?:limit|l[ií]mite|top|max|cant|cantidad)[=:\s]+(\d+)\b', text, re.IGNORECASE)
    if limit_match:
        limit = int(limit_match.group(1))
        text = (text[:limit_match.start()] + " " + text[limit_match.end():]).strip()

    # 3. Check for explicit batch/lote (e.g. lote 2, lote=2, batch 2, page 2, p 2, p2)
    lote_match = re.search(r'\b(?:lote|batch|p(?:[aá]gina|age)?)[=:\s]*(\d+)\b', text, re.IGNORECASE)
    if lote_match:
        batch_number = max(1, int(lote_match.group(1)))
        text = (text[:lote_match.start()] + " " + text[lote_match.end():]).strip()

    # 4. Check trailing standalone integer
    trailing_match = re.search(r'(?:^|\s)(\d+)$', text)
    if trailing_match:
        num = int(trailing_match.group(1))
        if not lote_match and not limit_match:
            if num <= 20:
                batch_number = max(1, num)
            else:
                limit = num
            text = text[:trailing_match.start()].strip()

    playlist_name = text.strip() or None
    return playlist_name, batch_number, limit
