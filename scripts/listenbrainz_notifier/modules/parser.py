#!/usr/bin/env python3
"""
Parser for missing tracks and playlist descriptions.
"""

import re
from typing import List, Optional, Set

from .models import MissingTrack


class TrackParser:
    """Parses missing track strings from Navidrome playlist descriptions."""

    @staticmethod
    def extract_missing_text(comment: str) -> Optional[str]:
        """
        Extracts the relevant missing tracks line from a playlist comment.
        Handles both 'Tracks not matched ...' and 'Tracks not found in library: ...'
        """
        if not comment:
            return None

        for line in comment.splitlines():
            line = line.strip()
            if "Tracks not matched " in line:
                return line.split("Tracks not matched ", 1)[1].strip()
            if "Tracks not found in library: " in line:
                return line.split("Tracks not found in library: ", 1)[1].strip()

        return None

    @classmethod
    def parse_missing_tracks(cls, text: str) -> List[MissingTrack]:
        """
        Parses a list of 'Track Title by Artist Name' entries separated by commas.
        Intelligently handles song titles containing commas (e.g. 'Boom, Boom, Boom, Boom!!').
        """
        if not text:
            return []

        # Remove any known prefixes if present in raw text
        for prefix in ["Tracks not matched ", "Tracks not found in library: "]:
            if text.startswith(prefix):
                text = text[len(prefix):].strip()

        comma_count = text.count(",")
        by_count = text.count(" by ")

        # A "Title by Artist" list has roughly as many " by " as tracks (commas).
        # If there are few or no " by " relative to commas (e.g. 1 "by" in 400 tracks like "Stand by Me"),
        # this is a pure list of track titles without artist names.
        is_title_by_artist = by_count > 0 and (comma_count == 0 or by_count >= max(1, int(comma_count * 0.3)))

        if not is_title_by_artist:
            # Fallback to comma separation
            raw_items = [item.strip() for item in text.split(",") if item.strip()]
            return [MissingTrack(title=item, artist="") for item in raw_items]

        # Each item has: Title_i by Artist_i
        # Between by_i and by_{i+1}, the segment is: 'Artist_i, Title_{i+1}'
        parts = text.split(" by ")
        if len(parts) < 2:
            return []

        results: List[MissingTrack] = []
        current_title = parts[0].strip()

        for i in range(1, len(parts)):
            segment = parts[i]
            if i == len(parts) - 1:
                # Last artist in the chain
                artist = segment.strip()
                # Remove any trailing parenthetical notes or rating text if caught
                results.append(MissingTrack(title=current_title, artist=artist))
            else:
                # Segment contains: 'Artist_i, Title_{i+1}'
                # Artist names from ListenBrainz credits rarely contain commas
                # (collaborations use '&', 'feat.', etc.), so the first comma
                # reliably splits the artist from the next title.
                if ", " in segment:
                    artist, next_title = segment.split(", ", 1)
                elif "," in segment:
                    artist, next_title = segment.split(",", 1)
                else:
                    artist, next_title = segment, ""

                results.append(MissingTrack(title=current_title, artist=artist.strip()))
                current_title = next_title.strip()

        return results

    @classmethod
    def rebuild_comment_without_tracks(cls, original_comment: str, resolved_track_titles: Set[str]) -> str:
        """
        Removes resolved tracks from 'Tracks not matched ...' or 'Tracks not found in library: ...'
        preserving all other lines (URL, Updated on, excluded, etc.).
        """
        def norm(s: str) -> str:
            return re.sub(r"[^\w\s]", "", (s or "").lower()).strip()

        norm_resolved = {norm(t) for t in resolved_track_titles if norm(t)}
        lines = original_comment.splitlines()
        new_lines = []

        for line in lines:
            if "Tracks not matched" in line or "Tracks not found in library:" in line:
                prefix = ""
                content = ""
                if "Tracks not found in library:" in line:
                    prefix = "Tracks not found in library: "
                    content = line.split("Tracks not found in library:")[1]
                elif "Tracks not matched" in line:
                    prefix = "Tracks not matched "
                    content = line.split("Tracks not matched")[1]

                parsed = cls.parse_missing_tracks(content.strip())
                remaining = [t for t in parsed if norm(t.title) not in norm_resolved]

                if remaining:
                    if " by " in content:
                        rem_str = ", ".join(f"{t.title} by {t.artist}" if t.artist else t.title for t in remaining)
                    else:
                        rem_str = ", ".join(t.title for t in remaining)
                    new_lines.append(prefix + rem_str)
            else:
                new_lines.append(line)

        return "\n".join(new_lines).strip()
