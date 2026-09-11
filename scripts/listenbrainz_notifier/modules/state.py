#!/usr/bin/env python3
"""
State Tracker for persisting processed playlists, batch limits, and discovered items.
"""

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger("listenbrainz_notifier")


class StateTracker:
    """Tracks processed playlists and comments to prevent duplicate alerts."""

    def __init__(self, state_file: Union[Path, str]):
        self.state_file = Path(state_file)
        self.state: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        if self.state_file.exists():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning("Could not load state file %s: %s", self.state_file, e)
        return {}

    def save(self):
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error("Could not save state file %s: %s", self.state_file, e)

    def get_limit(self, default_limit: int = 20) -> int:
        return int(self.state.get("__settings__", {}).get("max_albums_per_sync", default_limit))

    def set_limit(self, limit: int):
        if "__settings__" not in self.state:
            self.state["__settings__"] = {}
        self.state["__settings__"]["max_albums_per_sync"] = limit
        self.save()

    def is_already_processed(self, playlist_id: str, comment: str, changed_date: Optional[str] = None) -> bool:
        comment_hash = hashlib.sha256(comment.encode("utf-8")).hexdigest()
        entry = self.state.get(playlist_id)
        if not entry:
            return False

        if entry.get("comment_hash") == comment_hash:
            return True
        if changed_date and entry.get("changed") == changed_date:
            return True

        return False

    def mark_processed(self, playlist_id: str, comment: str, changed_date: Optional[str] = None):
        comment_hash = hashlib.sha256(comment.encode("utf-8")).hexdigest()
        self.state[playlist_id] = {
            "comment_hash": comment_hash,
            "changed": changed_date,
            "last_processed": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self.save()

    def save_last_batch(
        self,
        playlist_name: str,
        batch_number: int,
        batch_size: int,
        total_batches: int,
        albums: List[Dict[str, Any]],
        tracks: List[Dict[str, Any]],
    ):
        """Persists the resolved batch so /download can access it without re-resolving."""
        if "__last_batches__" not in self.state:
            self.state["__last_batches__"] = {}
        self.state["__last_batches__"][playlist_name.lower()] = {
            "playlist_name": playlist_name,
            "batch_number": batch_number,
            "batch_size": batch_size,
            "total_batches": total_batches,
            "albums": albums,
            "tracks": tracks,
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self.state["__last_batches__"]["__latest__"] = playlist_name.lower()
        self.save()

    def get_last_batch(self, playlist_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Retrieves the last resolved batch for a playlist or the most recent one."""
        batches = self.state.get("__last_batches__", {})
        if not playlist_name:
            latest_key = batches.get("__latest__")
            if latest_key and latest_key in batches:
                return batches[latest_key]
            for k, v in batches.items():
                if k != "__latest__":
                    return v
            return None
        return batches.get(playlist_name.lower())

    def save_last_discovered(self, items: List[Dict[str, Any]]):
        """Persists the latest recommended items for /descubrir and /dl_rec."""
        self.state["__discovered__"] = {
            "items": items,
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self.save()

    def get_last_discovered(self) -> List[Dict[str, Any]]:
        """Retrieves the latest discovered recommendations."""
        disc = self.state.get("__discovered__", {})
        return disc.get("items", []) if isinstance(disc, dict) else []

    def save_last_new_releases(self, items: List[Dict[str, Any]]):
        """Persists the latest new release items for /novedades and /dl_nov."""
        self.state["__new_releases__"] = {
            "items": items,
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self.save()

    def get_last_new_releases(self) -> List[Dict[str, Any]]:
        """Retrieves the latest new releases."""
        nr = self.state.get("__new_releases__", {})
        return nr.get("items", []) if isinstance(nr, dict) else []

    def get_picard_plugins(self) -> Dict[str, bool]:
        """Returns the configuration of enabled/disabled Picard plugins."""
        defaults = {
            "auto_romanizer": True,
            "lrclib_lyrics": True,
            "lastfm": True,
            "genre_mapper": True,
            "release_type": True,
            "soundtrack": True,
            "enhanced_titles": True,
            "feat_artists": True,
            "hyphen_unicode": True,
            "deduplicator": True,
            "library_relocation": True,
            "playlist": False,
            "collect_artists": False,
        }
        saved = self.state.get("__picard_plugins__", {})
        if isinstance(saved, dict):
            defaults.update(saved)
        return defaults

    def set_picard_plugin(self, plugin_key: str, enabled: bool):
        """Sets a Picard plugin enabled or disabled and persists to state."""
        if "__picard_plugins__" not in self.state:
            self.state["__picard_plugins__"] = {}
        self.state["__picard_plugins__"][plugin_key] = bool(enabled)
        self.save()

    def toggle_picard_plugin(self, plugin_key: str) -> bool:
        """Toggles a Picard plugin on/off and returns the new state."""
        current = self.get_picard_plugins().get(plugin_key, False)
        new_val = not current
        self.set_picard_plugin(plugin_key, new_val)
        return new_val

    @staticmethod
    def _normalize_ignore_str(text: str) -> str:
        if not text:
            return ""
        import re
        import unicodedata
        nfkd = unicodedata.normalize("NFKD", text)
        clean = "".join([c for c in nfkd if not unicodedata.combining(c)])
        clean = re.sub(r"[^\w\s]", " ", clean.lower())
        return " ".join(clean.split())

    def get_ignored(self) -> List[Dict[str, Any]]:
        """Returns the list of discarded/ignored songs or albums."""
        ignored = self.state.get("__ignored__", [])
        return ignored if isinstance(ignored, list) else []

    def is_ignored(self, name: str, artist: Optional[str] = None) -> bool:
        """Checks if a track title or album name is in the ignored list."""
        if not name:
            return False
        norm_name = self._normalize_ignore_str(name)
        norm_artist = self._normalize_ignore_str(artist) if artist else ""

        for item in self.get_ignored():
            item_name = item.get("norm_name", "")
            item_artist = item.get("norm_artist", "")

            # Exact or clean substring match
            name_match = (
                norm_name == item_name
                or (len(norm_name) >= 4 and norm_name in item_name)
                or (len(item_name) >= 4 and item_name in norm_name)
            )
            if not name_match:
                continue

            # If the item specified an artist, ensure artist matches if provided
            if item_artist and norm_artist:
                artist_match = (
                    norm_artist == item_artist
                    or norm_artist in item_artist
                    or item_artist in norm_artist
                )
                if not artist_match:
                    continue

            return True
        return False

    def add_ignored(self, name: str, artist: Optional[str] = None) -> bool:
        """Adds a track, album, or query to the persistent ignored list."""
        if not name or not name.strip():
            return False
        name_clean = name.strip()
        artist_clean = artist.strip() if artist else ""

        norm_name = self._normalize_ignore_str(name_clean)
        norm_artist = self._normalize_ignore_str(artist_clean)

        ignored = self.get_ignored()
        for item in ignored:
            if item.get("norm_name") == norm_name:
                if not norm_artist or item.get("norm_artist") == norm_artist:
                    return False  # Already present

        entry = {
            "name": name_clean,
            "artist": artist_clean,
            "norm_name": norm_name,
            "norm_artist": norm_artist,
            "added_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        ignored.append(entry)
        self.state["__ignored__"] = ignored
        self.save()
        logger.info("Added to ignored list: '%s' (artist: '%s')", name_clean, artist_clean)
        return True

    def remove_ignored(self, query: str) -> bool:
        """Removes an item from the ignored list by exact name, substring, or 1-based index."""
        ignored = self.get_ignored()
        if not ignored:
            return False

        # Try index match
        try:
            idx = int(query.strip())
            if 1 <= idx <= len(ignored):
                removed = ignored.pop(idx - 1)
                self.state["__ignored__"] = ignored
                self.save()
                logger.info("Removed from ignored list by index %d: '%s'", idx, removed.get("name"))
                return True
        except ValueError:
            pass

        norm_query = self._normalize_ignore_str(query)
        initial_len = len(ignored)
        new_ignored = []
        removed_any = False
        for item in ignored:
            item_norm = item.get("norm_name", "")
            if item_norm == norm_query or (len(norm_query) >= 3 and norm_query in item_norm):
                removed_any = True
                logger.info("Removed from ignored list: '%s'", item.get("name"))
            else:
                new_ignored.append(item)

        if removed_any:
            self.state["__ignored__"] = new_ignored
            self.save()
            return True
        return False

    def clear_ignored(self) -> int:
        """Clears all entries from the ignored list and returns the number cleared."""
        count = len(self.get_ignored())
        self.state["__ignored__"] = []
        self.save()
        logger.info("Cleared all %d ignored items", count)
        return count

