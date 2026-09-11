#!/usr/bin/env python3
"""
Navidrome / Subsonic API Client.
"""

import hashlib
import logging
import re
import secrets
from typing import Any, Dict, List, Optional, Tuple

import requests

try:
    import pykakasi
    _kakasi_inst = pykakasi.kakasi()
except ImportError:
    _kakasi_inst = None

logger = logging.getLogger("listenbrainz_notifier")


class NavidromeClient:
    """Client for Navidrome's Subsonic API."""

    def __init__(self, base_url: str, user: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.user = user
        self.password = password

    def _get_auth_params(self) -> Dict[str, str]:
        salt = secrets.token_hex(6)
        token = hashlib.md5((self.password + salt).encode("utf-8")).hexdigest()
        return {
            "u": self.user,
            "t": token,
            "s": salt,
            "v": "1.16.1",
            "c": "listenbrainz-jams-notifier",
            "f": "json",
        }

    def get_playlists(self) -> List[Dict[str, Any]]:
        """Retrieves all playlists from Navidrome."""
        url = f"{self.base_url}/rest/getPlaylists.view"
        params = self._get_auth_params()

        try:
            response = requests.get(url, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            subsonic_resp = data.get("subsonic-response", {})

            if subsonic_resp.get("status") != "ok":
                err = subsonic_resp.get("error", {}).get("message", "Unknown Subsonic error")
                logger.error("Subsonic API error: %s", err)
                return []

            playlists = subsonic_resp.get("playlists", {}).get("playlist", [])
            # Subsonic can return a single object or list
            if isinstance(playlists, dict):
                playlists = [playlists]
            return playlists
        except Exception as e:
            logger.error("Failed to fetch playlists from Navidrome: %s", e)
            return []

    def get_playlist_details(self, playlist_id: str) -> Optional[Dict[str, Any]]:
        """Fetches full playlist details including comment."""
        url = f"{self.base_url}/rest/getPlaylist.view"
        params = self._get_auth_params()
        params["id"] = playlist_id

        try:
            response = requests.get(url, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            subsonic_resp = data.get("subsonic-response", {})
            return subsonic_resp.get("playlist")
        except Exception as e:
            logger.error("Failed to fetch playlist %s details: %s", playlist_id, e)
            return None

    def search_songs(self, query: str, count: int = 15) -> List[Dict[str, Any]]:
        """Searches Navidrome for songs matching the query."""
        url = f"{self.base_url}/rest/search3.view"
        params = self._get_auth_params()
        params["query"] = query
        params["songCount"] = str(count)

        try:
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            songs = data.get("subsonic-response", {}).get("searchResult3", {}).get("song", [])
            if isinstance(songs, dict):
                songs = [songs]
            return songs or []
        except Exception as e:
            logger.warning("Error searching Navidrome for '%s': %s", query, e)
            return []

    def search_albums(self, query: str, count: int = 15) -> List[Dict[str, Any]]:
        """Searches Navidrome for albums matching the query."""
        url = f"{self.base_url}/rest/search3.view"
        params = self._get_auth_params()
        params["query"] = query
        params["albumCount"] = str(count)

        try:
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            albums = data.get("subsonic-response", {}).get("searchResult3", {}).get("album", [])
            if isinstance(albums, dict):
                albums = [albums]
            return albums or []
        except Exception as e:
            logger.warning("Error searching Navidrome albums for '%s': %s", query, e)
            return []

    def get_album(self, album_id: str) -> Optional[Dict[str, Any]]:
        """Fetches full album details including track list from Navidrome."""
        url = f"{self.base_url}/rest/getAlbum.view"
        params = self._get_auth_params()
        params["id"] = album_id

        try:
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            return data.get("subsonic-response", {}).get("album")
        except Exception as e:
            logger.warning("Error fetching album '%s' from Navidrome: %s", album_id, e)
            return None

    def get_album_tracks(self, album_id: str) -> List[Dict[str, Any]]:
        """Fetches all songs/tracks for an album ID in Navidrome."""
        album = self.get_album(album_id)
        if not album:
            return []
        songs = album.get("song", [])
        if isinstance(songs, dict):
            return [songs]
        elif isinstance(songs, list):
            return songs
        return []

    def find_album_match(self, album_name: str, artist_name: str = "") -> Optional[Dict[str, Any]]:
        """
        Checks whether an album already exists in the local Navidrome library.
        Normalizes titles, strips single/edition tags, and handles flexible/token artist matching.
        """
        def norm(s: str) -> str:
            return re.sub(r"[^\w\s]", "", (s or "").lower()).strip()

        clean_album = re.sub(r"\s*[\(\[].*?[\)\]]", "", album_name).strip()
        norm_target_album = norm(clean_album)
        if not norm_target_album:
            return None

        candidates = self.search_albums(clean_album, count=10)
        if not candidates:
            return None

        norm_target_artist = norm(artist_name)

        for cand in candidates:
            cand_name = cand.get("name", "")
            clean_cand = re.sub(r"\s*[\(\[].*?[\)\]]", "", cand_name).strip()
            norm_cand_name = norm(clean_cand)

            name_match = (
                norm_cand_name == norm_target_album
                or (len(norm_target_album) >= 4 and (norm_target_album in norm_cand_name or norm_cand_name in norm_target_album))
            )
            if not name_match:
                continue

            cand_artist = norm(cand.get("artist", ""))
            cand_artists = [cand_artist]
            for a_obj in cand.get("artists", []):
                if isinstance(a_obj, dict) and a_obj.get("name"):
                    cand_artists.append(norm(a_obj["name"]))

            if norm_target_artist:
                matched_artist = False
                for ca in cand_artists:
                    if not ca:
                        continue
                    if ca == norm_target_artist:
                        matched_artist = True
                        break
                    if len(norm_target_artist) >= 3 and (norm_target_artist in ca or ca in norm_target_artist):
                        matched_artist = True
                        break
                    if any(t in ca for t in norm_target_artist.split() if len(t) >= 3) or any(t in norm_target_artist for t in ca.split() if len(t) >= 3):
                        matched_artist = True
                        break

                # Also check if target artist appears in the album name itself
                if not matched_artist and norm_target_artist in norm(cand_name):
                    matched_artist = True

                # If only 1 candidate found with exact album title, accept it
                if not matched_artist and len(candidates) == 1 and norm_cand_name == norm_target_album:
                    matched_artist = True

                if not matched_artist:
                    continue

            return cand

        return None

    def find_best_match(
        self,
        target_title: str,
        target_artist: str = "",
        target_album: Optional[str] = None,
        target_duration_ms: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Looks up whether a track already exists in Navidrome using intelligent scoring:
        - Multi-query search (Title + Artist, Romaji if Japanese, Title with count=30)
        - Title matching (strict/normalized, track-number prefix stripping, version tag stripping)
        - Flexible artist matching (artist, displayArtist, albumArtists, contributors, substrings)
        - Tie-breaker scoring (duration delta, compilation vs studio, live/demo penalty)
        """
        def norm(s: str) -> str:
            clean = re.sub(r"\s*[\(\[].*?[\)\]]", "", s or "").strip()
            clean = re.sub(r"^\d+[\s\.\-_]+", "", clean).strip()
            clean = clean.replace("&", " and ")
            clean = clean.replace("’", "'").replace("`", "'")
            clean = clean.replace("ō", "o").replace("ū", "u").replace("ā", "a").replace("ē", "e").replace("ī", "i")
            clean = clean.replace("ou", "o").replace("uu", "u")
            clean = re.sub(r"\s*-\s*(english|japanese|spanish|single|album|radio|edit|ver|version|remaster|remastered).*", "", clean, flags=re.I).strip()
            return re.sub(r"[^\w\s]", "", clean.lower()).strip()

        def to_romaji(s: str) -> str:
            if not _kakasi_inst or not s:
                return s
            if any('\u3040' <= ch <= '\u30ff' or '\u4e00' <= ch <= '\u9faf' for ch in s):
                try:
                    return " ".join(x["passport"] for x in _kakasi_inst.convert(s)).strip()
                except Exception:
                    pass
            return s

        clean_title = re.sub(r"\s*[\(\[].*?[\)\]]", "", target_title).strip()
        clean_title = re.sub(r"^\d+[\s\.\-_]+", "", clean_title).strip()
        clean_title = clean_title.replace("’", "'").replace("`", "'")
        clean_title = re.sub(r"\s*-\s*(english|japanese|spanish|single|album|radio|edit|ver|version|remaster|remastered).*", "", clean_title, flags=re.I).strip()
        norm_target_title = norm(clean_title)
        if not norm_target_title:
            return None

        # 1. Gather candidates using targeted queries
        raw_cands = []
        if target_artist:
            raw_cands.extend(self.search_songs(f"{clean_title} {target_artist}", count=25))
            if "&" in clean_title:
                raw_cands.extend(self.search_songs(f"{clean_title.replace('&', 'and')} {target_artist}", count=20))
            rom_artist = to_romaji(target_artist)
            if rom_artist != target_artist:
                raw_cands.extend(self.search_songs(f"{clean_title} {rom_artist}", count=20))

        rom_title = to_romaji(clean_title)
        if rom_title != clean_title:
            if target_artist:
                raw_cands.extend(self.search_songs(f"{rom_title} {target_artist}", count=20))
                rom_artist = to_romaji(target_artist)
                if rom_artist != target_artist:
                    raw_cands.extend(self.search_songs(f"{rom_title} {rom_artist}", count=20))
            raw_cands.extend(self.search_songs(rom_title, count=25))

        raw_cands.extend(self.search_songs(clean_title, count=30))
        if "&" in clean_title:
            raw_cands.extend(self.search_songs(clean_title.replace("&", " and "), count=30))

        # Deduplicate candidates
        candidates: List[Dict[str, Any]] = []
        seen_ids = set()
        for s in raw_cands:
            sid = s.get("id")
            if sid and sid not in seen_ids:
                seen_ids.add(sid)
                candidates.append(s)
            elif not sid and s not in candidates:
                candidates.append(s)

        if not candidates:
            return None

        norm_target_artist = norm(target_artist)
        norm_rom_target_artist = norm(to_romaji(target_artist))
        scored_candidates: List[Tuple[float, Dict[str, Any]]] = []

        compilation_kw = [
            "greatest hits", "the best of", "best of", "anthology",
            "ultimate experience", "essential", "singles", "collection",
            "box set", "definitive collection",
        ]
        live_kw = [
            "live", "en vivo", "woodstock", "monterey", "concert",
            "acústico", "acoustic", "demo", "remix", "unplugged",
        ]

        target_is_live = any(k in target_title.lower() for k in live_kw)

        for cand in candidates:
            cand_title = cand.get("title", "")
            norm_cand_title = norm(cand_title)

            cand_parts = [norm(p) for p in re.split(r"\s*[\-\–\—\/]\s*", cand_title) if p.strip()]

            # Title must match (exact normalized, space-insensitive, substring, dual segments, or token overlap)
            title_match = False
            for t_target in (norm_target_title, norm(rom_title)):
                if not t_target:
                    continue
                if t_target == norm_cand_title or t_target.replace(" ", "") == norm_cand_title.replace(" ", ""):
                    title_match = True
                    break
                # Check individual segments of a dual title (e.g. "太陽 - Sun" matches "Sun" or "太陽")
                if any(t_target == cp or t_target.replace(" ", "") == cp.replace(" ", "") for cp in cand_parts):
                    title_match = True
                    break
                if len(t_target.replace(" ", "")) >= 4 and (
                    t_target.replace(" ", "") in norm_cand_title.replace(" ", "")
                    or norm_cand_title.replace(" ", "") in t_target.replace(" ", "")
                ):
                    title_match = True
                    break
                t_words = set(t_target.split())
                c_words = set(norm_cand_title.split())
                if t_words and c_words:
                    common = t_words.intersection(c_words)
                    # If target has words in common with candidate
                    if len(common) >= 2 or (len(common) == 1 and (len(list(common)[0]) >= 3 or common == t_words)):
                        title_match = True
                        break
                    # Word stem / loanword matching for words >= 5 characters (e.g. romance in taishourouman)
                    for tw in t_words:
                        if len(tw) >= 5:
                            for cw in c_words:
                                if len(cw) >= 5 and (tw[:5] in cw or cw[:5] in tw):
                                    title_match = True
                                    break
                            if title_match:
                                break
                    if title_match:
                        break

            if not title_match:
                continue

            # Check artist compatibility if target_artist is known
            cand_artists = [
                cand.get("artist", ""),
                cand.get("displayArtist", ""),
                cand.get("displayAlbumArtist", ""),
            ]
            for a_obj in cand.get("artists", []):
                if isinstance(a_obj, dict) and a_obj.get("name"):
                    cand_artists.append(a_obj["name"])
            for a_obj in cand.get("albumArtists", []):
                if isinstance(a_obj, dict) and a_obj.get("name"):
                    cand_artists.append(a_obj["name"])
            for a_obj in cand.get("contributors", []):
                if isinstance(a_obj, dict) and a_obj.get("artist", {}).get("name"):
                    cand_artists.append(a_obj["artist"]["name"])

            norm_cand_artists = [norm(a) for a in cand_artists if a]

            artist_score = 0.0
            target_artists = [a for a in (norm_target_artist, norm_rom_target_artist) if a]
            if target_artists:
                matched_artist = False
                for ta in target_artists:
                    for ca in norm_cand_artists:
                        if not ca:
                            continue
                        if ca == ta:
                            matched_artist = True
                            artist_score = 100.0
                            break
                        if len(ta) >= 3 and (ta in ca or ca in ta):
                            matched_artist = True
                            artist_score = 85.0
                            break
                        if any(t in ca for t in ta.split() if len(t) >= 3) or any(t in ta for t in ca.split() if len(t) >= 3):
                            matched_artist = True
                            artist_score = 80.0
                            break
                    if matched_artist:
                        break

                # Also check if target artist appears in the candidate title or album name
                if not matched_artist:
                    for ta in target_artists:
                        if ta in norm_cand_title or ta in norm(cand.get("album", "")):
                            matched_artist = True
                            artist_score = 85.0
                            break

                # If candidate has exact title match and target title is distinct (>= 5 chars or >= 2 words)
                if not matched_artist and (norm_cand_title == norm_target_title or norm_cand_title == norm(rom_title)):
                    if len(norm_target_title) >= 5 or len(norm_target_title.split()) >= 2:
                        matched_artist = True
                        artist_score = 75.0

                if not matched_artist:
                    continue
            else:
                artist_score = 50.0

            score = artist_score

            cand_album = cand.get("album", "")
            cand_album_lower = cand_album.lower()

            # Album match bonus
            if target_album and norm(target_album) == norm(cand_album):
                score += 35.0

            # Duration match scoring
            cand_duration = cand.get("duration")
            if target_duration_ms and cand_duration:
                diff_sec = abs((target_duration_ms / 1000.0) - cand_duration)
                if diff_sec <= 4.0:
                    score += 30.0
                elif diff_sec <= 10.0:
                    score += 15.0
                elif diff_sec > 30.0:
                    score -= 40.0

            # Live / demo penalty (avoid concerts when studio requested)
            cand_is_live = any(k in cand_title.lower() or k in cand_album_lower for k in live_kw)
            if cand_is_live and not target_is_live:
                score -= 50.0

            # Compilation penalty vs studio album
            is_comp = any(k in cand_album_lower for k in compilation_kw)
            if is_comp:
                score -= 20.0
            else:
                score += 15.0

            # Bitrate tie-breaker
            bitrate = cand.get("bitRate", 128) or 128
            score += bitrate / 100.0

            scored_candidates.append((score, cand))

        if not scored_candidates:
            return None

        scored_candidates.sort(key=lambda x: x[0], reverse=True)
        best_score, best_cand = scored_candidates[0]

        if best_score >= 50.0:
            logger.debug(
                "Matched library song '%s' by '%s' on album '%s' (score: %.1f)",
                best_cand.get("title"),
                best_cand.get("artist"),
                best_cand.get("album"),
                best_score,
            )
            return best_cand

        return None

    def set_playlist_tracks(self, playlist_id: str, song_ids: List[str]) -> bool:
        """Overwrites tracks of an existing playlist with the given song IDs."""
        unique_song_ids = list(dict.fromkeys(song_ids))
        url = f"{self.base_url}/rest/createPlaylist.view"
        params = self._get_auth_params()
        params["playlistId"] = playlist_id
        query_parts = [f"{k}={requests.utils.quote(v)}" for k, v in params.items()]
        for s_id in unique_song_ids:
            query_parts.append(f"songId={requests.utils.quote(s_id)}")

        full_url = f"{url}?{'&'.join(query_parts)}"
        try:
            resp = requests.get(full_url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            return data.get("subsonic-response", {}).get("status") == "ok"
        except Exception as e:
            logger.error("Failed to set tracks for playlist %s: %s", playlist_id, e)
            return False

    def add_tracks_to_playlist(self, playlist_id: str, song_ids: List[str]) -> bool:
        """Adds tracks to an existing playlist in Navidrome, preventing duplicates."""
        if not song_ids:
            return True

        # Deduplicate incoming song IDs while preserving order
        unique_song_ids = list(dict.fromkeys(song_ids))

        # Check existing tracks in playlist to avoid inserting duplicates
        existing_ids = set()
        playlist_details = self.get_playlist_details(playlist_id)
        if playlist_details:
            entries = playlist_details.get("entry", [])
            if isinstance(entries, dict):
                entries = [entries]
            for entry in entries:
                if isinstance(entry, dict) and "id" in entry:
                    existing_ids.add(entry["id"])

        to_add = [s_id for s_id in unique_song_ids if s_id not in existing_ids]
        if not to_add:
            logger.info("All %d track(s) already exist in playlist %s. Skipping addition.", len(unique_song_ids), playlist_id)
            return True

        url = f"{self.base_url}/rest/updatePlaylist.view"
        params = self._get_auth_params()
        params["playlistId"] = playlist_id
        # Subsonic allows multiple songIdToAdd parameters
        query_parts = [f"{k}={requests.utils.quote(v)}" for k, v in params.items()]
        for s_id in to_add:
            query_parts.append(f"songIdToAdd={requests.utils.quote(s_id)}")

        full_url = f"{url}?{'&'.join(query_parts)}"
        try:
            resp = requests.get(full_url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            return data.get("subsonic-response", {}).get("status") == "ok"
        except Exception as e:
            logger.error("Failed to add tracks to playlist %s: %s", playlist_id, e)
            return False

    def update_playlist_comment(self, playlist_id: str, comment: str) -> bool:
        """Updates the comment on a playlist."""
        url = f"{self.base_url}/rest/updatePlaylist.view"
        params = self._get_auth_params()
        params["playlistId"] = playlist_id
        params["comment"] = comment

        try:
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            return data.get("subsonic-response", {}).get("status") == "ok"
        except Exception as e:
            logger.error("Failed to update comment on playlist %s: %s", playlist_id, e)
            return False

    def start_scan(self, full_scan: bool = False) -> bool:
        """Triggers an immediate background library scan in Navidrome."""
        url = f"{self.base_url}/rest/startScan.view"
        params = self._get_auth_params()
        if full_scan:
            params["fullScan"] = "true"
        try:
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json().get("subsonic-response", {})
                return data.get("status") == "ok"
        except Exception as e:
            logger.debug("Error requesting startScan from Navidrome: %s", e)
        return False
