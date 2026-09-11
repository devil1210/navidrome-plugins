#!/usr/bin/env python3
"""
Process Downloads Folder with PicardEngine.
Enriches pending downloads in /media/music/downloads with Picard plugins
and relocates them to the appropriate library directory according to
the official Picard naming script.
"""

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from mutagen.mp4 import MP4
except ImportError:
    MP4 = None

from dotenv import load_dotenv

from modules.navidrome import NavidromeClient
from modules.picard_engine import PicardEngine
from modules.state import StateTracker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("process_downloads")


def inspect_album_metadata(album_dir: Path) -> Tuple[str, str, Optional[str], bool, List[Path]]:
    """Reads metadata tags from the first audio file in the folder."""
    audio_files = sorted([
        f for f in album_dir.iterdir()
        if f.is_file() and f.suffix.lower() in (".m4a", ".mp3", ".flac", ".opus")
    ])
    if not audio_files:
        return "", "", None, False, []

    first_file = audio_files[0]
    album_name = album_dir.name
    album_artist = ""
    date = None
    is_single = len(audio_files) == 1

    if MP4 is not None and first_file.suffix.lower() == ".m4a":
        try:
            mp4 = MP4(str(first_file))
            if mp4.tags:
                if "©alb" in mp4.tags and mp4.tags["©alb"]:
                    album_name = mp4.tags["©alb"][0]
                if "aART" in mp4.tags and mp4.tags["aART"]:
                    album_artist = mp4.tags["aART"][0]
                elif "©ART" in mp4.tags and mp4.tags["©ART"]:
                    album_artist = mp4.tags["©ART"][0]

                if "©day" in mp4.tags and mp4.tags["©day"]:
                    date = str(mp4.tags["©day"][0])

                if "----:com.apple.iTunes:RELEASETYPE" in mp4.tags:
                    raw_type = bytes(mp4.tags["----:com.apple.iTunes:RELEASETYPE"][0]).decode("utf-8", errors="ignore").lower()
                    if "single" in raw_type:
                        is_single = True
                    elif "album" in raw_type or "ep" in raw_type:
                        is_single = False
        except Exception as e:
            logger.debug("Could not read tags from %s: %s", first_file.name, e)

    if not album_artist:
        album_artist = "Unknown Artist"

    return album_name, album_artist, date, is_single, audio_files


def main():
    parser = argparse.ArgumentParser(description="Process and relocate pending music downloads")
    parser.add_argument("--downloads-dir", default=os.getenv("DOWNLOADS_DIR", "/media/music/downloads"), help="Downloads folder")
    parser.add_argument("--library-dir", default=os.getenv("MUSIC_DIR", "/media/music"), help="Library root folder")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without writing or moving files")
    parser.add_argument("--folder", help="Specific folder name inside downloads to process")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of folders to process (0 = all)")
    parser.add_argument("--no-scan", action="store_true", help="Do not trigger Navidrome scan after processing")
    parser.add_argument("--uid", type=int, default=1000, help="Owner UID for files (default 1000)")
    parser.add_argument("--gid", type=int, default=1000, help="Group GID for files (default 1000)")
    args = parser.parse_args()

    downloads_path = Path(args.downloads_dir).resolve()
    library_path = Path(args.library_dir).resolve()

    if not downloads_path.exists():
        logger.error("Downloads folder does not exist: %s", downloads_path)
        sys.exit(1)
    if not library_path.exists():
        logger.error("Library folder does not exist: %s", library_path)
        sys.exit(1)

    # Load active plugins configuration
    state_file = Path(__file__).parent / ".state.json"
    tracker = StateTracker(state_file)
    plugins_cfg = tracker.get_picard_plugins()

    logger.info("Starting Downloads Folder Processor")
    logger.info("Downloads path: %s", downloads_path)
    logger.info("Library path:   %s", library_path)
    logger.info("Dry run mode:   %s", args.dry_run)
    active_plugins = [k for k, v in plugins_cfg.items() if v]
    logger.info("Active Picard plugins (%d/%d): %s", len(active_plugins), len(plugins_cfg), ", ".join(active_plugins))

    # Scan candidate folders
    if args.folder:
        target_dir = downloads_path / args.folder
        if not target_dir.exists() or not target_dir.is_dir():
            logger.error("Specified folder does not exist: %s", target_dir)
            sys.exit(1)
        folders = [target_dir]
    else:
        folders = sorted([
            p for p in downloads_path.iterdir()
            if p.is_dir() and not p.name.startswith((".", "_"))
        ])

    logger.info("Found %d folder candidates in downloads", len(folders))

    processed_count = 0
    relocated_count = 0
    skipped_count = 0
    error_count = 0

    for idx, folder in enumerate(folders, 1):
        if args.limit and processed_count >= args.limit:
            logger.info("Reached limit of %d folders. Stopping.", args.limit)
            break

        album_name, album_artist, date, is_single, audio_files = inspect_album_metadata(folder)
        if not audio_files:
            logger.warning("[%d/%d] Skipping '%s': No audio files found", idx, len(folders), folder.name)
            skipped_count += 1
            continue

        processed_count += 1
        logger.info(
            "[%d/%d] Processing '%s' (%d tracks) | Artist: '%s' | Album: '%s' | Single: %s | Date: %s",
            idx,
            len(folders),
            folder.name,
            len(audio_files),
            album_artist,
            album_name,
            is_single,
            date or "N/A",
        )

        if args.dry_run:
            # Simulate naming script output
            sim_album = album_name
            sim_artist = album_artist
            if plugins_cfg.get("hyphen_unicode", True):
                sim_album = PicardEngine.normalize_hyphens(sim_album)
                sim_artist = PicardEngine.normalize_hyphens(sim_artist)
            if plugins_cfg.get("auto_romanizer", True):
                if PicardEngine.contains_japanese(sim_album):
                    sim_album = PicardEngine.romanize_japanese(sim_album, mode="auto", max_dual_len=65, fallback_long=True)
                if PicardEngine.contains_japanese(sim_artist):
                    sim_artist = PicardEngine.romanize_japanese(sim_artist, mode="auto", max_dual_len=65, fallback_long=True)
            if plugins_cfg.get("release_type", True):
                sim_album = PicardEngine.apply_release_type(sim_album, is_single=is_single)

            sample_title = audio_files[0].stem
            if MP4 is not None and audio_files[0].suffix.lower() == ".m4a":
                try:
                    mp4_sample = MP4(str(audio_files[0]))
                    if mp4_sample.tags and "©nam" in mp4_sample.tags:
                        sample_title = mp4_sample.tags["©nam"][0]
                except Exception:
                    pass
            if plugins_cfg.get("hyphen_unicode", True):
                sample_title = PicardEngine.normalize_hyphens(sample_title)
            if plugins_cfg.get("enhanced_titles", True):
                sample_title = PicardEngine.enhance_title(sample_title)
            if plugins_cfg.get("auto_romanizer", True) and PicardEngine.contains_japanese(sample_title):
                sample_title = PicardEngine.romanize_japanese(sample_title, mode="auto", max_dual_len=65, fallback_long=True)

            rel_folder, first_rel_file = PicardEngine.evaluate_naming_script(
                artist=sim_artist,
                album=sim_album,
                title=sample_title,
                albumartist=sim_artist,
                date=date,
                is_compilation=is_single is False and "various" in sim_artist.lower(),
            )
            dest_dir = library_path / rel_folder
            logger.info("   [DRY-RUN] -> Destination directory: %s", dest_dir)
            logger.info("   [DRY-RUN] -> Sample track filename:  %s", first_rel_file)
            relocated_count += 1
        else:
            try:
                final_dest = PicardEngine.process_and_relocate_album(
                    temp_album_dir=folder,
                    library_root=library_path,
                    album_name=album_name,
                    album_artist=album_artist,
                    date=date,
                    is_single=is_single,
                    plugins_config=plugins_cfg,
                    uid=args.uid,
                    gid=args.gid,
                )
                if final_dest.resolve() != folder.resolve():
                    relocated_count += 1
                    logger.info("   [OK] Relocated to: %s", final_dest)
                else:
                    logger.warning("   [SKIP] Kept in original folder: %s", folder)
            except Exception as e:
                error_count += 1
                logger.error("   [ERROR] Failed to process '%s': %s", folder.name, e, exc_info=True)

    logger.info("=" * 60)
    logger.info(
        "Processing finished: %d examined, %d relocated, %d skipped, %d errors",
        processed_count,
        relocated_count,
        skipped_count,
        error_count,
    )

    if not args.dry_run and not args.no_scan and relocated_count > 0:
        logger.info("Triggering Navidrome library scan...")
        try:
            env_file = Path(__file__).parent / ".env"
            if env_file.exists():
                load_dotenv(dotenv_path=env_file)
            else:
                load_dotenv()
            nav_url = os.getenv("NAVIDROME_URL", "http://localhost:4533")
            nav_user = os.getenv("NAVIDROME_USER", "admin")
            nav_pass = os.getenv("NAVIDROME_PASSWORD", "")
            navidrome = NavidromeClient(nav_url, nav_user, nav_pass)
            navidrome.start_scan(full_scan=False)
            logger.info("Navidrome scan triggered successfully.")
        except Exception as e:
            logger.warning("Could not trigger Navidrome scan: %s", e)


if __name__ == "__main__":
    main()
