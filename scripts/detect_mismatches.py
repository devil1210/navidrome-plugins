import socket
import urllib3.util.connection as urllib3_cn
urllib3_cn.allowed_gai_family = lambda: socket.AF_INET

import os
import sys
import io
import re
import json
import urllib.parse
from pathlib import Path
from typing import Optional, Tuple, Dict
import requests
from PIL import Image

def compute_dhash(image: Image.Image, hash_size: int = 8) -> int:
    resized = image.convert("L").resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
    pixels = list(resized.getdata())
    width = hash_size + 1
    diff = []
    for row in range(hash_size):
        row_start = row * width
        for col in range(hash_size):
            diff.append(pixels[row_start + col] > pixels[row_start + col + 1])
    val = 0
    for b in diff:
        val = (val << 1) | int(b)
    return val

def hamming(h1: int, h2: int) -> int:
    return bin(h1 ^ h2).count("1")

def normalize(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[\(\[].*?[\)\]]", "", s)
    s = re.sub(r"[^\w\s]", "", s)
    return " ".join(s.split())

session = requests.Session()
session.headers.update({"User-Agent": "iTunes/12.11.3 (Windows; Microsoft Windows 10 x64) AppleWebKit/537.36"})

def audit_folder(folder: Path) -> Dict:
    cover_file = folder / "cover.jpg"
    if not cover_file.exists():
        return {"status": "NO_COVER", "folder": str(folder)}

    try:
        with Image.open(cover_file) as im:
            local_hash = compute_dhash(im)
            local_size = im.size
    except Exception as e:
        return {"status": "BAD_COVER", "folder": str(folder), "err": str(e)}

    # Artist and album
    album = folder.name
    artist = folder.parent.name
    album_clean = re.sub(r"^\[\d{4}\]\s*-\s*", "", album).strip()
    album_clean = re.sub(r"\s+\((?:single|ep)\)$", "", album_clean, flags=re.IGNORECASE).strip()
    artist_clean = re.sub(r"[\(\[].*?[\)\]]", "", artist).strip()

    norm_artist = normalize(artist_clean)
    artist_words = [w for w in re.split(r"\W+", norm_artist) if len(w) >= 4]

    # 1. Search iTunes for official cover with ARTIST + ALBUM
    official_cand = None
    q = urllib.parse.quote(f"{artist_clean} {album_clean}".strip())
    url = f"https://itunes.apple.com/search?term={q}&entity=album&limit=5"
    try:
        r = session.get(url, timeout=5)
        if r.status_code == 200:
            for item in r.json().get("results", []):
                cand_name = normalize(item.get("artistName", ""))
                if norm_artist in cand_name or cand_name in norm_artist or any(w in cand_name for w in artist_words):
                    official_cand = item
                    break
    except Exception:
        pass

    # 2. Check if local cover matches the official artist cover
    if official_cand and official_cand.get("artworkUrl100"):
        try:
            art_r = session.get(official_cand["artworkUrl100"], timeout=5)
            if art_r.status_code == 200:
                off_im = Image.open(io.BytesIO(art_r.content))
                off_hash = compute_dhash(off_im)
                dist_off = hamming(local_hash, off_hash)
                if dist_off <= 8:
                    return {
                        "status": "CORRECT",
                        "folder": str(folder),
                        "artist": artist_clean,
                        "album": album_clean,
                        "official_artist": official_cand.get("artistName"),
                        "official_album": official_cand.get("collectionName"),
                        "dhash_dist": dist_off
                    }
                else:
                    # DOES NOT MATCH OFFICIAL! Let's see who it matches!
                    pass
        except Exception:
            pass

    # 3. Search iTunes for bare album title to see if it stole someone else's cover
    q_bare = urllib.parse.quote(album_clean)
    for entity in ("album", "song"):
        url_bare = f"https://itunes.apple.com/search?term={q_bare}&entity={entity}&limit=10"
        try:
            r = session.get(url_bare, timeout=5)
            if r.status_code == 200:
                for item in r.json().get("results", []):
                    c_artist = item.get("artistName", "")
                    c_norm = normalize(c_artist)
                    is_same = norm_artist in c_norm or c_norm in norm_artist or any(w in c_norm for w in artist_words)
                    if is_same:
                        continue
                    c_art = item.get("artworkUrl100")
                    if c_art:
                        art_r = session.get(c_art, timeout=5)
                        if art_r.status_code == 200:
                            cand_im = Image.open(io.BytesIO(art_r.content))
                            c_hash = compute_dhash(cand_im)
                            dist_other = hamming(local_hash, c_hash)
                            if dist_other <= 8:
                                return {
                                    "status": "STOLEN_COVER",
                                    "folder": str(folder),
                                    "artist": artist_clean,
                                    "album": album_clean,
                                    "stolen_from_artist": c_artist,
                                    "stolen_from_album": item.get("collectionName"),
                                    "dhash_dist": dist_other,
                                    "stolen_art_url": c_art
                                }
        except Exception:
            pass

    return {
        "status": "OK_OR_YOUTUBE",
        "folder": str(folder),
        "artist": artist_clean,
        "album": album_clean,
        "note": "Not stolen from iTunes candidate"
    }

if __name__ == "__main__":
    test_folders = [
        Path("/media/music/General/Red Hot Chili Peppers/[2022] - Unlimited Love"),
        Path("/media/music/General/Koke Nuñez/[2026] - El Karma"),
        Path("/media/music/General/Koke Nuñez/[2024] - Usted Me Gusta"),
        Path("/media/music/General/Jordan/[2026] - Y Que Pasó"),
        Path("/media/music/_duplicados_backup/General/Alanys Lagos_backup_/[2025] - Que Lindos Ojos"),
        Path("/media/music/_duplicados_backup/General/Alanys Lagos_backup_/[2026] - Llegaste Tú"),
        Path("/media/music/_duplicados_backup/General/Alanys Lagos_backup_/[2026] - Tu Eres El Único"),
        Path("/media/music/_duplicados_backup/General/Alanys Lagos_backup_/[2026] - Se Me Fue El Amor"),
        Path("/media/music/General/Alanys Lagos/[2025] - Que Lindos Ojos (single)"),
    ]
    for tf in test_folders:
        if tf.exists():
            res = audit_folder(tf)
            print(f"[{res['status']}] {res.get('artist')} - {res.get('album')}: {res}")
        else:
            print(f"[NOT_EXISTS] {tf}")
