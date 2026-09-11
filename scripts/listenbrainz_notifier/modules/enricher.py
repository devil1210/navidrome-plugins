#!/usr/bin/env python3
"""
ListenBrainz metadata enricher for playlists lacking artist information.
"""

import json
import logging
import re
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests

from .models import MissingTrack

logger = logging.getLogger("listenbrainz_notifier")


class ListenBrainzEnricher:
    """
    Enriches playlists lacking artist metadata (such as locally generated Jams)
    by looking up official MusicBrainz/ListenBrainz artist metadata.
    """

    def __init__(
        self,
        username: str,
        token: Optional[str] = None,
        cache_file: Optional[Path] = None,
    ):
        self.username = username
        self.token = token
        self.cache_file = cache_file or (Path(__file__).resolve().parent.parent / ".lbz_cache.json")
        self.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "NavidromePlaylistImporter/6.0.0",
        }
        if token:
            self.headers["Authorization"] = f"Token {token}"
        self.title_map: Dict[str, Dict[str, str]] = self._load_cache()

    def _normalize_title(self, title: str) -> str:
        return (
            title.lower()
            .replace("’", "'")
            .replace("`", "'")
            .replace("“", '"')
            .replace("”", '"')
            .strip()
        )

    def _load_cache(self) -> Dict[str, Dict[str, str]]:
        if self.cache_file.exists():
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning("Could not load ListenBrainz metadata cache: %s", e)
        return {}

    def _save_cache(self):
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self.title_map, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning("Could not save ListenBrainz metadata cache: %s", e)

    def refresh_recommendations(self, force: bool = False):
        """Fetches up to 1000 recommendations from ListenBrainz and maps titles to artists."""
        if not self.username:
            return

        if not force and self.cache_file.exists():
            try:
                mtime = self.cache_file.stat().st_mtime
                if time.time() - mtime < 86400 and len(self.title_map) > 50:
                    logger.info("Using cached ListenBrainz metadata (%d tracks available).", len(self.title_map))
                    return
            except Exception:
                pass

        logger.info("Fetching ListenBrainz recommendations for user '%s'...", self.username)
        url = f"https://api.listenbrainz.org/1/cf/recommendation/user/{self.username}/recording?count=1000"
        try:
            resp = requests.get(url, headers=self.headers, timeout=20)
            if resp.status_code != 200:
                logger.warning("ListenBrainz recommendations API returned %d: %s", resp.status_code, resp.text[:200])
                return

            payload = resp.json().get("payload", {})
            mbid_objs = payload.get("mbids", [])
            mbids = [x["recording_mbid"] for x in mbid_objs if "recording_mbid" in x]
            if not mbids:
                logger.warning("No recommendation MBIDs found for user %s", self.username)
                return

            logger.info("Retrieved %d recommendation MBIDs. Looking up artist metadata...", len(mbids))
            lookup_url = "https://api.listenbrainz.org/1/metadata/recording"
            for i in range(0, len(mbids), 100):
                batch = mbids[i:i + 100]
                r_meta = requests.post(
                    lookup_url,
                    headers=self.headers,
                    json={"recording_mbids": batch, "inc": "artist release"},
                    timeout=20,
                )
                if r_meta.status_code == 200:
                    for mbid, d in r_meta.json().items():
                        rec_name = d.get("recording", {}).get("name")
                        artists = [a.get("name") for a in d.get("artist", {}).get("artists", []) if a.get("name")]
                        artist_str = " & ".join(artists) if artists else ""
                        album_str = d.get("release", {}).get("name", "")
                        if rec_name:
                            key = self._normalize_title(rec_name)
                            info_entry = {
                                "title": rec_name,
                                "artist": artist_str,
                                "album": album_str,
                            }
                            self.title_map[key] = info_entry
                            # Also index without parenthetical/bracketed annotations for robust matching
                            clean_rec = re.sub(r"\s*[\(\[].*?[\)\]]", "", rec_name).strip()
                            clean_key = self._normalize_title(clean_rec)
                            if clean_key and clean_key not in self.title_map:
                                self.title_map[clean_key] = info_entry
                time.sleep(0.2)

            self._save_cache()
            logger.info("ListenBrainz metadata cache updated (%d tracks mapped).", len(self.title_map))
        except Exception as e:
            logger.error("Error refreshing ListenBrainz recommendations: %s", e)

    def enrich_tracks(self, tracks: List[MissingTrack]) -> List[MissingTrack]:
        """Fills in missing artist names using the ListenBrainz recommendations cache."""
        missing_artists = [t for t in tracks if not t.artist]
        if not missing_artists:
            return tracks

        if len(self.title_map) == 0:
            self.refresh_recommendations()

        enriched: List[MissingTrack] = []
        matched_count = 0
        for t in tracks:
            if t.artist:
                enriched.append(t)
                continue

            key = self._normalize_title(t.title)
            info = self.title_map.get(key)
            if not info:
                clean_title = re.sub(r"\s*[\(\[].*?[\)\]]", "", t.title).strip()
                info = self.title_map.get(self._normalize_title(clean_title))

            if info and info.get("artist"):
                enriched.append(MissingTrack(title=t.title, artist=info["artist"]))
                matched_count += 1
            else:
                enriched.append(t)

        logger.info("Enriched %d/%d missing tracks with official ListenBrainz artist metadata.", matched_count, len(missing_artists))
        return enriched
