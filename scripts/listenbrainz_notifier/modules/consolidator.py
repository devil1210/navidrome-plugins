#!/usr/bin/env python3
"""
Folder and Album Consolidator:
Detects and unifies fragmented album directories across the music library
and download cache, consolidating audio tracks into their canonical album home.
"""

import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("listenbrainz_notifier")


def _norm_folder_key(s: str) -> str:
    """Normalizes album or artist directory names for robust duplicate detection."""
    clean = re.sub(r"^\[\d{4}\]\s*-\s*", "", s or "").strip()
    clean = re.sub(r"\s*[\(\[].*?[\)\]]", "", clean).strip()
    clean = clean.replace("&", " and ").replace("’", "'").replace("`", "'")
    return re.sub(r"[^\w\s]", "", clean.lower()).strip()


class FolderConsolidator:
    """
    Consolidates fragmented album directories into their canonical artist/album home.
    Fixes split cards in Navidrome by uniting loose single-track downloads with existing albums.
    """

    def __init__(
        self,
        library_dir: Optional[Path] = None,
        download_dir: Optional[Path] = None,
        navidrome: Optional[Any] = None,
        uid: int = 1000,
        gid: int = 1000,
    ):
        self.library_dir = Path(library_dir) if library_dir else None
        self.download_dir = Path(download_dir) if download_dir else None
        self.navidrome = navidrome
        self.uid = uid
        self.gid = gid

    def consolidate_all(self, dry_run: bool = False) -> List[Dict[str, Any]]:
        """
        Scans download_dir and library_dir, discovering fragmented album folders
        and consolidating their contents into the primary canonical album folder.
        """
        results: List[Dict[str, Any]] = []
        if not self.library_dir and not self.download_dir:
            return results

        # 1. Discover all candidate album folders in category subdirectories
        candidate_categories = ["General", "J-Music", "Compilaciones", "Soundtracks/Worldwide", "Soundtracks/J-Music", "Soundtracks"]
        albums_by_name: Dict[str, List[Path]] = {}

        if self.library_dir and self.library_dir.is_dir():
            for cat in candidate_categories:
                c_dir = self.library_dir / cat
                if not c_dir.is_dir():
                    continue
                try:
                    for artist_item in c_dir.iterdir():
                        if not artist_item.is_dir() or artist_item.name.startswith((".", "_")):
                            continue
                        for alb_item in artist_item.iterdir():
                            if alb_item.is_dir() and not alb_item.name.startswith((".", "_")):
                                alb_k = _norm_folder_key(alb_item.name)
                                if alb_k:
                                    albums_by_name.setdefault(alb_k, []).append(alb_item)
                except Exception as e:
                    logger.debug("Error indexing category %s: %s", cat, e)

        # Also check downloads directory and top-level library for loose album folders
        loose_sources = []
        if self.download_dir and self.download_dir.is_dir():
            loose_sources.append(self.download_dir)
        if self.library_dir and self.library_dir.is_dir():
            loose_sources.append(self.library_dir)

        standard_top = {
            "general", "j-music", "compilaciones", "soundtracks", "downloads",
            "omitidos", "cookies", "sin-artista-del-album", "_duplicados_backup",
            "$recycle.bin", "system volume information", "_singles"
        }
        for src_root in loose_sources:
            try:
                for item in src_root.iterdir():
                    if not item.is_dir() or item.name.startswith((".", "_")) or item.name.lower() in standard_top:
                        continue
                    alb_k = _norm_folder_key(item.name)
                    if alb_k:
                        albums_by_name.setdefault(alb_k, []).append(item)
            except Exception as e:
                logger.debug("Error indexing loose folders in %s: %s", src_root, e)

        # 2. Find and merge duplicates
        consolidated_targets: Set[Path] = set()
        for alb_k, folders in albums_by_name.items():
            if len(folders) <= 1 or not alb_k:
                continue

            # Group folders by category + artist (or standalone loose folders)
            artist_groups: Dict[str, List[Path]] = {}
            for f in folders:
                if f.parent in loose_sources or f.parent.name in candidate_categories:
                    artist_key = "__standalone__"
                else:
                    cat_name = f.parent.parent.name if f.parent.parent else ""
                    artist_key = f"{cat_name}:{_norm_folder_key(f.parent.name)}"
                artist_groups.setdefault(artist_key, []).append(f)

            # If there's a standalone folder and exactly one specific category/artist folder, combine them
            if "__standalone__" in artist_groups and len(artist_groups) == 2:
                specific_k = [k for k in artist_groups if k != "__standalone__"][0]
                artist_groups[specific_k].extend(artist_groups.pop("__standalone__"))

            for art_k, group in artist_groups.items():
                if len(group) <= 1:
                    continue

                def score_folder(p: Path) -> int:
                    score = 0
                    parts = p.parts
                    if any(c in parts for c in ["General", "J-Music", "Compilaciones", "Soundtracks"]):
                        score += 50
                    if re.match(r"^\[\d{4}\]\s*-\s*", p.name):
                        score += 20
                    if len(parts) >= 4:
                        score += 10
                    try:
                        audio_count = len([x for x in p.iterdir() if x.is_file() and x.suffix.lower() in ('.m4a', '.mp3', '.flac', '.opus', '.webm')])
                    except Exception:
                        audio_count = 0
                    score += audio_count
                    return score

                sorted_folders = sorted(group, key=score_folder, reverse=True)
                canonical = sorted_folders[0]
                sources = sorted_folders[1:]

                moved_files_total = 0
                for src in sources:
                    if src == canonical or not src.is_dir():
                        continue
                    try:
                        audio_files = [f for f in src.iterdir() if f.is_file() and f.suffix.lower() in ('.m4a', '.mp3', '.flac', '.opus', '.webm')]
                    except Exception:
                        audio_files = []

                    if not audio_files:
                        # Clean up empty source directory
                        if not dry_run:
                            try:
                                shutil.rmtree(src, ignore_errors=True)
                            except Exception:
                                pass
                        continue

                    for f in audio_files:
                        dest_file = canonical / f.name
                        if dest_file.exists():
                            # If duplicate filename, compare size or rename
                            try:
                                if f.stat().st_size == dest_file.stat().st_size:
                                    if not dry_run:
                                        try:
                                            f.unlink(missing_ok=True)
                                        except Exception:
                                            pass
                                    continue
                                else:
                                    dest_file = canonical / f"alt_{f.name}"
                            except Exception:
                                pass

                        if not dry_run:
                            try:
                                canonical.mkdir(parents=True, exist_ok=True)
                                shutil.move(str(f), str(dest_file))
                                moved_files_total += 1
                            except Exception as me:
                                logger.warning("No se pudo mover archivo '%s' a '%s': %s", f.name, canonical, me)
                        else:
                            moved_files_total += 1

                    # Clean up source dir after moving all files
                    if not dry_run:
                        try:
                            remaining = [x for x in src.iterdir() if x.name.lower() != "cover.jpg"]
                            if not remaining:
                                shutil.rmtree(src, ignore_errors=True)
                        except Exception:
                            pass

                if moved_files_total > 0:
                    consolidated_targets.add(canonical)
                    results.append({
                        "album": canonical.name,
                        "target_folder": str(canonical),
                        "moved_files": moved_files_total,
                        "source_folders": [str(s) for s in sources],
                    })
                    logger.info(
                        "Consolidados %d archivo(s) en '%s' desde %d carpeta(s) dispersas.",
                        moved_files_total, canonical.name, len(sources)
                    )

        # 3. Trigger Navidrome scan if any consolidation occurred
        if results and self.navidrome and not dry_run:
            try:
                self.navidrome.start_scan(full_scan=False)
                logger.info("Disparado escaneo en Navidrome tras consolidación de álbumes.")
            except Exception as e:
                logger.warning("No se pudo iniciar escaneo en Navidrome: %s", e)

        return results
