#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Auditoría Exhaustiva de Carátulas en la Biblioteca Navidrome
============================================================
Detecta carátulas que fueron erróneamente asociadas por búsquedas
no validadas en iTunes (ej. Ariel Camacho para Koke Nuñez, Juan Gabriel para Alanys Lagos).

Método de detección:
1. Para cada carpeta de álbum/single con archivos de audio y cover.jpg:
2. Extrae el artista y título real desde tags/carpeta.
3. Si la búsqueda en iTunes por título (clean_album) o (clean_artist + clean_album)
   devuelve un artista diferente, descarga la miniatura de iTunes y calcula
   el dHash perceptual contra el cover.jpg local.
4. Si la distancia de Hamming es <= 8, es un falso positivo 100% CONFIRMADO.
5. Genera un reporte detallado en JSON y Markdown.
"""

import os
import sys
import io
import re
import json
import time
import urllib.parse
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from PIL import Image

try:
    from mutagen.mp4 import MP4
except ImportError:
    MP4 = None


def compute_dhash(image: Image.Image, hash_size: int = 8) -> int:
    """Calcula el dHash (Difference Hash) perceptual de 64 bits."""
    # Convertir a escala de grises y redimensionar a (hash_size + 1, hash_size)
    resized = image.convert("L").resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
    pixels = list(resized.getdata())
    width = hash_size + 1
    diff = []
    for row in range(hash_size):
        row_start = row * width
        for col in range(hash_size):
            diff.append(pixels[row_start + col] > pixels[row_start + col + 1])
    # Convertir lista booleana a entero de 64 bits
    decimal_val = 0
    for bit in diff:
        decimal_val = (decimal_val << 1) | int(bit)
    return decimal_val


def hamming_distance(hash1: int, hash2: int) -> int:
    """Calcula la distancia de Hamming (bits diferentes) entre dos hashes."""
    x = hash1 ^ hash2
    return bin(x).count("1")


def normalize_string(s: str) -> str:
    """Normaliza cadenas eliminando puntuación y acentos para comparación."""
    s = s.lower().strip()
    s = re.sub(r"[\(\[].*?[\)\]]", "", s)
    s = re.sub(r"[^\w\s]", "", s)
    return " ".join(s.split())


def extract_metadata_from_folder(folder: Path) -> Tuple[str, str]:
    """Extrae artista y álbum a partir de la carpeta y etiquetas de audio."""
    folder_album = folder.name
    folder_artist = folder.parent.name

    # Limpiar prefijo de año [YYYY] -
    album_clean = re.sub(r"^\[\d{4}\]\s*-\s*", "", folder_album).strip()
    # Limpiar sufijo (single)
    album_clean = re.sub(r"\s+\((?:single|ep)\)$", "", album_clean, flags=re.IGNORECASE).strip()

    # Intentar leer desde tags .m4a
    audio_files = sorted([f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in (".m4a", ".mp3", ".flac")])
    if audio_files and MP4 and audio_files[0].suffix.lower() == ".m4a":
        try:
            m = MP4(str(audio_files[0]))
            tags = m.tags or {}
            if "aART" in tags and tags["aART"]:
                folder_artist = tags["aART"][0]
            elif "©ART" in tags and tags["©ART"]:
                folder_artist = tags["©ART"][0]
            if "©alb" in tags and tags["©alb"]:
                album_clean = tags["©alb"][0]
        except Exception:
            pass

    return folder_artist, album_clean


def check_folder_cover(folder: Path, session: requests.Session) -> Optional[Dict]:
    """Inspecciona si el cover.jpg local pertenece a otro artista según iTunes."""
    cover_path = folder / "cover.jpg"
    if not cover_path.exists():
        return None

    try:
        with Image.open(cover_path) as local_img:
            local_hash = compute_dhash(local_img)
            local_size = local_img.size
    except Exception:
        return None

    artist, album = extract_metadata_from_folder(folder)
    clean_artist = re.sub(r"[\(\[].*?[\)\]]", "", artist).strip()
    clean_album = re.sub(r"[\(\[].*?[\)\]]", "", album).strip()
    clean_album = re.sub(r"^(?:\[\d{4}\]\s*-\s*)", "", clean_album).strip()
    clean_album = re.sub(r"\s+\((?:single|ep)\)$", "", clean_album, flags=re.IGNORECASE).strip()

    if not clean_album:
        return None

    norm_target = normalize_string(clean_artist)
    artist_words = [w for w in re.split(r"\W+", norm_target) if len(w) >= 4]

    # Estrategias de búsqueda que usaba el código anterior:
    # 1. clean_album solo (la causa del error)
    # 2. clean_artist + clean_album
    search_terms = [clean_album]
    if clean_artist and clean_artist != clean_album:
        search_terms.append(f"{clean_artist} {clean_album}")

    for term in search_terms:
        for entity in ("album", "song"):
            url = f"https://itunes.apple.com/search?term={urllib.parse.quote(term)}&entity={entity}&limit=5"
            try:
                resp = session.get(url, timeout=6)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                results = data.get("results", [])

                for item in results:
                    cand_artist = item.get("artistName", "")
                    cand_album = item.get("collectionName", "")
                    cand_art = item.get("artworkUrl100", "")

                    if not cand_art or not cand_artist:
                        continue

                    cand_norm = normalize_string(cand_artist)

                    # Si el artista devuelto es el mismo artista local, NO es mismatch
                    is_same_artist = (norm_target in cand_norm) or (cand_norm in norm_target)
                    if not is_same_artist and artist_words:
                        is_same_artist = any(w in cand_norm for w in artist_words)

                    if is_same_artist:
                        continue

                    # El artista es DIFERENTE. ¿Coincide la carátula local con la de iTunes?
                    try:
                        art_resp = session.get(cand_art, timeout=6)
                        if art_resp.status_code == 200:
                            cand_im = Image.open(io.BytesIO(art_resp.content))
                            cand_hash = compute_dhash(cand_im)
                            dist = hamming_distance(local_hash, cand_hash)

                            if dist <= 8:  # Coincidencia casi idéntica
                                mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(cover_path.stat().st_mtime))
                                return {
                                    "folder": str(folder),
                                    "artist": artist,
                                    "album": album,
                                    "stolen_from_artist": cand_artist,
                                    "stolen_from_album": cand_album,
                                    "hamming_distance": dist,
                                    "itunes_art_url": cand_art,
                                    "cover_modified": mtime,
                                    "local_size": list(local_size),
                                }
                    except Exception:
                        pass
            except Exception:
                pass

    return None


def main():
    library_root = Path(os.getenv("MUSIC_DIR", "/media/music")).resolve()
    print(f"=== Iniciando Auditoría de Carátulas en: {library_root} ===")

    # Encontrar carpetas con audio
    subfolders_to_scan = ["General", "J-Music", "Soundtracks", "Compilaciones", "Sin-Artista-del-Album"]
    album_folders = []

    for sub in subfolders_to_scan:
        p = library_root / sub
        if not p.exists():
            continue
        for root, dirs, files in os.walk(str(p)):
            if any(f.lower().endswith((".m4a", ".mp3", ".flac")) for f in files):
                album_folders.append(Path(root))

    print(f"Total carpetas con audio encontradas: {len(album_folders)}")

    # Filtrar aquellas con cover.jpg
    with_cover = [f for f in album_folders if (f / "cover.jpg").exists()]
    print(f"Total carpetas con cover.jpg: {len(with_cover)}")

    # Sesión HTTP compartida
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})

    affected = []
    total = len(with_cover)
    start_time = time.time()

    print(f"Iniciando escaneo multi-hilo (8 workers)...")

    def worker(f):
        # Crear sesión por hilo
        local_sess = requests.Session()
        local_sess.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        res = check_folder_cover(f, local_sess)
        time.sleep(0.05)
        return res

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(worker, f): f for f in with_cover}
        done = 0
        for fut in as_completed(futures):
            done += 1
            if done % 100 == 0 or done == total:
                elapsed = time.time() - start_time
                rate = done / elapsed if elapsed > 0 else 0
                print(f"Progreso: {done}/{total} ({done*100//total}%) | Afectados: {len(affected)} | Velocidad: {rate:.1f} carpetas/s")
            try:
                res = fut.result()
                if res:
                    affected.append(res)
                    print(f"  [MISMATCH DETECTADO] {res['artist']} - {res['album']} ➔ TIENE CARÁTULA DE: {res['stolen_from_artist']} ({res['stolen_from_album']}) [d={res['hamming_distance']}]")
            except Exception as e:
                pass

    print(f"\n=== Auditoría Finalizada ===")
    print(f"Total carpetas auditadas: {total}")
    print(f"Total carátulas afectadas detectadas: {len(affected)}")

    # Guardar resultados en JSON y Markdown
    report_json = Path("/tmp/caratulas_afectadas.json")
    with open(report_json, "w", encoding="utf-8") as f:
        json.dump(affected, f, indent=2, ensure_ascii=False)
    print(f"Reporte JSON guardado en: {report_json}")

    report_md = Path("/tmp/caratulas_afectadas.md")
    with open(report_md, "w", encoding="utf-8") as f:
        f.write(f"# Reporte de Auditoría: Carátulas Afectadas\n\n")
        f.write(f"- **Total carpetas auditadas:** {total}\n")
        f.write(f"- **Total carátulas afectadas detectadas:** {len(affected)}\n\n")
        f.write("| Artista Real | Álbum / Canción | Carátula Perteneciente A | Distancia dHash | Modificado |\n")
        f.write("|---|---|---|:---:|---|\n")
        for a in sorted(affected, key=lambda x: x["artist"].lower()):
            f.write(f"| {a['artist']} | {a['album']} | **{a['stolen_from_artist']}** ({a['stolen_from_album']}) | {a['hamming_distance']} | {a['cover_modified']} |\n")

    print(f"Reporte Markdown guardado en: {report_md}")


if __name__ == "__main__":
    main()
