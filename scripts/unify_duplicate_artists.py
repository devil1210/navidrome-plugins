#!/usr/bin/env python3
"""
Unify duplicate artist folders in /media/music/General and /media/music/J-Music
using official YouTube Music artist names.
"""

import os
import re
import sys
import shutil
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from mutagen.mp4 import MP4
import ytmusicapi
from dotenv import load_dotenv

load_dotenv("/opt/navidrome-scripts/listenbrainz_notifier/.env")
from modules.navidrome import NavidromeClient


def norm_key(s: str) -> str:
    """Normalize string for fuzzy grouping: strip accents, non-alphanumeric, lowercase."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[\W_]+", "", s).lower()
    return s


def sanitize_folder_name(name: str) -> str:
    """Apply safe folder naming according to Picard/Lidarr standard."""
    if not name:
        return "Unknown"
    # Normalize fancy apostrophes to standard ASCII apostrophe
    s = re.sub(r"[’´`‘]", "'", name)
    # Normalize unicode hyphens/dashes to standard ASCII hyphen
    s = re.sub(r"[\u2010\u2011\u2012\u2013\u2014\u2015\u2212]", "-", s)
    # Replace slashes with hyphen
    s = re.sub(r"[/\\|]", "-", s)
    # Replace other illegal chars with underscore
    s = re.sub(r'[<>:"?*]', "_", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip(". ") or "Unknown"


# Explicit canonical overrides for edge cases where YTM query can be ambiguous
CANONICAL_OVERRIDES = {
    "alexbuenoorquestaliberacion": "Alex Bueno & Orquesta Liberación",
    "adolescentsorquesta": "Adolescent's Orquesta",
    "aha": "a-ha",
    "rem": "R.E.M.",
}


def get_canonical_ytm_name(yt: ytmusicapi.YTMusic, candidates: List[str]) -> str:
    """Queries YouTube Music to get the official canonical artist name."""
    group_key = norm_key(candidates[0])
    if group_key in CANONICAL_OVERRIDES:
        return CANONICAL_OVERRIDES[group_key]

    clean_candidates = [re.sub(r"[’´`‘]", "'", c) for c in candidates]
    best_cand = max(clean_candidates, key=len)

    try:
        results = yt.search(best_cand, filter="artists")
        if results:
            yt_artist = results[0].get("artist", "")
            if yt_artist:
                n_yt = norm_key(yt_artist)
                n_cand = norm_key(best_cand)
                if n_yt == n_cand:
                    return yt_artist
                if n_yt in n_cand or n_cand in n_yt:
                    return yt_artist
    except Exception as e:
        print(f"  [warn] YTM search error for '{best_cand}': {e}")

    # Fallback: prefer the candidate with ASCII apostrophe and proper capitalization
    for c in candidates:
        if "'" in c and "´" not in c and "’" not in c:
            return c
    return candidates[0]


def find_duplicate_groups(base_dir: str) -> List[List[str]]:
    all_dirs = [d for d in os.listdir(base_dir) if os.path.isdir(os.path.join(base_dir, d)) and not d.startswith(".")]
    groups = defaultdict(list)
    for d in all_dirs:
        groups[norm_key(d)].append(d)

    # Manual pairings / special cases
    if "Adolescent’s" in all_dirs and "adolescentsorquesta" in groups:
        if "Adolescent’s" not in groups["adolescentsorquesta"]:
            groups["adolescentsorquesta"].append("Adolescent’s")

    if "Alex & Orquesta Liberación" in all_dirs and "Alex Bueno & Orquesta Liberación" in all_dirs:
        groups["alexbuenoorquestaliberacion"] = ["Alex Bueno & Orquesta Liberación", "Alex & Orquesta Liberación"]

    # Filter only groups with > 1 folder
    dupes = [v for k, v in groups.items() if len(v) > 1]
    return dupes


def update_audio_tags(audio_path: Path, canonical_artist: str):
    try:
        mp4 = MP4(str(audio_path))
        if mp4.tags is None:
            return
        modified = False
        if "aART" not in mp4.tags or mp4.tags["aART"] != [canonical_artist]:
            mp4.tags["aART"] = [canonical_artist]
            modified = True

        if "©ART" in mp4.tags and mp4.tags["©ART"]:
            curr_art = mp4.tags["©ART"][0]
            norm_curr = re.sub(r"[’´`‘]", "'", curr_art)
            if norm_key(curr_art) == norm_key(canonical_artist):
                if curr_art != canonical_artist:
                    mp4.tags["©ART"] = [canonical_artist]
                    modified = True
            elif norm_curr != curr_art:
                mp4.tags["©ART"] = [norm_curr]
                modified = True

        if modified:
            mp4.save()
    except Exception as e:
        print(f"    [err] Updating tags on {audio_path.name}: {e}")


def merge_artist_folders(base_dir: str, group: List[str], canonical_name: str, dry_run: bool = True):
    canonical_folder = sanitize_folder_name(canonical_name)
    target_dir = Path(base_dir) / canonical_folder

    print(f"\n=======================================================")
    print(f"Group: {group}")
    print(f"Canonical: '{canonical_name}' -> Target: '{canonical_folder}'")
    print(f"=======================================================")

    if not dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chown(str(target_dir), 1000, 1000)
            os.chmod(str(target_dir), 0o775)
        except Exception:
            pass

    for src_name in group:
        src_dir = Path(base_dir) / src_name
        if not src_dir.exists():
            continue
        if src_dir.resolve() == target_dir.resolve():
            continue

        print(f"  Merging from '{src_name}' into '{canonical_folder}'...")

        for album_entry in list(src_dir.iterdir()):
            if not album_entry.is_dir() or album_entry.name.startswith("."):
                continue

            target_album_dir = target_dir / album_entry.name

            if not target_album_dir.exists():
                print(f"    -> Moving whole album '{album_entry.name}'")
                if not dry_run:
                    shutil.move(str(album_entry), str(target_album_dir))
                    for af in target_album_dir.iterdir():
                        if af.is_file() and af.suffix.lower() == ".m4a":
                            update_audio_tags(af, canonical_name)
            else:
                print(f"    -> Merging tracks into existing album '{album_entry.name}'")
                if not dry_run:
                    for track_file in list(album_entry.iterdir()):
                        dest_track_file = target_album_dir / track_file.name
                        if not dest_track_file.exists():
                            shutil.move(str(track_file), str(dest_track_file))
                            if dest_track_file.suffix.lower() == ".m4a":
                                update_audio_tags(dest_track_file, canonical_name)
                        else:
                            if track_file.stat().st_size == dest_track_file.stat().st_size:
                                print(f"       Track '{track_file.name}' identical duplicate, removing redundant copy.")
                                track_file.unlink()
                            else:
                                dupe_dir = Path("/media/music/_duplicados_backup") / canonical_folder / album_entry.name
                                dupe_dir.mkdir(parents=True, exist_ok=True)
                                shutil.move(str(track_file), str(dupe_dir / track_file.name))
                                print(f"       Track '{track_file.name}' differs in size, moved to backup: {dupe_dir}")
                    try:
                        album_entry.rmdir()
                    except Exception:
                        pass

        if not dry_run:
            try:
                remaining = list(src_dir.iterdir())
                if not remaining:
                    src_dir.rmdir()
                    print(f"  Removed empty source folder: '{src_name}'")
                else:
                    print(f"  [notice] Folder '{src_name}' still has files: {[r.name for r in remaining]}")
            except Exception as e:
                print(f"  [err] Removing '{src_name}': {e}")

    # Ensure permissions
    if not dry_run and target_dir.exists():
        for root, dirs, files in os.walk(target_dir):
            for d in dirs:
                p = os.path.join(root, d)
                try:
                    os.chown(p, 1000, 1000)
                    os.chmod(p, 0o775)
                except Exception: pass
            for f in files:
                p = os.path.join(root, f)
                try:
                    os.chown(p, 1000, 1000)
                    os.chmod(p, 0o664)
                except Exception: pass


def clean_empty_artist_dirs(base_dir: str, dry_run: bool = True):
    """Removes empty artist directories."""
    p_base = Path(base_dir)
    if not p_base.exists():
        return
    for d in p_base.iterdir():
        if d.is_dir() and not d.name.startswith("."):
            try:
                items = list(d.iterdir())
                if not items:
                    print(f"Cleaning empty artist directory: {d}")
                    if not dry_run:
                        d.rmdir()
            except Exception:
                pass


def main():
    dry_run = "--apply" not in sys.argv
    if dry_run:
        print("[DRY-RUN MODE] No changes will be written. Run with --apply to execute.")
    else:
        print("[APPLY MODE] Executing changes...")

    yt = ytmusicapi.YTMusic()
    base_general = "/media/music/General"
    groups = find_duplicate_groups(base_general)

    print(f"Found {len(groups)} duplicate artist groups in {base_general}")

    for group in groups:
        canonical_name = get_canonical_ytm_name(yt, group)
        merge_artist_folders(base_general, group, canonical_name, dry_run=dry_run)

    clean_empty_artist_dirs(base_general, dry_run=dry_run)
    clean_empty_artist_dirs("/media/music/J-Music", dry_run=dry_run)

    if not dry_run:
        # Trigger Navidrome scan
        url = os.getenv("NAVIDROME_URL", "http://localhost:4533")
        user = os.getenv("NAVIDROME_USER", "admin")
        pwd = os.getenv("NAVIDROME_PASSWORD", "")
        if url and user:
            print("\nTriggering Navidrome scan...")
            client = NavidromeClient(url, user, pwd)
            res = client.start_scan(full_scan=False)
            print("Navidrome scan started:", res)

    print("\nDone!")


if __name__ == "__main__":
    main()
