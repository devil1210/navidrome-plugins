#!/usr/bin/env python3
"""
Library Incomplete Album Auditor:
Scans the local Navidrome library and disk storage to detect albums
with missing tracks or gaps, filtering out intentional singles and blocked items.
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .models import ResolvedAlbum
from .navidrome import NavidromeClient
from .state import StateTracker

logger = logging.getLogger("listenbrainz_notifier")


def _norm_album_key(s: str) -> str:
    """Normalizes album name for robust comparison across romanizations and punctuation."""
    clean = re.sub(r"^\[\d{4}\]\s*-\s*", "", s or "").strip()
    clean = re.sub(r"[\"\'`’“”«»]", "", clean)
    clean = re.sub(r"[\(\)\[\]\{\}]", " ", clean)
    clean = clean.replace("&", " and ").replace("・", " ").replace("·", " ")
    clean = re.sub(r"[^\w\s]", "", clean.lower())
    return re.sub(r"\s+", " ", clean).strip()


def _norm_artist_key(s: str) -> str:
    """Normalizes artist name for multi-artist credit comparison."""
    clean = re.sub(r"[\"\'`’“”«»]", "", s or "")
    clean = re.sub(r"[\(\)\[\]\{\}]", " ", clean)
    clean = clean.replace("&", " and ").replace("feat.", "and").replace("featuring", "and")
    clean = re.sub(r"[^\w\s]", "", clean.lower())
    return re.sub(r"\s+", " ", clean).strip()


class IncompleteAlbumAuditor:
    """
    Audits the music library to discover incomplete studio albums.
    Excludes singles, ignored releases, and persistently blocked tracks.
    """

    def __init__(
        self,
        navidrome: Optional[NavidromeClient] = None,
        downloader: Optional[Any] = None,
        state_tracker: Optional[StateTracker] = None,
        library_dir: Optional[Path] = None,
        resolver: Optional[Any] = None,
    ):
        self.navidrome = navidrome
        self.downloader = downloader
        self.state_tracker = state_tracker
        self.library_dir = Path(library_dir) if library_dir else None
        if resolver is not None:
            self.resolver = resolver
        else:
            try:
                from .resolver import YouTubeMusicResolver
                self.resolver = YouTubeMusicResolver()
            except Exception:
                self.resolver = None

    def _is_album_complete_on_disk(self, artist_name: str, album_name: str, year: Optional[int] = None, expected_min_tracks: int = 1) -> bool:
        """Checks if an album folder with audio tracks already exists on disk in library_dir."""
        if not self.library_dir or not self.library_dir.is_dir() or not album_name:
            return False
        clean_art = _norm_artist_key(artist_name)
        clean_alb = _norm_album_key(album_name)
        if not clean_alb:
            return False

        for cat in ["General", "J-Music", "Compilaciones", "Soundtracks", "Soundtracks/Worldwide", "Soundtracks/J-Music"]:
            c_dir = self.library_dir / cat
            if not c_dir.is_dir():
                continue
            for art_dir in c_dir.iterdir():
                if not art_dir.is_dir() or art_dir.name.startswith((".", "_")):
                    continue
                dir_art_k = _norm_artist_key(art_dir.name)
                # Match artist if exact, substring, or if album is a soundtrack/compilation
                artist_matches = (
                    not clean_art
                    or clean_art == dir_art_k
                    or dir_art_k in clean_art
                    or clean_art in dir_art_k
                    or "soundtrack" in clean_alb
                    or "soundtrack" in cat.lower()
                    or "compilacion" in cat.lower()
                )
                if artist_matches:
                    for alb_dir in art_dir.iterdir():
                        if not alb_dir.is_dir() or alb_dir.name.startswith((".", "_")):
                            continue

                        # Respect release year if folder has [YYYY] prefix and year is provided
                        year_match = re.match(r"^\[(\d{4})\]\s*-\s*", alb_dir.name)
                        if year and year > 0 and year_match:
                            try:
                                dir_yr = int(year_match.group(1))
                                if abs(year - dir_yr) > 1:
                                    # Different release year era (e.g. 2004 vs 2022)
                                    continue
                            except Exception:
                                pass

                        dir_alb_k = _norm_album_key(alb_dir.name)
                        if dir_alb_k == clean_alb or (len(clean_alb) > 6 and (clean_alb in dir_alb_k or dir_alb_k in clean_alb)):
                            try:
                                audio_files = [f for f in alb_dir.glob("**/*") if f.is_file() and f.suffix.lower() in ('.m4a', '.mp3', '.flac')]
                                if len(audio_files) >= expected_min_tracks and len(audio_files) >= 1:
                                    return True
                            except Exception:
                                pass
        return False

    def find_incomplete_albums(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        """
        Scans library albums and returns a list of candidate incomplete albums.
        For compilations / Various Artists, extracts the real artist's studio album for individual tracks.
        """
        incomplete: List[Dict[str, Any]] = []

        if not self.navidrome:
            return incomplete

        # 1. Fetch album list from Navidrome across batches
        all_albums: List[Dict[str, Any]] = []
        nav_offset = 0
        batch_size = 500
        while True:
            batch = self.navidrome.get_album_list(type_="alphabeticalByName", size=batch_size, offset=nav_offset)
            if not batch:
                break
            all_albums.extend(batch)
            if len(batch) < batch_size:
                break
            nav_offset += len(batch)

        if not all_albums:
            all_albums = self.navidrome.get_album_list(type_="newest", size=500)

        # Build album index by normalized key to detect duplicate/split cards in Navidrome
        nav_albums_by_key: Dict[str, List[Dict[str, Any]]] = {}
        for a in all_albums:
            a_name = a.get("name") or a.get("title") or ""
            ak = _norm_album_key(a_name)
            if ak:
                nav_albums_by_key.setdefault(ak, []).append(a)

        skipped_offset = 0
        generic_artists = {"various artists", "varios artistas", "compilaciones", "soundtrack", "soundtracks", "unknown", "unknown artist"}
        single_keywords = re.compile(
            r"\b(single|sencillo|ep|remix|acoustic|session|character song|character songu|theme song|op single|ed single|insert song|c/w|feat|cover|instrumental|radio edit|karaoke|cv:)\b|"
            r"(キャラクターソング|キャラソン|シングル|挿入歌|主題歌)",
            re.I
        )

        for alb in all_albums:
            if len(incomplete) >= limit:
                break

            alb_name = alb.get("name") or alb.get("title") or ""
            artist_name = alb.get("artist") or alb.get("displayArtist") or ""
            alb_id = alb.get("id")
            this_year = alb.get("year") or 0

            if not alb_name or not alb_id:
                continue

            # Exclude dummy or punctuation-only names (e.g. "- - -", "...", "---")
            if not re.sub(r"[\s\-_.]", "", alb_name) or not re.sub(r"[\s\-_.]", "", artist_name):
                continue

            # Exclude recognized singles or special folders
            if alb_name.lower().startswith(("_singles", "single", "sencillo")) or single_keywords.search(alb_name) or single_keywords.search(artist_name):
                continue

            # Check if user ignored, persistently blocked, or already audited complete
            if self.state_tracker:
                if self.state_tracker.is_ignored(alb_name, artist_name):
                    continue
                if self.state_tracker.is_failed_blocked(alb_name, artist_name):
                    continue
                if self.state_tracker.is_audited_complete(alb_name, artist_name, year=this_year):
                    continue

            # Check if another entry in Navidrome already has the full release of this album (respecting release year)
            alb_k = _norm_album_key(alb_name)
            if alb_k in nav_albums_by_key:
                has_full_duplicate = False
                this_cnt = alb.get("songCount") or 0
                for other_alb in nav_albums_by_key[alb_k]:
                    if other_alb.get("id") != alb_id:
                        other_cnt = other_alb.get("songCount") or 0
                        other_year = other_alb.get("year") or 0

                        # If both have explicit release years and differ by > 1 year, they are distinct albums from different eras
                        if this_year > 0 and other_year > 0 and abs(this_year - other_year) > 1:
                            continue

                        if other_cnt >= 6 and other_cnt > this_cnt:
                            has_full_duplicate = True
                            break
                if has_full_duplicate:
                    continue

            # Fetch tracks from Navidrome
            nav_tracks = self.navidrome.get_album_tracks(alb_id)
            if not nav_tracks:
                continue

            song_count = len(nav_tracks)
            is_compilation = (
                artist_name.lower() in generic_artists
                or alb.get("isCompilation", False)
            )

            # Special case for compilations: resolve the artist's studio album for tracks in the compilation
            if is_compilation:
                if song_count <= 4:
                    for t in nav_tracks:
                        if len(incomplete) >= limit:
                            break
                        t_art = t.get("artist") or t.get("displayArtist") or ""
                        t_title = t.get("title") or ""
                        if not t_art or t_art.lower() in generic_artists:
                            continue
                        if self.state_tracker and (self.state_tracker.is_ignored(t_title, t_art) or self.state_tracker.is_failed_blocked(t_title, t_art) or self.state_tracker.is_audited_complete(t_title, t_art)):
                            continue

                        if skipped_offset < offset:
                            skipped_offset += 1
                            continue

                        resolved_alb = None
                        if self.resolver and hasattr(self.resolver, "resolve_track_to_album"):
                            try:
                                resolved_alb = self.resolver.resolve_track_to_album(t_title, t_art)
                            except Exception:
                                resolved_alb = None

                        if resolved_alb:
                            if self.state_tracker and self.state_tracker.is_audited_complete(resolved_alb.album_name, resolved_alb.artist_name):
                                continue

                            if self._is_album_complete_on_disk(resolved_alb.artist_name, resolved_alb.album_name):
                                if self.state_tracker:
                                    self.state_tracker.mark_audited_complete(resolved_alb.album_name, resolved_alb.artist_name)
                                continue

                            incomplete.append({
                                "id": alb_id,
                                "album_name": resolved_alb.album_name,
                                "artist_name": resolved_alb.artist_name,
                                "present_count": 1,
                                "expected_count": 10,
                                "cover_url": alb.get("coverArt") or getattr(resolved_alb, "cover_url", None),
                                "resolved_album": resolved_alb,
                                "source_compilation": alb_name,
                            })
                continue

            # Regular artist album check
            is_candidate = False
            total_expected = 0

            # Check track number metadata
            track_numbers = []
            total_tracks_meta = 0
            for t in nav_tracks:
                trkn = t.get("track")
                if trkn and isinstance(trkn, int):
                    track_numbers.append(trkn)
                tot = t.get("totalTracks") or t.get("trackCount")
                if tot and isinstance(tot, int) and tot > total_tracks_meta:
                    total_tracks_meta = tot

            if song_count == 1:
                # If only 1 track is present, only consider it an incomplete studio album if:
                # 1) The single track is not track 1 (e.g. track 5 of an album)
                # 2) Or metadata explicitly declared totalTracks > 1
                if track_numbers and track_numbers[0] > 1:
                    is_candidate = True
                    total_expected = max(track_numbers[0], 2)
                elif total_tracks_meta > 1:
                    is_candidate = True
                    total_expected = total_tracks_meta
                else:
                    # Standalone 1-track release / single -> not an incomplete album
                    continue
            elif song_count >= 2:
                if track_numbers:
                    max_trkn = max(track_numbers)
                    if max_trkn > song_count and max_trkn <= 60:
                        is_candidate = True
                        total_expected = max_trkn
                    elif total_tracks_meta > song_count and total_tracks_meta <= 60:
                        is_candidate = True
                        total_expected = total_tracks_meta
                    else:
                        # Check for missing gaps in sequence (e.g. [1, 2, 4])
                        min_trkn = min(track_numbers)
                        expected_set = set(range(min_trkn, max_trkn + 1))
                        if len(expected_set) > len(track_numbers):
                            is_candidate = True
                            total_expected = max_trkn
                        else:
                            # Sequence is complete!
                            continue

            if is_candidate:
                if skipped_offset < offset:
                    skipped_offset += 1
                    continue

                resolved_alb = None
                if self.resolver and hasattr(self.resolver, "resolve_album_by_name"):
                    try:
                        resolved_alb = self.resolver.resolve_album_by_name(alb_name, artist_name)
                    except Exception:
                        resolved_alb = None
                elif not self.resolver:
                    # Offline / default mode for tests without external resolver
                    resolved_alb = ResolvedAlbum(
                        url=f"https://music.youtube.com/playlist?list=search_{abs(hash(alb_name))}",
                        album_name=alb_name,
                        artist_name=artist_name,
                    )

                if resolved_alb:
                    if self.state_tracker and self.state_tracker.is_audited_complete(resolved_alb.album_name, resolved_alb.artist_name, year=this_year):
                        continue

                    if self._is_album_complete_on_disk(resolved_alb.artist_name, resolved_alb.album_name, year=this_year, expected_min_tracks=total_expected):
                        if self.state_tracker:
                            self.state_tracker.mark_audited_complete(resolved_alb.album_name, resolved_alb.artist_name, year=this_year)
                            self.state_tracker.mark_audited_complete(alb_name, artist_name, year=this_year)
                        continue

                    incomplete.append({
                        "id": alb_id,
                        "album_name": alb_name or resolved_alb.album_name,
                        "artist_name": artist_name or resolved_alb.artist_name,
                        "present_count": song_count,
                        "expected_count": total_expected or (song_count + 1),
                        "cover_url": alb.get("coverArt") or getattr(resolved_alb, "cover_url", None),
                        "resolved_album": resolved_alb,
                    })

        logger.info("Auditoría de biblioteca: detectados %d álbum(es) incompletos (offset: %d).", len(incomplete), offset)
        return incomplete

