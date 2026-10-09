#!/usr/bin/env python3
"""
Playlist Aligner:
Maintains Navidrome playlists in the strict 1-to-N canonical order
defined by official ListenBrainz playlists (or other official sources).
"""

import logging
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

try:
    import pykakasi
    _kakasi_inst = pykakasi.kakasi()
except ImportError:
    _kakasi_inst = None

logger = logging.getLogger("listenbrainz_notifier")

# Short-lived in-memory cache for official playlists: mbid -> (timestamp, tracks)
_LBZ_CACHE: Dict[str, Tuple[float, List[Dict[str, str]]]] = {}
_LBZ_CACHE_TTL = 300  # 5 minutes


def normalize_text(s: Optional[str]) -> str:
    """Normalizes text for fuzzy matching by removing punctuation and brackets."""
    if not s:
        return ""
    clean = re.sub(r"\s*[\(\[].*?[\)\]]", "", s).strip()
    clean = re.sub(r"^\d+[\s\.\-_]+", "", clean).strip()
    clean = clean.replace("’", "'").replace("`", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"[^\w\s]", "", clean.lower()).strip()


def romanize_text(text: str) -> str:
    """Converts Japanese kanji/kana to Hepburn romaji if pykakasi is available."""
    if not text or not _kakasi_inst:
        return text
    try:
        conv = _kakasi_inst.convert(text)
        return " ".join([item["hepburn"] for item in conv if item.get("hepburn")])
    except Exception:
        return text


# Known alias mapping between common artist variants
ARTIST_ALIASES = {
    "zatomayo": "zutomayo",
    "zutomayo": "ずっと真夜中でいいのに",
    "ずっと真夜中でいいのに": "zutomayo",
}


def tracks_match(
    official_title: str,
    official_artist: str,
    cand_title: str,
    cand_artist: str,
) -> bool:
    """
    Checks if a candidate Navidrome track matches an official track by title and artist.
    Supports romanized titles, token-based artist overlap, and subtitle stripping.
    """
    norm_ot = normalize_text(official_title)
    norm_ct = normalize_text(cand_title)
    if not norm_ot or not norm_ct:
        return False

    # Title match
    t_match = (
        norm_ot == norm_ct
        or norm_ot.replace(" ", "") == norm_ct.replace(" ", "")
        or (len(norm_ot) >= 4 and norm_ot in norm_ct)
        or (len(norm_ct) >= 4 and norm_ct in norm_ot)
    )

    if not t_match and _kakasi_inst:
        rom_ot = normalize_text(romanize_text(official_title))
        rom_ct = normalize_text(romanize_text(cand_title))
        if rom_ot and rom_ct:
            t_match = (
                rom_ot == rom_ct
                or rom_ot.replace(" ", "") == rom_ct.replace(" ", "")
                or (len(rom_ot) >= 4 and rom_ot in rom_ct)
                or (len(rom_ct) >= 4 and rom_ct in rom_ot)
            )

    if not t_match:
        return False

    # Artist match
    norm_oa = normalize_text(official_artist)
    norm_ca = normalize_text(cand_artist)

    # Check aliases
    clean_oa = norm_oa.replace(" ", "")
    clean_ca = norm_ca.replace(" ", "")
    if ARTIST_ALIASES.get(clean_oa) == clean_ca or ARTIST_ALIASES.get(clean_ca) == clean_oa:
        return True

    a_match = (
        not norm_oa
        or not norm_ca
        or norm_oa == norm_ca
        or clean_oa == clean_ca
        or (len(norm_oa) >= 3 and norm_oa in norm_ca)
        or (len(norm_ca) >= 3 and norm_ca in norm_oa)
        or any(tok in norm_ca for tok in norm_oa.split() if len(tok) >= 3)
        or any(tok in norm_oa for tok in norm_ca.split() if len(tok) >= 3)
    )

    if not a_match and _kakasi_inst:
        rom_oa = normalize_text(romanize_text(official_artist))
        rom_ca = normalize_text(romanize_text(cand_artist))
        if rom_oa and rom_ca:
            a_match = (
                rom_oa == rom_ca
                or (len(rom_oa) >= 3 and rom_oa in rom_ca)
                or (len(rom_ca) >= 3 and rom_ca in norm_ca)
            )

    return a_match


def fetch_official_tracks(comment: str) -> Optional[List[Dict[str, str]]]:
    """
    Extracts the ListenBrainz playlist MBID from the comment and fetches the official tracklist.
    Returns list of dicts: [{'title': ..., 'artist': ...}, ...] or None if unavailable.
    """
    if not comment:
        return None

    m = re.search(r"https?://(?:api\.)?listenbrainz\.org/playlist/([0-9a-fA-F-]{36})", comment)
    if not m:
        return None

    mbid = m.group(1).lower()
    now = time.time()
    if mbid in _LBZ_CACHE:
        cached_time, cached_tracks = _LBZ_CACHE[mbid]
        if now - cached_time < _LBZ_CACHE_TTL:
            return cached_tracks

    url = f"https://api.listenbrainz.org/1/playlist/{mbid}"
    try:
        resp = requests.get(url, headers={"Accept": "application/json"}, timeout=15)
        if resp.status_code != 200:
            logger.warning("ListenBrainz API returned status %d for playlist %s", resp.status_code, mbid)
            return None
        data = resp.json()
        tracks_raw = data.get("playlist", {}).get("track", [])
        official_tracks: List[Dict[str, str]] = []
        for t in tracks_raw:
            official_tracks.append({
                "title": t.get("title", ""),
                "artist": t.get("creator", ""),
                "identifier": t.get("identifier", ""),
            })
        _LBZ_CACHE[mbid] = (now, official_tracks)
        return official_tracks
    except Exception as e:
        logger.warning("Failed to fetch official tracklist from ListenBrainz for %s: %s", mbid, e)
        return None


def align_tracks_to_official_order(
    official_tracks: List[Dict[str, str]],
    available_songs: List[Dict[str, Any]],
) -> List[str]:
    """
    Aligns available Navidrome songs to strictly follow the official tracklist order.
    Unmatched official songs are skipped (not yet available).
    Any leftover Navidrome songs not in official list are appended at the end.
    """
    ordered_ids: List[str] = []
    used_indices: Set[int] = set()

    for ot in official_tracks:
        o_title = ot.get("title", "")
        o_artist = ot.get("artist", "")

        best_cand = None
        best_idx = -1
        for i, cand in enumerate(available_songs):
            if i in used_indices:
                continue
            cand_id = cand.get("id")
            if not cand_id:
                continue
            cand_title = cand.get("title", "")
            cand_artist = cand.get("artist") or cand.get("displayArtist", "")
            if tracks_match(o_title, o_artist, cand_title, cand_artist):
                best_cand = cand
                best_idx = i
                break

        if best_cand and best_idx >= 0:
            used_indices.add(best_idx)
            ordered_ids.append(best_cand["id"])

    # Append any remaining available songs not matched to official list (prevents track loss)
    for i, cand in enumerate(available_songs):
        if i not in used_indices and cand.get("id"):
            ordered_ids.append(cand["id"])

    # Deduplicate while preserving order
    return list(dict.fromkeys(ordered_ids))


class ReorderResult:
    """Encapsulates the result of a playlist reordering operation with boolean compatibility."""

    def __init__(
        self,
        ok: bool,
        was_reordered: bool,
        track_count: int = 0,
        official_count: int = 0,
        message: str = "",
        playlist_name: str = "",
    ):
        self.ok = ok
        self.was_reordered = was_reordered
        self.track_count = track_count
        self.official_count = official_count
        self.message = message
        self.playlist_name = playlist_name

    def __bool__(self) -> bool:
        return self.ok

    def __repr__(self) -> str:
        return f"<ReorderResult ok={self.ok} reordered={self.was_reordered} count={self.track_count} msg='{self.message}'>"


def reorder_playlist_if_needed(
    navidrome: Any,
    playlist_id: str,
    comment: str,
    new_songs: Optional[List[Dict[str, Any]]] = None,
    new_song_ids: Optional[List[str]] = None,
) -> ReorderResult:
    """
    Reorders a Navidrome playlist to follow its official ListenBrainz sequence.
    If official tracklist is unavailable, falls back to regular add_tracks_to_playlist.
    Returns ReorderResult (which evaluates as boolean in boolean contexts).
    """
    if not navidrome or not playlist_id:
        return ReorderResult(ok=False, was_reordered=False, message="Cliente Navidrome o ID no proporcionado")

    official_tracks = fetch_official_tracks(comment)

    # Fallback if no official ListenBrainz sequence is defined for this playlist
    if not official_tracks:
        fallback_ids = []
        if new_songs:
            fallback_ids.extend([s["id"] for s in new_songs if isinstance(s, dict) and s.get("id")])
        if new_song_ids:
            fallback_ids.extend([sid for sid in new_song_ids if sid])
        if fallback_ids:
            ok = navidrome.add_tracks_to_playlist(playlist_id, fallback_ids)
            return ReorderResult(ok=ok, was_reordered=False, track_count=len(fallback_ids), message="Agregadas pistas al final (sin URL oficial de ListenBrainz)")
        return ReorderResult(ok=True, was_reordered=False, message="Sin pistas nuevas ni URL oficial de ListenBrainz")

    # Gather all candidate songs (existing in playlist + newly detected)
    details = navidrome.get_playlist_details(playlist_id)
    existing_entries: List[Dict[str, Any]] = []
    if details:
        raw_entries = details.get("entry", [])
        if isinstance(raw_entries, dict):
            existing_entries = [raw_entries]
        elif isinstance(raw_entries, list):
            existing_entries = [e for e in raw_entries if isinstance(e, dict)]

    pool: List[Dict[str, Any]] = list(existing_entries)
    known_ids: Set[str] = {e["id"] for e in pool if "id" in e}

    if new_songs:
        for s in new_songs:
            if isinstance(s, dict) and s.get("id") and s["id"] not in known_ids:
                pool.append(s)
                known_ids.add(s["id"])

    if new_song_ids:
        for sid in new_song_ids:
            if sid and sid not in known_ids:
                if hasattr(navidrome, "get_song"):
                    song_obj = navidrome.get_song(sid)
                    if song_obj:
                        pool.append(song_obj)
                        known_ids.add(sid)

    ordered_ids = align_tracks_to_official_order(official_tracks, pool)
    current_ids = [e["id"] for e in existing_entries if e.get("id")]

    pls_name = details.get("name", playlist_id) if details else playlist_id

    # If the playlist order already exactly matches the desired order, no API write needed
    if ordered_ids == current_ids:
        logger.info("Playlist '%s' is already in canonical official order (%d tracks).", pls_name, len(current_ids))
        return ReorderResult(
            ok=True,
            was_reordered=False,
            track_count=len(current_ids),
            official_count=len(official_tracks),
            message=f"Ya se encuentra en orden oficial ({len(current_ids)} pistas)",
            playlist_name=pls_name,
        )

    logger.info(
        "Re-sequencing playlist '%s' (%d tracks) to strictly match official ListenBrainz order...",
        pls_name,
        len(ordered_ids),
    )
    ok = navidrome.set_playlist_tracks(playlist_id, ordered_ids)
    if ok:
        logger.info("✓ Successfully reordered playlist '%s' in Navidrome.", pls_name)
        msg = f"Se reordenaron {len(ordered_ids)} pistas para coincidir con la lista oficial de ListenBrainz"
    else:
        logger.error("Failed to update playlist tracks for '%s' via Subsonic API.", pls_name)
        msg = "Error al actualizar pistas en Navidrome via API Subsonic"

    return ReorderResult(
        ok=ok,
        was_reordered=ok,
        track_count=len(ordered_ids),
        official_count=len(official_tracks),
        message=msg,
        playlist_name=pls_name,
    )
