#!/usr/bin/env python3
"""
Sync orchestration between Navidrome playlists, ListenBrainz, YouTube Music, and Telegram.
"""

import concurrent.futures
import logging
import math
import re
from typing import Any, Dict, List, Optional, Set, Tuple

_kakasi_inst = None
_kakasi_initialized = False

def _get_kakasi():
    global _kakasi_inst, _kakasi_initialized
    if not _kakasi_initialized:
        _kakasi_initialized = True
        try:
            import pykakasi
            _kakasi_inst = pykakasi.kakasi()
        except ImportError:
            _kakasi_inst = None
    return _kakasi_inst

from .enricher import ListenBrainzEnricher
from .models import MissingTrack, ResolutionResult, ResolvedAlbum, ResolvedTrack
from .navidrome import NavidromeClient
from .parser import TrackParser
from .resolver import YouTubeMusicResolver
from .state import StateTracker
from .telegram import TelegramSender

logger = logging.getLogger("listenbrainz_notifier")


def norm_title(s: str) -> str:
    clean = re.sub(r"\s*[\(\[].*?[\)\]]", "", s or "").strip()
    clean = re.sub(r"^\d+[\s\.\-_]+", "", clean).strip()
    clean = clean.replace("&", " and ")
    clean = clean.replace("’", "'").replace("`", "'")
    clean = clean.replace("ō", "o").replace("ū", "u").replace("ā", "a").replace("ē", "e").replace("ī", "i")
    clean = clean.replace("ou", "o").replace("uu", "u")
    clean = re.sub(r"\s*-\s*(english|japanese|spanish|single|album|radio|edit|ver|version|remaster|remastered).*", "", clean, flags=re.I).strip()
    return re.sub(r"[^\w\s]", "", clean.lower()).strip()


def to_romaji(s: str) -> str:
    kakasi = _get_kakasi()
    if not kakasi or not s:
        return s
    if any('\u3040' <= ch <= '\u30ff' or '\u4e00' <= ch <= '\u9faf' for ch in s):
        try:
            return " ".join(x["passport"] for x in kakasi.convert(s)).strip()
        except Exception:
            pass
    return s


def titles_match(t1: str, t2: str) -> bool:
    n1 = norm_title(t1)
    n2 = norm_title(t2)
    if not n1 or not n2:
        return False
    if n1 == n2 or n1.replace(" ", "") == n2.replace(" ", ""):
        return True
    r1 = norm_title(to_romaji(t1))
    r2 = norm_title(to_romaji(t2))
    if r1 and (r1 == n2 or r1.replace(" ", "") == n2.replace(" ", "")):
        return True
    if r2 and (r2 == n1 or r2.replace(" ", "") == n1.replace(" ", "")):
        return True
    if r1 and r2 and (r1 == r2 or r1.replace(" ", "") == r2.replace(" ", "")):
        return True
    c1 = n1.replace(" ", "")
    c2 = n2.replace(" ", "")
    if len(c1) >= 4 and len(c2) >= 4 and (c1 in c2 or c2 in c1):
        return True
    if len(c1) >= 5 and len(c2) >= 5 and (c1[:5] in c2 or c2[:5] in c1):
        return True
    return False


def process_playlist_text(
    playlist_name: str,
    raw_text: str,
    telegram: Optional[TelegramSender],
    fallback_to_track: bool = True,
    dry_run: bool = False,
    enricher: Optional[ListenBrainzEnricher] = None,
    navidrome: Optional[NavidromeClient] = None,
    playlist_id: Optional[str] = None,
    batch_number: int = 1,
    batch_size: int = 20,
    state_tracker: Optional[StateTracker] = None,
) -> bool:
    """Processes raw text or comment string and dispatches notification."""
    missing_text = TrackParser.extract_missing_text(raw_text)
    if not missing_text:
        # If no header, treat raw_text as list
        missing_text = raw_text

    tracks = TrackParser.parse_missing_tracks(missing_text)
    if not tracks:
        logger.info("No missing tracks found in playlist text.")
        return True

    logger.info("Parsed %d missing track(s) for '%s'", len(tracks), playlist_name)

    resolved_and_discarded_titles: Set[str] = set()

    # Filter out discarded/ignored tracks early
    if state_tracker:
        before_count = len(tracks)
        active_tracks = []
        for t in tracks:
            if state_tracker.is_ignored(t.title, t.artist):
                resolved_and_discarded_titles.add(t.title)
            else:
                active_tracks.append(t)
        tracks = active_tracks
        if len(tracks) < before_count:
            logger.info("Filtered out %d discarded track(s) for '%s'", before_count - len(tracks), playlist_name)

    # 1. Enrich with official artist metadata from ListenBrainz if missing
    if enricher:
        tracks = enricher.enrich_tracks(tracks)
        if state_tracker:
            active_tracks = []
            for t in tracks:
                if state_tracker.is_ignored(t.title, t.artist):
                    resolved_and_discarded_titles.add(t.title)
                else:
                    active_tracks.append(t)
            tracks = active_tracks

    # 2. Intelligent Library Pre-Check: verify against local Navidrome library
    if navidrome:
        truly_missing: List[MissingTrack] = []
        matched_locally: List[Tuple[MissingTrack, Dict[str, Any]]] = []

        if len(tracks) <= 2:
            match_results = [(t, navidrome.find_best_match(t.title, t.artist)) for t in tracks]
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
                futures = [executor.submit(navidrome.find_best_match, t.title, t.artist) for t in tracks]
                match_results = [(t, f.result()) for t, f in zip(tracks, futures)]

        for t, best_cand in match_results:
            if best_cand and best_cand.get("id"):
                matched_locally.append((t, best_cand))
            else:
                truly_missing.append(t)

        if matched_locally:
            logger.info(
                "Found %d/%d tracks already in local Navidrome library for '%s'!",
                len(matched_locally),
                len(tracks),
                playlist_name,
            )
            for t, s in matched_locally:
                logger.info(
                    "  ✓ In library: '%s' by '%s' -> [ID: %s] '%s' by '%s' ('%s')",
                    t.title,
                    t.artist,
                    s.get("id"),
                    s.get("title"),
                    s.get("artist"),
                    s.get("album"),
                )
                resolved_and_discarded_titles.add(t.title)

            if playlist_id and not dry_run:
                song_ids = list(dict.fromkeys([s["id"] for _, s in matched_locally]))
                logger.info("Auto-injecting %d unique song(s) into Navidrome playlist '%s' (ID: %s)...", len(song_ids), playlist_name, playlist_id)
                navidrome.add_tracks_to_playlist(playlist_id, song_ids)

        tracks = truly_missing

    if not tracks:
        if navidrome and playlist_id and not dry_run and resolved_and_discarded_titles:
            new_comment = TrackParser.rebuild_comment_without_tracks(raw_text, resolved_and_discarded_titles)
            if new_comment != raw_text:
                navidrome.update_playlist_comment(playlist_id, new_comment)
                logger.info("Updated comment for playlist '%s'.", playlist_name)
        logger.info(
            "All tracks for '%s' are already present in the local library! Nothing to download from YouTube Music.",
            playlist_name,
        )
        return True

    resolver = YouTubeMusicResolver()
    full_result = resolver.resolve(tracks, fallback_to_track=fallback_to_track)

    logger.info(
        "Resolved %d album(s), %d track(s), %d unresolved for '%s'.",
        len(full_result.albums),
        len(full_result.tracks),
        len(full_result.unresolved),
        playlist_name,
    )

    # 3. Post-Resolution Library Deduplication (Layer 2)
    if navidrome:
        post_matched_song_ids: List[str] = []
        filtered_albums: List[ResolvedAlbum] = []
        for alb in full_result.albums:
            existing = navidrome.find_album_match(alb.album_name, alb.artist_name)
            if existing:
                nav_tracks = navidrome.get_album_tracks(existing.get("id", ""))
                all_present = True
                matched_ids_for_alb = []
                for t_idx, t_name in enumerate(alb.tracks):
                    matched_s = None
                    # Try matching with original parsed track name
                    for nt in nav_tracks:
                        if titles_match(t_name, nt.get("title", "")):
                            matched_s = nt
                            break
                    # If not matched, try matching with the resolved YouTube Music song title
                    if not matched_s and getattr(alb, "resolved_tracks", []):
                        res_track = alb.resolved_tracks[t_idx] if t_idx < len(alb.resolved_tracks) else None
                        if res_track:
                            for nt in nav_tracks:
                                if titles_match(res_track, nt.get("title", "")):
                                    matched_s = nt
                                    break
                    if not matched_s and getattr(alb, "resolved_tracks", []):
                        for res_track in alb.resolved_tracks:
                            for nt in nav_tracks:
                                if titles_match(res_track, nt.get("title", "")):
                                    matched_s = nt
                                    break
                            if matched_s:
                                break

                    if matched_s:
                        if matched_s.get("id"):
                            matched_ids_for_alb.append(matched_s["id"])
                    else:
                        all_present = False

                if all_present and alb.tracks:
                    logger.info("El álbum '%s' de '%s' ya tiene todas sus pistas requeridas en Navidrome. Omitiendo de pendientes.", alb.album_name, alb.artist_name)
                    post_matched_song_ids.extend(matched_ids_for_alb)
                    for t in alb.tracks:
                        resolved_and_discarded_titles.add(t)
                    continue
                else:
                    alb.is_partial = True
                    alb.nav_present_tracks = len(matched_ids_for_alb)
                    alb.nav_total_tracks = len(nav_tracks)

            filtered_albums.append(alb)

        filtered_tracks: List[ResolvedTrack] = []
        for trk in full_result.tracks:
            m = navidrome.find_best_match(trk.title, trk.artist)
            if m and m.get("id"):
                logger.info("La pista suelta '%s' de '%s' ya existe en Navidrome (ID: %s). Omitiendo de pendientes.", trk.title, trk.artist, m.get("id"))
                post_matched_song_ids.append(m["id"])
                resolved_and_discarded_titles.add(trk.title)
                continue
            filtered_tracks.append(trk)

        if post_matched_song_ids and playlist_id and not dry_run:
            uniq_ids = list(dict.fromkeys(post_matched_song_ids))
            logger.info("Auto-inyectando %d canción(es) detectadas tras resolución en la playlist '%s'...", len(uniq_ids), playlist_name)
            navidrome.add_tracks_to_playlist(playlist_id, uniq_ids)

        full_result.albums = filtered_albums
        full_result.tracks = filtered_tracks

    # 4. Post-Resolution Ignored Filter (Albums and Tracks)
    if state_tracker:
        final_albums: List[ResolvedAlbum] = []
        for alb in full_result.albums:
            if state_tracker.is_ignored(alb.album_name, alb.artist_name):
                logger.info("Omitiendo álbum descartado por el usuario: '%s' de '%s'", alb.album_name, alb.artist_name)
                for t in alb.tracks:
                    resolved_and_discarded_titles.add(t)
                continue
            all_tracks_ignored = all(state_tracker.is_ignored(t, alb.artist_name) for t in alb.tracks)
            if all_tracks_ignored and alb.tracks:
                logger.info("Omitiendo álbum '%s' porque todas sus pistas requeridas están descartadas", alb.album_name)
                for t in alb.tracks:
                    resolved_and_discarded_titles.add(t)
                continue
            final_albums.append(alb)
        full_result.albums = final_albums

        final_tracks: List[ResolvedTrack] = []
        for trk in full_result.tracks:
            if state_tracker.is_ignored(trk.title, trk.artist):
                logger.info("Omitiendo tema suelto descartado por el usuario: '%s' de '%s'", trk.title, trk.artist)
                resolved_and_discarded_titles.add(trk.title)
                continue
            final_tracks.append(trk)
        full_result.tracks = final_tracks

    # 5. Clean playlist comment in Navidrome for any resolved or discarded tracks
    if navidrome and playlist_id and not dry_run and resolved_and_discarded_titles:
        new_comment = TrackParser.rebuild_comment_without_tracks(raw_text, resolved_and_discarded_titles)
        if new_comment != raw_text:
            navidrome.update_playlist_comment(playlist_id, new_comment)
            logger.info("Updated comment for playlist '%s' to remove %d satisfied/discarded tracks: %s", playlist_name, len(resolved_and_discarded_titles), resolved_and_discarded_titles)
            raw_text = new_comment

    if not full_result.albums and not full_result.tracks:
        logger.info(
            "Todas las pistas y álbumes para '%s' ya están presentes en la biblioteca local tras resolución. Nada pendiente por descargar.",
            playlist_name,
        )
        return True

    total_albums = len(full_result.albums)
    total_singles = len(full_result.tracks)

    if batch_size > 0 and total_albums > 0:
        total_batches = max(1, math.ceil(total_albums / batch_size))
        batch_number = max(1, min(batch_number, total_batches))
        start_idx = (batch_number - 1) * batch_size
        end_idx = start_idx + batch_size
        sliced_albums = full_result.albums[start_idx:end_idx]
        sliced_tracks = full_result.tracks if batch_number == total_batches else []
    else:
        total_batches = 1
        batch_number = 1
        sliced_albums = full_result.albums
        sliced_tracks = full_result.tracks

    result = ResolutionResult(
        albums=sliced_albums,
        tracks=sliced_tracks,
        unresolved=full_result.unresolved,
        total_albums_detected=total_albums,
        total_tracks_detected=total_singles,
        batch_number=batch_number,
        total_batches=total_batches,
        batch_size=batch_size,
    )

    if state_tracker:
        state_tracker.save_last_batch(
            playlist_name=playlist_name,
            batch_number=batch_number,
            batch_size=batch_size,
            total_batches=total_batches,
            albums=[{"url": a.url, "album_name": a.album_name, "artist_name": a.artist_name, "tracks": a.tracks} for a in sliced_albums],
            tracks=[{"url": t.url, "title": t.title, "artist": t.artist} for t in sliced_tracks],
        )

    if dry_run or not telegram:
        batch_info = f" (Lote {result.batch_number}/{result.total_batches})" if result.total_batches > 1 else ""
        print("\n" + "=" * 60)
        print(f"[DRY-RUN] Output for MediaHuman — {playlist_name}{batch_info}")
        print("=" * 60)
        urls_block = "\n".join(result.all_urls)
        print(urls_block)
        print("=" * 60)
        print("Detected Content Details:")
        for alb in result.albums:
            print(f"  • [ALBUM] {alb.artist_name} - {alb.album_name} ({len(alb.tracks)} track(s)) -> {alb.url}")
        for trk in result.tracks:
            print(f"  • [TRACK] {trk.artist} - {trk.title} -> {trk.url}")
        print("=" * 60 + "\n")
        return True

    return telegram.send_notification(playlist_name, result, total_searched=len(tracks))


def run_sync(
    navidrome: NavidromeClient,
    telegram: TelegramSender,
    state_tracker: StateTracker,
    target_names: Set[str],
    fallback_to_track: bool,
    force: bool,
    dry_run: bool,
    enricher: Optional[ListenBrainzEnricher] = None,
    batch_number: int = 1,
    batch_size: int = 20,
) -> Tuple[int, int]:
    """
    Checks Navidrome for updated ListenBrainz playlists and processes them.
    Returns (found_count, processed_count).
    """
    logger.info("Checking Navidrome playlists...")
    playlists = navidrome.get_playlists()
    if not playlists:
        logger.info("No playlists returned from Navidrome.")
        return 0, 0

    found_count = 0
    processed_count = 0
    for pls in playlists:
        pls_name = pls.get("name", "")
        pls_id = pls.get("id", "")
        comment = pls.get("comment", "")
        changed = pls.get("changed")

        # Check if playlist matches target name filter (if specified)
        if target_names and "*" not in target_names:
            name_matches = any(target.lower() in pls_name.lower() for target in target_names)
            if not name_matches:
                continue

        found_count += 1
        has_matched_comment = ("Tracks not matched" in comment) or ("Tracks not found in library" in comment)
        if not has_matched_comment:
            # If comment wasn't included in list response, fetch full details to check
            details = navidrome.get_playlist_details(pls_id)
            if details:
                comment = details.get("comment", "")
                changed = details.get("changed", changed)
                has_matched_comment = ("Tracks not matched" in comment) or ("Tracks not found in library" in comment)

        if not has_matched_comment:
            continue

        logger.info("Found relevant playlist: '%s' (ID: %s)", pls_name, pls_id)

        if not force and state_tracker.is_already_processed(pls_id, comment, changed):
            logger.info("Playlist '%s' has not changed since last check. Skipping.", pls_name)
            continue

        success = process_playlist_text(
            playlist_name=pls_name,
            raw_text=comment,
            telegram=telegram,
            fallback_to_track=fallback_to_track,
            dry_run=dry_run,
            enricher=enricher,
            navidrome=navidrome,
            playlist_id=pls_id,
            batch_number=batch_number,
            batch_size=batch_size,
            state_tracker=state_tracker,
        )

        if success and not dry_run:
            # Fetch latest comment in case it was cleaned
            updated_details = navidrome.get_playlist_details(pls_id)
            latest_comment = updated_details.get("comment", comment) if updated_details else comment
            state_tracker.mark_processed(pls_id, latest_comment, changed)
            processed_count += 1

    if processed_count == 0 and not dry_run:
        logger.info("All playlists up to date.")

    try:
        import gc
        gc.collect()
    except Exception:
        pass

    return found_count, processed_count
