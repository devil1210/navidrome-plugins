#!/usr/bin/env python3
"""
Unify cross-genre artist folders between /media/music/General and /media/music/J-Music.
Consolidates artist albums into their true genre home directory.
"""

import os
import re
import sys
import shutil
import unicodedata
from pathlib import Path
from mutagen.mp4 import MP4
from dotenv import load_dotenv

load_dotenv("/opt/navidrome-scripts/listenbrainz_notifier/.env")
from modules.navidrome import NavidromeClient


def norm_key(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[\W_]+", "", s).lower()
    return s


# Artists whose primary home is General
TARGET_GENERAL_KEYS = {
    "adrianbarba",
    "andrea",
    "animadrop",
    "attaque77",
    "beabadoobee",
    "chanchoenpiedra",
    "daftpunk",
    "damasgratis",
    "danielmunozyosmarujos",
    "danielmunozylesmarujos",
    "deeppurple",
    "hoshishiitake",
    "hoyomix",
    "illapu",
    "jimmyeatworld",
    "lecheromon",
    "linkinpark",
    "littlerichard",
    "lorchestracinematique",
    "losprisioneros",
    "nanaoakari",
    "panicatthedisco",
    "pesopluma",
    "rafaga",
    "scatmanjohn",
    "yukikajiura",
}

# Folders to completely skip from cross-genre unification
EXCLUDE_KEYS = {
    "soundtrack",
}


def update_audio_tags(audio_path: Path, canonical_artist: str):
    try:
        mp4 = MP4(str(audio_path))
        if mp4.tags is None:
            return
        modified = False
        if "aART" not in mp4.tags or mp4.tags["aART"] != [canonical_artist]:
            mp4.tags["aART"] = [canonical_artist]
            modified = True
        if modified:
            mp4.save()
    except Exception as e:
        pass


def safe_move_item(src: Path, dst: Path):
    """Safely move or copy+delete an item."""
    try:
        shutil.move(str(src), str(dst))
    except Exception as e:
        # Fallback: copy tree then remove
        if src.is_dir():
            shutil.copytree(str(src), str(dst), dirs_exist_ok=True)
            try:
                shutil.rmtree(str(src), ignore_errors=True)
            except Exception:
                pass
        else:
            shutil.copy2(str(src), str(dst))
            try:
                src.unlink()
            except Exception:
                pass


def merge_artist(src_dir: Path, target_dir: Path, artist_name: str, dry_run: bool = True):
    if not src_dir.exists() or src_dir.resolve() == target_dir.resolve():
        return

    print(f"\nMerging: '{src_dir}' -> '{target_dir}'")
    if not dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chown(str(target_dir), 1000, 1000)
            os.chmod(str(target_dir), 0o775)
        except Exception:
            pass

    for album_entry in list(src_dir.iterdir()):
        if not album_entry.is_dir() or album_entry.name.startswith("."):
            continue

        target_album_dir = target_dir / album_entry.name

        try:
            if not target_album_dir.exists():
                print(f"  -> Moving whole album '{album_entry.name}'")
                if not dry_run:
                    safe_move_item(album_entry, target_album_dir)
                    for af in target_album_dir.iterdir():
                        if af.is_file() and af.suffix.lower() == ".m4a":
                            update_audio_tags(af, artist_name)
            else:
                print(f"  -> Merging tracks into existing album '{album_entry.name}'")
                if not dry_run:
                    for track_file in list(album_entry.iterdir()):
                        dest_track_file = target_album_dir / track_file.name
                        if not dest_track_file.exists():
                            safe_move_item(track_file, dest_track_file)
                            if dest_track_file.suffix.lower() == ".m4a":
                                update_audio_tags(dest_track_file, artist_name)
                        else:
                            if track_file.stat().st_size == dest_track_file.stat().st_size:
                                track_file.unlink()
                            else:
                                dupe_dir = Path("/media/music/_duplicados_backup/cross_genre") / artist_name / album_entry.name
                                dupe_dir.mkdir(parents=True, exist_ok=True)
                                safe_move_item(track_file, dupe_dir / track_file.name)
                    try:
                        album_entry.rmdir()
                    except Exception:
                        pass
        except Exception as err:
            print(f"  [error moving {album_entry.name}]: {err}")

    if not dry_run:
        try:
            remaining = list(src_dir.iterdir())
            if not remaining:
                src_dir.rmdir()
                print(f"  Removed empty folder: '{src_dir}'")
            else:
                print(f"  [notice] Folder '{src_dir}' still has items: {[r.name for r in remaining]}")
        except Exception as e:
            pass

        # Fix permissions where permitted
        for root, dirs, files in os.walk(target_dir):
            for d in dirs:
                p = os.path.join(root, d)
                try: os.chown(p, 1000, 1000); os.chmod(p, 0o775)
                except Exception: pass
            for f in files:
                p = os.path.join(root, f)
                try: os.chown(p, 1000, 1000); os.chmod(p, 0o664)
                except Exception: pass


def main():
    dry_run = "--apply" not in sys.argv
    if dry_run:
        print("[DRY-RUN MODE] No changes will be made. Pass --apply to execute.")
    else:
        print("[APPLY MODE] Executing cross-genre consolidation...")

    base_gen = Path("/media/music/General")
    base_jm = Path("/media/music/J-Music")

    gen_map = {norm_key(d.name): d for d in base_gen.iterdir() if d.is_dir() and not d.name.startswith(".")}
    jm_map = {norm_key(d.name): d for d in base_jm.iterdir() if d.is_dir() and not d.name.startswith(".")}

    overlap_keys = [k for k in sorted(set(gen_map.keys()).intersection(set(jm_map.keys()))) if k not in EXCLUDE_KEYS]
    print(f"Found {len(overlap_keys)} overlapping artists between General and J-Music.\n")

    for k in overlap_keys:
        g_dir = gen_map[k]
        j_dir = jm_map[k]

        is_general = k in TARGET_GENERAL_KEYS or "danielmunoz" in k

        if is_general:
            target_dir = g_dir
            src_dir = j_dir
            artist_name = g_dir.name
        else:
            target_dir = j_dir
            src_dir = g_dir
            artist_name = j_dir.name

        merge_artist(src_dir, target_dir, artist_name, dry_run=dry_run)

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
