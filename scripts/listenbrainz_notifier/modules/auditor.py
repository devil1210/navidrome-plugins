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
        self.resolver = resolver

    def _is_album_complete_on_disk(self, artist_name: str, album_name: str) -> bool:
        """Checks if an album folder with audio tracks already exists on disk in library_dir."""
        if not self.library_dir or not self.library_dir.is_dir() or not artist_name or not album_name:
            return False
        clean_art = re.sub(r"[^\w\s]", "", str(artist_name).lower()).strip()
        clean_alb = re.sub(r"[^\w\s]", "", str(album_name).lower()).strip()
        for cat in ["General", "J-Music", "Compilaciones", "Soundtracks", "Soundtracks/Worldwide", "Soundtracks/J-Music"]:
            c_dir = self.library_dir / cat
            if not c_dir.is_dir():
                continue
            for art_dir in c_dir.iterdir():
                if not art_dir.is_dir() or art_dir.name.startswith((".", "_")):
                    continue
                if re.sub(r"[^\w\s]", "", art_dir.name.lower()).strip() == clean_art:
                    for alb_dir in art_dir.iterdir():
                        if not alb_dir.is_dir() or alb_dir.name.startswith((".", "_")):
                            continue
                        dir_alb = re.sub(r"^\[\d{4}\]\s*-\s*", "", alb_dir.name)
                        if re.sub(r"[^\w\s]", "", dir_alb.lower()).strip() == clean_alb:
                            try:
                                audio_files = [f for f in alb_dir.glob("**/*") if f.is_file() and f.suffix.lower() in ('.m4a', '.mp3', '.flac')]
                                if len(audio_files) >= 2:
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
        while len(all_albums) < 2500:
            batch = self.navidrome.get_album_list(type_="alphabeticalByName", size=batch_size, offset=nav_offset)
            if not batch:
                break
            all_albums.extend(batch)
            if len(batch) < batch_size:
                break
            nav_offset += len(batch)

        if not all_albums:
            all_albums = self.navidrome.get_album_list(type_="newest", size=500)

        skipped_offset = 0
        generic_artists = {"various artists", "varios artistas", "compilaciones", "soundtrack", "soundtracks", "unknown", "unknown artist"}

        for alb in all_albums:
            if len(incomplete) >= limit:
                break

            alb_name = alb.get("name") or alb.get("title") or ""
            artist_name = alb.get("artist") or alb.get("displayArtist") or ""
            alb_id = alb.get("id")

            if not alb_name or not alb_id:
                continue

            # Exclude dummy or punctuation-only names (e.g. "- - -", "...", "---")
            if not re.sub(r"[\s\-_.]", "", alb_name) or not re.sub(r"[\s\-_.]", "", artist_name):
                continue

            # Exclude recognized singles or special folders
            if alb_name.lower().startswith(("_singles", "single", "sencillo")) or re.search(r"\b(single|sencillo|ep|remix|acoustic|session)\b", alb_name, re.I):
                continue

            # Check if user ignored, persistently blocked, or already audited complete
            if self.state_tracker:
                if self.state_tracker.is_ignored(alb_name, artist_name):
                    continue
                if self.state_tracker.is_failed_blocked(alb_name, artist_name):
                    continue
                if self.state_tracker.is_audited_complete(alb_name, artist_name):
                    continue

            # Check if already complete on disk
            if self._is_album_complete_on_disk(artist_name, alb_name):
                if self.state_tracker:
                    self.state_tracker.mark_audited_complete(alb_name, artist_name)
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
                                self.state_tracker.mark_audited_complete(t_title, t_art)
                                continue

                            if self._is_album_complete_on_disk(resolved_alb.artist_name, resolved_alb.album_name):
                                if self.state_tracker:
                                    self.state_tracker.mark_audited_complete(resolved_alb.album_name, resolved_alb.artist_name)
                                    self.state_tracker.mark_audited_complete(t_title, t_art)
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
                        else:
                            # If online resolver fails to find official album, mark track as audited to prevent infinite loop
                            if self.resolver and self.state_tracker:
                                self.state_tracker.mark_audited_complete(t_title, t_art)
                continue

            # Regular artist album check
            is_candidate = False
            total_expected = 0

            # Check track number metadata
            track_numbers = []
            for t in nav_tracks:
                trkn = t.get("track")
                if trkn and isinstance(trkn, int):
                    track_numbers.append(trkn)

            if song_count == 1 and not re.search(r"\b(single|sencillo|ep)\b", alb_name, re.I):
                is_candidate = True
            elif track_numbers:
                max_trkn = max(track_numbers)
                if max_trkn > song_count and max_trkn <= 40:
                    is_candidate = True
                    total_expected = max_trkn

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
                    if self.state_tracker and self.state_tracker.is_audited_complete(resolved_alb.album_name, resolved_alb.artist_name):
                        self.state_tracker.mark_audited_complete(alb_name, artist_name)
                        continue

                    if self._is_album_complete_on_disk(resolved_alb.artist_name, resolved_alb.album_name):
                        if self.state_tracker:
                            self.state_tracker.mark_audited_complete(resolved_alb.album_name, resolved_alb.artist_name)
                            self.state_tracker.mark_audited_complete(alb_name, artist_name)
                        continue

                    incomplete.append({
                        "id": alb_id,
                        "album_name": resolved_alb.album_name,
                        "artist_name": resolved_alb.artist_name,
                        "present_count": song_count,
                        "expected_count": total_expected or (song_count + 1),
                        "cover_url": alb.get("coverArt") or getattr(resolved_alb, "cover_url", None),
                        "resolved_album": resolved_alb,
                    })
                else:
                    # Unresolvable on YTM -> mark audited to prevent infinite loop
                    if self.state_tracker:
                        self.state_tracker.mark_audited_complete(alb_name, artist_name)

        logger.info("Auditoría de biblioteca: detectados %d álbum(es) incompletos (offset: %d).", len(incomplete), offset)
        return incomplete

