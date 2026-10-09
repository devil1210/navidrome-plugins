import socket
import urllib3.util.connection as urllib3_cn
urllib3_cn.allowed_gai_family = lambda: socket.AF_INET

import os
import sys
import io
import re
import json
import time
import urllib.parse
from pathlib import Path
from typing import Optional, Tuple, Dict, List
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from PIL import Image

try:
    from mutagen.mp4 import MP4
except ImportError:
    MP4 = None


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


def extract_metadata(folder: Path) -> Tuple[str, str]:
    album = folder.name
    artist = folder.parent.name

    # Remove year prefix and single suffix
    album_clean = re.sub(r"^\[\d{4}\]\s*-\s*", "", album).strip()
    album_clean = re.sub(r"\s+\((?:single|ep)\)$", "", album_clean, flags=re.IGNORECASE).strip()

    artist_clean = re.sub(r"[\(\[].*?[\)\]]", "", artist).strip()
    artist_clean = re.sub(r"_backup_$", "", artist_clean).strip()

    # Read tags from first audio file
    for f in folder.iterdir():
        if f.is_file() and f.suffix.lower() == ".m4a" and MP4:
            try:
                m = MP4(str(f))
                tags = m.tags or {}
                if "aART" in tags and tags["aART"]:
                    artist_clean = tags["aART"][0]
                    break
                elif "©ART" in tags and tags["©ART"]:
                    artist_clean = tags["©ART"][0]
                    break
            except Exception:
                pass

    return artist_clean, album_clean


def check_album(folder: Path, session: requests.Session) -> Optional[Dict]:
    cover_file = folder / "cover.jpg"
    if not cover_file.exists():
        return None

    try:
        with Image.open(cover_file) as im:
            local_hash = compute_dhash(im)
            local_size = im.size
    except Exception:
        return None

    artist_clean, album_clean = extract_metadata(folder)
    if not album_clean or not artist_clean:
        return None

    norm_artist = normalize(artist_clean)
    artist_words = [w for w in re.split(r"\W+", norm_artist) if len(w) >= 4]

    # Search queries:
    # 1. Bare album title (the trigger for the bug)
    # 2. Artist + Album
    queries = [album_clean]
    if artist_clean.lower() != album_clean.lower():
        queries.append(f"{artist_clean} {album_clean}")

    for q_term in queries:
        for entity in ("album", "song"):
            q_enc = urllib.parse.quote(q_term.strip())
            url = f"https://itunes.apple.com/search?term={q_enc}&entity={entity}&limit=8"
            try:
                r = session.get(url, timeout=4)
                if r.status_code != 200:
                    continue
                results = r.json().get("results", [])
                for item in results:
                    c_artist = item.get("artistName", "")
                    c_album = item.get("collectionName", "")
                    c_art = item.get("artworkUrl100", "")
                    if not c_art or not c_artist:
                        continue

                    c_norm = normalize(c_artist)
                    is_same = (norm_artist in c_norm) or (c_norm in norm_artist)
                    if not is_same and artist_words:
                        is_same = any(w in c_norm for w in artist_words)

                    if is_same:
                        continue

                    # Different artist returned! Check if local cover matches candidate
                    try:
                        art_r = session.get(c_art, timeout=4)
                        if art_r.status_code == 200:
                            cand_im = Image.open(io.BytesIO(art_r.content))
                            c_hash = compute_dhash(cand_im)
                            dist = hamming(local_hash, c_hash)
                            if dist <= 8:
                                mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(cover_file.stat().st_mtime))
                                return {
                                    "folder": str(folder),
                                    "artist": artist_clean,
                                    "album": album_clean,
                                    "stolen_from_artist": c_artist,
                                    "stolen_from_album": c_album,
                                    "hamming_distance": dist,
                                    "itunes_art_url": c_art,
                                    "cover_modified": mtime,
                                    "local_size": list(local_size)
                                }
                    except Exception:
                        pass
            except Exception:
                pass

    return None


def main():
    library_root = Path(os.getenv("MUSIC_DIR", "/media/music")).resolve()
    print(f"=== Auditoría Completa de la Biblioteca: Carátulas Desalineadas ===")
    print(f"Ruta: {library_root}")

    subdirs = ["General", "J-Music", "Soundtracks", "Compilaciones", "Sin-Artista-del-Album", "_duplicados_backup"]
    folders_to_check = []

    for sub in subdirs:
        p = library_root / sub
        if not p.exists():
            continue
        for root, dirs, files in os.walk(str(p)):
            if "cover.jpg" in files and any(f.lower().endswith((".m4a", ".mp3", ".flac")) for f in files):
                folders_to_check.append(Path(root))

    total = len(folders_to_check)
    print(f"Total carpetas con cover.jpg y audio: {total}")

    affected = []
    start_time = time.time()

    def worker(folder_path):
        sess = requests.Session()
        sess.headers.update({"User-Agent": "iTunes/12.11.3 (Windows; Microsoft Windows 10 x64) AppleWebKit/537.36"})
        res = check_album(folder_path, sess)
        time.sleep(0.04)
        return res

    print("Iniciando escaneo con 10 workers...")
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(worker, f): f for f in folders_to_check}
        done = 0
        for fut in as_completed(futures):
            done += 1
            if done % 100 == 0 or done == total:
                elapsed = time.time() - start_time
                rate = done / elapsed if elapsed > 0 else 0
                print(f"Progreso: {done}/{total} ({done*100//total}%) | Afectados: {len(affected)} | {rate:.1f} carp/s")
            try:
                res = fut.result()
                if res:
                    affected.append(res)
                    print(f"  [MISMATCH] {res['artist']} — {res['album']} ➔ TIENE CARÁTULA DE: {res['stolen_from_artist']} ({res['stolen_from_album']}) [d={res['hamming_distance']}]")
            except Exception:
                pass

    print(f"\n=== Auditoría Completa Finalizada ===")
    print(f"Total carpetas examinadas: {total}")
    print(f"Total carátulas afectadas detectadas: {len(affected)}")

    # Guardar reporte
    out_json = Path("/tmp/auditoria_caratulas_completa.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(affected, f, indent=2, ensure_ascii=False)
    print(f"Reporte JSON guardado en: {out_json}")

    out_md = Path("/tmp/auditoria_caratulas_completa.md")
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("# Reporte Definitivo de Carátulas Afectadas\n\n")
        f.write(f"- **Carpetas analizadas:** {total}\n")
        f.write(f"- **Carátulas con arte ajeno detectadas:** {len(affected)}\n\n")
        f.write("| Artista de la Canción | Álbum / Single | Carátula Perteneciente A | Distancia dHash | Carpeta |\n")
        f.write("|---|---|---|:---:|---|\n")
        for a in sorted(affected, key=lambda x: x["artist"].lower()):
            f.write(f"| {a['artist']} | {a['album']} | **{a['stolen_from_artist']}** ({a['stolen_from_album']}) | {a['hamming_distance']} | `{a['folder']}` |\n")

    print(f"Reporte Markdown guardado en: {out_md}")


if __name__ == "__main__":
    main()
