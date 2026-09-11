#!/usr/bin/env python3
"""
Fix Album Covers in Navidrome Library.
Ensures every album has an official 1200x1200 square cover.jpg
and that square artwork is embedded into every .m4a file (removing
16:9 pillarbox / letterbox video thumbnail borders).
"""

import io
import json
import logging
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import List, Optional, Tuple

try:
    from PIL import Image
except ImportError:
    Image = None

try:
    from mutagen.mp4 import MP4, MP4Cover
except ImportError:
    MP4 = None
    MP4Cover = None

from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("fix_covers")


def fetch_itunes_cover(artist: str, album: str) -> Optional[Image.Image]:
    """Queries iTunes Search API for official 1200x1200 square album cover."""
    if not Image:
        return None

    # Clean artist and album for search query
    clean_artist = re.sub(r"[\(\[].*?[\)\]]", "", artist).strip()
    clean_album = re.sub(r"[\(\[].*?[\)\]]", "", album).strip()
    clean_album = re.sub(r"^(?:\[\d{4}\]\s*-\s*)", "", clean_album).strip()
    clean_album = re.sub(r"\s+\((?:single|ep)\)$", "", clean_album, flags=re.IGNORECASE).strip()

    search_queries = [
        f"{clean_artist} {clean_album}",
        f"{artist} {clean_album}",
        clean_album,
    ]

    for term in search_queries:
        if not term.strip():
            continue
        url = f"https://itunes.apple.com/search?term={urllib.parse.quote(term.strip())}&entity=album&limit=3"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            results = data.get("results", [])
            for r in results:
                art = r.get("artworkUrl100")
                if art:
                    art1200 = re.sub(r"\d+x\d+bb", "1200x1200bb", art)
                    img_req = urllib.request.Request(art1200, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(img_req, timeout=8) as img_resp:
                        raw = img_resp.read()
                        im = Image.open(io.BytesIO(raw))
                        if im.width >= 400 and im.height >= 400:
                            return im
        except Exception:
            pass

    return None


def crop_to_square(im: Image.Image) -> Image.Image:
    """Crops 16:9 or non-square image to central 1:1 square, removing side pillarbox bars."""
    w, h = im.size
    if w == h:
        return im
    if w > h:
        offset = (w - h) // 2
        return im.crop((offset, 0, offset + h, h))
    else:
        offset = (h - w) // 2
        return im.crop((0, offset, w, offset + w))


def process_folder(folder_path: Path, uid: int = 1000, gid: int = 1000) -> bool:
    """Fixes cover in folder: ensures 1200x1200 square cover.jpg and embeds into .m4a."""
    m4as = sorted([f for f in folder_path.iterdir() if f.is_file() and f.suffix.lower() == ".m4a"])
    if not m4as:
        return False

    cover_file = folder_path / "cover.jpg"
    needs_fix = False

    if not cover_file.exists():
        needs_fix = True
    else:
        try:
            with Image.open(cover_file) as img:
                if img.width != img.height or img.width < 600:
                    needs_fix = True
        except Exception:
            needs_fix = True

    # Also check if embedded cover in m4a is non-square
    if not needs_fix:
        try:
            m = MP4(str(m4as[0]))
            if "covr" in m.tags and m.tags["covr"]:
                with Image.open(io.BytesIO(m.tags["covr"][0])) as img:
                    if img.width != img.height:
                        needs_fix = True
            else:
                needs_fix = True
        except Exception:
            pass

    if not needs_fix:
        return False

    # Extract album and artist from tags or folder
    album = folder_path.name
    artist = folder_path.parent.name
    try:
        m = MP4(str(m4as[0]))
        if "©alb" in m.tags and m.tags["©alb"]:
            album = m.tags["©alb"][0]
        if "aART" in m.tags and m.tags["aART"]:
            artist = m.tags["aART"][0]
        elif "©ART" in m.tags and m.tags["©ART"]:
            artist = m.tags["©ART"][0]
    except Exception:
        pass

    logger.info("Fixing cover for '%s - %s' in %s", artist, album, folder_path.name)

    # 1. Try iTunes search for official studio 1200x1200
    chosen_img = fetch_itunes_cover(artist, album)

    # 2. Fallback to existing cover.jpg or embedded m4a image, cropped to 1:1
    if not chosen_img:
        if cover_file.exists():
            try:
                with Image.open(cover_file) as im:
                    chosen_img = crop_to_square(im.copy())
            except Exception:
                pass

    if not chosen_img:
        try:
            m = MP4(str(m4as[0]))
            if "covr" in m.tags and m.tags["covr"]:
                with Image.open(io.BytesIO(m.tags["covr"][0])) as im:
                    chosen_img = crop_to_square(im.copy())
                    logger.info("   -> Cropped 16:9 embedded cover to 1:1 square (%dx%d)", chosen_img.width, chosen_img.height)
        except Exception:
            pass

    if not chosen_img:
        logger.warning("   [SKIP] Could not find or derive cover for %s", folder_path.name)
        return False

    # Standardize to 1200x1200 square
    if chosen_img.width != 1200 or chosen_img.height != 1200:
        chosen_img = chosen_img.resize((1200, 1200), Image.Resampling.LANCZOS)

    # Save cover.jpg
    buf = io.BytesIO()
    chosen_img.convert("RGB").save(cover_file, "JPEG", quality=95, optimize=True)
    chosen_img.convert("RGB").save(buf, "JPEG", quality=95)
    cover_bytes = buf.getvalue()

    try:
        os.chown(str(cover_file), uid, gid)
        os.chmod(str(cover_file), 0o664)
    except Exception:
        pass

    # Embed 1200x1200 JPEG cover into all .m4a tracks in folder
    embedded_count = 0
    for m4a_path in m4as:
        try:
            m = MP4(str(m4a_path))
            m["covr"] = [MP4Cover(cover_bytes, imageformat=MP4Cover.FORMAT_JPEG)]
            m.save()
            embedded_count += 1
        except Exception as e:
            logger.debug("Error embedding cover in %s: %s", m4a_path.name, e)

    logger.info("   [OK] Saved 1200x1200 cover.jpg and embedded into %d tracks", embedded_count)
    return True


def main():
    library_root = Path(os.getenv("MUSIC_DIR", "/media/music")).resolve()
    logger.info("Scanning library for non-square or missing covers in: %s", library_root)

    # Target folders: search all folders containing audio in General/, J-Music/, Soundtracks/
    candidates = []
    for sub in ["General", "J-Music", "Soundtracks", "Compilaciones"]:
        sub_dir = library_root / sub
        if not sub_dir.exists():
            continue
        for root, dirs, files in os.walk(str(sub_dir)):
            if any(f.endswith(".m4a") for f in files):
                candidates.append(Path(root))

    logger.info("Found %d album folders to inspect", len(candidates))

    fixed_count = 0
    for idx, folder in enumerate(candidates, 1):
        try:
            if process_folder(folder):
                fixed_count += 1
        except Exception as e:
            logger.error("Error processing %s: %s", folder.name, e)

    logger.info("Finished: %d album covers fixed to 1200x1200 square", fixed_count)

    if fixed_count > 0:
        # Clear Navidrome image thumbnail cache so it regenerates from new 1200x1200 covers
        cache_dir = Path("/opt/docker/player-stack/config/navidrome/cache/images")
        if cache_dir.exists():
            logger.info("Clearing Navidrome image thumbnail cache at %s...", cache_dir)
            try:
                for item in cache_dir.iterdir():
                    if item.is_dir():
                        import shutil
                        shutil.rmtree(str(item), ignore_errors=True)
                    else:
                        item.unlink(missing_ok=True)
                logger.info("Navidrome image cache cleared.")
            except Exception as e:
                logger.warning("Could not clear Navidrome image cache: %s", e)

        # Trigger scan via Subsonic API
        try:
            env_file = Path(__file__).parent / ".env"
            if env_file.exists():
                load_dotenv(dotenv_path=env_file)
            else:
                load_dotenv()
            from modules.navidrome import NavidromeClient
            nav_url = os.getenv("NAVIDROME_URL", "http://localhost:4533")
            nav_user = os.getenv("NAVIDROME_USER", "admin")
            nav_pass = os.getenv("NAVIDROME_PASSWORD", "")
            navidrome = NavidromeClient(nav_url, nav_user, nav_pass)
            navidrome.start_scan(full_scan=False)
            logger.info("Triggered Navidrome library scan.")
        except Exception as e:
            logger.warning("Could not trigger Navidrome scan: %s", e)


if __name__ == "__main__":
    main()
