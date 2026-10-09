#!/usr/bin/env python3
"""
Audio metadata tagging, MusicBrainz Picard preparation, and 1:1 cover art processing.
"""

import io
import json
import logging
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import requests

logger = logging.getLogger("listenbrainz_notifier")

KNOWN_MULTIWORD_ARTISTS = {
    "fear, and loathing in las vegas",
    "earth, wind & fire", "earth, wind and fire",
    "tyler, the creator",
    "crosby, stills, nash & young", "crosby, stills, nash and young",
    "simon & garfunkel", "simon and garfunkel",
    "kool & the gang", "kool and the gang",
    "florence + the machine", "florence and the machine",
    "bob marley & the wailers", "bob marley and the wailers",
    "iron & wine",
    "blood, sweat & tears", "blood, sweat and tears",
    "echo & the bunnymen",
    "tom petty and the heartbreakers",
    "joan jett & the blackhearts",
    "huey lewis & the news", "huey lewis and the news",
    "kc & the sunshine band", "kc and the sunshine band",
    "sly & the family stone", "sly and the family stone",
    "katrina and the waves",
    "king gizzard & the lizard wizard",
    "love harmony's, inc.", "love harmony's， inc.",
    "daryl hall & john oates", "daryl hall and john oates", "hall & oates",
    "godley & creme", "godley and creme",
    "louis armstrong & his all-stars", "louis armstrong and his all-stars",
    "louis armstrong & his orchestra", "louis armstrong and his orchestra",
    "willie colón & legal alien", "willie colon & legal alien",
    "ac/dc",
}


def fetch_official_cover(
    album_name: Optional[str] = None,
    artist_name: Optional[str] = None,
    cover_url: Optional[str] = None,
) -> Optional[bytes]:
    """
    Retrieves official high-resolution 1:1 square artwork:
    1. From cover_url (upgraded to 1200x1200).
    2. From iTunes Search API (1200x1200bb.jpg or 1400x1400bb.jpg).
    3. From MusicBrainz / Cover Art Archive.
    Returns JPEG bytes strictly verified to be square (1:1), or None.
    """
    from PIL import Image

    def _verify_and_square_image(img_bytes: bytes) -> Optional[bytes]:
        try:
            with Image.open(io.BytesIO(img_bytes)) as img:
                w, h = img.size
                if w < 300 or h < 300:
                    return None
                if w != h:
                    if w > h:
                        offset = (w - h) // 2
                        img = img.crop((offset, 0, offset + h, h))
                    else:
                        offset = (h - w) // 2
                        img = img.crop((0, offset, w, offset + w))
                out = io.BytesIO()
                img.convert("RGB").save(out, format="JPEG", quality=95, optimize=True)
                return out.getvalue()
        except Exception as e:
            logger.debug("Error procesando imagen con PIL: %s", e)
            return None

    # 1. Intentar cover_url si se dispone de él
    if cover_url and cover_url.startswith("http"):
        high_res_url = re.sub(r"=w\d+-h\d+.*", "=w1200-h1200-l90-rj", cover_url)
        high_res_url = re.sub(r"=s\d+.*", "=s1200-c-k-c0x00ffffff-no-rj", high_res_url)
        try:
            resp = requests.get(
                high_res_url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                timeout=5,
            )
            if resp.status_code == 200 and len(resp.content) > 1000:
                sq_bytes = _verify_and_square_image(resp.content)
                if sq_bytes:
                    logger.info("Carátula oficial 1:1 obtenida desde cover_url")
                    return sq_bytes
        except Exception as e:
            logger.debug("Fallo al descargar cover_url %s: %s", cover_url[:80], e)

    # 2. iTunes Search API (oficial alta definición 1200x1200 de sellos discográficos)
    clean_album = re.sub(r"\s*[\(\[].*?[\)\]]", "", album_name or "").strip()
    clean_artist = re.sub(r"\s*[\(\[].*?[\)\]]", "", artist_name or "").strip()
    if clean_album and clean_artist:
        norm_target_artist = clean_artist.lower()
        query = f"{clean_artist} {clean_album}".strip()
        for entity in ("album", "song"):
            try:
                q_enc = urllib.parse.quote(query)
                url = f"https://itunes.apple.com/search?term={q_enc}&entity={entity}&limit=5"
                resp = requests.get(
                    url,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                    timeout=5,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for item in data.get("results", []):
                        cand_artist = item.get("artistName", "").lower()
                        if norm_target_artist not in cand_artist and cand_artist not in norm_target_artist:
                            artist_words = [w for w in re.split(r"\W+", norm_target_artist) if len(w) >= 4]
                            if not any(w in cand_artist for w in artist_words):
                                continue
                        art_url = item.get("artworkUrl100", "")
                        if art_url:
                            high_art_url = art_url.replace("100x100bb.jpg", "1200x1200bb.jpg")
                            art_resp = requests.get(
                                high_art_url,
                                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                                timeout=5,
                            )
                            if art_resp.status_code == 200 and len(art_resp.content) > 1000:
                                sq_bytes = _verify_and_square_image(art_resp.content)
                                if sq_bytes:
                                    logger.info("Carátula oficial HD (1200x1200) encontrada en iTunes para '%s - %s'", clean_artist, clean_album)
                                    return sq_bytes
            except Exception as e:
                logger.debug("Error buscando carátula en iTunes (%s): %s", entity, e)

    # 3. MusicBrainz / Cover Art Archive
    if clean_album and clean_artist:
        try:
            mb_q = f"release:{clean_album} AND artist:{clean_artist}"
            mb_url = f"https://musicbrainz.org/ws/2/release/?query={urllib.parse.quote(mb_q)}&fmt=json&limit=1"
            mb_resp = requests.get(
                mb_url,
                headers={"User-Agent": "NavidromeNotifier/2.0 ( contact@navidrome.org )"},
                timeout=5,
            )
            if mb_resp.status_code == 200:
                mb_data = mb_resp.json()
                releases = mb_data.get("releases", [])
                if releases:
                    mb_artist = releases[0].get("artist-credit", [])
                    mb_artist_str = " ".join([a.get("name", "") if isinstance(a, dict) else str(a) for a in mb_artist]).lower()
                    if clean_artist.lower() in mb_artist_str or mb_artist_str in clean_artist.lower():
                        mbid = releases[0].get("id")
                        caa_url = f"https://coverartarchive.org/release/{mbid}/front-1200"
                        caa_resp = requests.get(
                            caa_url,
                            headers={"User-Agent": "NavidromeNotifier/2.0"},
                            timeout=5,
                            allow_redirects=True,
                        )
                        if caa_resp.status_code == 200 and len(caa_resp.content) > 1000:
                            sq_bytes = _verify_and_square_image(caa_resp.content)
                            if sq_bytes:
                                logger.info("Carátula oficial encontrada en Cover Art Archive para MBID %s", mbid)
                                return sq_bytes
        except Exception as e:
            logger.debug("Error buscando en Cover Art Archive: %s", e)

    return None


def process_album_cover(
    target_folder: Path,
    uid: int = 1000,
    gid: int = 1000,
    cover_url: Optional[str] = None,
    target_file: Optional[Path] = None,
    album_name: Optional[str] = None,
    album_artist: Optional[str] = None,
):
    """
    Garantiza que cada carpeta tenga su archivo cover.jpg de proporción 1:1 estrictamente cuadrada
    y que la carátula quede incrustada en el átomo nativo 'covr' de cada archivo .m4a.
    """
    if not target_folder.exists():
        return

    cover_path = target_folder / "cover.jpg"
    cover_bytes: Optional[bytes] = None

    # 1. Intentar descargar carátula oficial en alta definición cuadrada (1200x1200)
    official_bytes = fetch_official_cover(
        album_name=album_name,
        artist_name=album_artist,
        cover_url=cover_url,
    )
    if official_bytes:
        try:
            cover_path.write_bytes(official_bytes)
            cover_bytes = official_bytes
            logger.info("Carátula oficial 1:1 guardada en %s", target_folder.name)
        except Exception as e:
            logger.warning("No se pudo escribir cover.jpg oficial en %s: %s", target_folder.name, e)

    # 2. Última opción: Si no se pudo obtener carátula oficial, recortar miniatura de YouTube a 1:1
    if not cover_bytes and (not cover_path.is_file() or cover_path.stat().st_size < 1000):
        loose_images = [
            f for f in target_folder.iterdir()
            if f.is_file() and f.suffix.lower() in ('.jpg', '.jpeg', '.png', '.webp') and f.name.lower() != 'cover.jpg'
        ]
        if loose_images:
            best_img = max(loose_images, key=lambda f: f.stat().st_size)
            try:
                from PIL import Image
                with Image.open(best_img) as img:
                    w, h = img.size
                    if w != h:
                        if w > h:
                            offset = (w - h) // 2
                            img = img.crop((offset, 0, offset + h, h))
                        else:
                            offset = (h - w) // 2
                            img = img.crop((0, offset, w, offset + w))
                    img.convert("RGB").save(cover_path, "JPEG", quality=95, optimize=True)
                    cover_bytes = cover_path.read_bytes()
                    logger.info("Recorte 1:1 de miniatura de YouTube guardado como cover.jpg en %s", target_folder.name)
            except Exception as e:
                logger.debug("Error recortando miniatura suelta con PIL: %s", e)
        else:
            audio_candidates = sorted([f for f in target_folder.iterdir() if f.is_file() and f.suffix.lower() == '.m4a'])
            if audio_candidates:
                try:
                    from mutagen.mp4 import MP4
                    from PIL import Image
                    mp4 = MP4(audio_candidates[0])
                    covr = mp4.get("covr", [])
                    if covr:
                        raw_covr = bytes(covr[0])
                        with Image.open(io.BytesIO(raw_covr)) as img:
                            w, h = img.size
                            if w != h:
                                if w > h:
                                    offset = (w - h) // 2
                                    img = img.crop((offset, 0, offset + h, h))
                                else:
                                    offset = (h - w) // 2
                                    img = img.crop((0, offset, w, offset + w))
                            img.convert("RGB").save(cover_path, "JPEG", quality=95, optimize=True)
                            cover_bytes = cover_path.read_bytes()
                            logger.info("Recorte 1:1 extraído de pista .m4a guardado como cover.jpg en %s", target_folder.name)
                except Exception as e:
                    logger.debug("Error extrayendo carátula embebida con mutagen: %s", e)

    # 3. Si cover.jpg ya existía de antes, asegurar proporción 1:1 estrictamente cuadrada
    if cover_path.is_file() and cover_path.stat().st_size > 1000:
        try:
            from PIL import Image
            with Image.open(cover_path) as img:
                w, h = img.size
                if w != h:
                    logger.info("Recortando carátula existente no cuadrada a 1:1 en %s (%dx%d)", target_folder.name, w, h)
                    if w > h:
                        offset = (w - h) // 2
                        sq_img = img.crop((offset, 0, offset + h, h))
                    else:
                        offset = (h - w) // 2
                        sq_img = img.crop((0, offset, w, offset + w))
                    sq_img.convert("RGB").save(cover_path, "JPEG", quality=95, optimize=True)
            cover_bytes = cover_path.read_bytes()
        except Exception as e:
            logger.debug("Error verificando proporción de cover.jpg: %s", e)

    # 4. Eliminar miniaturas sueltas temporales (.webp, .png, etc.), preservando cover.jpg
    for img in target_folder.iterdir():
        if img.is_file() and img.name.lower() != 'cover.jpg' and img.suffix.lower() in ('.jpg', '.jpeg', '.png', '.webp'):
            try:
                img.unlink(missing_ok=True)
            except Exception:
                pass

    # 5. Si cover_bytes existe, incrustarlo en todos los archivos .m4a
    if cover_path.is_file() and cover_path.stat().st_size > 1000:
        try:
            os.chmod(cover_path, 0o664)
            os.chown(cover_path, uid, gid)
        except Exception:
            pass

        try:
            if not cover_bytes:
                cover_bytes = cover_path.read_bytes()
            from mutagen.mp4 import MP4, MP4Cover

            targets = [target_file] if target_file else (
                [] if target_folder.name == "_Singles" else list(target_folder.glob("*.m4a"))
            )
            for m4a_file in targets:
                if m4a_file and m4a_file.is_file():
                    try:
                        mp4 = MP4(m4a_file)
                        mp4["covr"] = [MP4Cover(cover_bytes, imageformat=MP4Cover.FORMAT_JPEG)]
                        mp4.save()
                    except Exception as me:
                        logger.warning("No se pudo incrustar carátula en %s: %s", m4a_file.name, me)
        except Exception as e:
            logger.warning("Error procesando incrustación de carátula en %s: %s", target_folder.name, e)


def cleanup_loose_images(target_folder: Path, cover_url: Optional[str] = None):
    """Mantiene compatibilidad hacia atrás invocando process_album_cover."""
    process_album_cover(target_folder, cover_url=cover_url)


def quick_metadata_lookup(title: str, artist: str) -> Optional[Dict[str, Any]]:
    """Consulta ultra-rápida a iTunes Search API (< 500ms) para enriquecer metadatos."""
    try:
        clean_q = f"{title} {artist}".strip()
        q = urllib.parse.quote(clean_q)
        url = f"https://itunes.apple.com/search?term={q}&entity=song&limit=1"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("resultCount", 0) > 0:
                r = data["results"][0]
                return {
                    "genre": r.get("primaryGenreName"),
                    "release_date": r.get("releaseDate", "")[:10],
                    "collection_name": r.get("collectionName"),
                    "track_name": r.get("trackName"),
                    "artist_name": r.get("artistName"),
                }
    except Exception as e:
        logger.debug("Error en consulta rápida iTunes para '%s - %s': %s", artist, title, e)
    return None


def split_artists(raw: Union[str, List[str]]) -> List[str]:
    """Splits artist credits containing multiple artists into a list of individual artist names."""
    if not raw:
        return []
    if isinstance(raw, list):
        if len(raw) > 1:
            result = []
            for x in raw:
                result.extend(split_artists(x))
            return result
        s = str(raw[0])
    else:
        s = str(raw)

    s = s.strip()
    if not s or s.lower() in KNOWN_MULTIWORD_ARTISTS:
        return [s]

    pattern = r"(?:\s+(?:feat\s*\.?|ft\s*\.?|featuring|with|con)\s+|[;/•]|\s+&\s+|\s*,\s*)"
    parts = re.split(pattern, s, flags=re.IGNORECASE)
    cleaned: List[str] = []
    seen = set()
    for p in parts:
        p_clean = re.sub(r"^[\s\.,;\-]+", "", p).strip()
        if p_clean and p_clean.lower() not in seen:
            seen.add(p_clean.lower())
            cleaned.append(p_clean)

    return cleaned if len(cleaned) > 1 else [s]


def process_album_metadata(
    target_folder: Path,
    album_artist: str,
    album_name: str,
    year: Optional[str] = None,
    is_single: bool = False,
    track_titles: Optional[List[str]] = None,
    target_file: Optional[Path] = None,
    uid: int = 1000,
    gid: int = 1000,
):
    """Blinda las pistas descargadas (.m4a) con todos los metadatos requeridos por MusicBrainz Picard."""
    if not target_folder.exists():
        return

    targets = [target_file] if target_file else sorted([f for f in target_folder.glob("*.m4a") if f.is_file()])
    if not targets:
        return

    total_tracks = len(targets)
    extra_meta = quick_metadata_lookup(album_name, album_artist)
    genre = extra_meta.get("genre") if extra_meta else None
    release_date = extra_meta.get("release_date") if extra_meta else None

    for idx, m4a_file in enumerate(targets, 1):
        if not m4a_file or not m4a_file.is_file():
            continue
        try:
            from mutagen.mp4 import MP4, MP4FreeForm
            mp4 = MP4(m4a_file)

            if album_artist:
                primary_album_artist = split_artists(album_artist)[0] if album_artist else album_artist
                mp4["aART"] = [primary_album_artist]
                for redundant_key in [
                    "----:com.apple.iTunes:ALBUMARTIST",
                    "----:com.apple.iTunes:ALBUM ARTIST",
                    "----:com.apple.iTunes:album artist",
                    "----:com.apple.iTunes:albumartist",
                    "----:com.apple.iTunes:Album Artist",
                    "----:com.apple.iTunes:AlbumArtist",
                ]:
                    if redundant_key in mp4:
                        del mp4[redundant_key]

            if album_name and ("©alb" not in mp4 or not mp4["©alb"] or mp4["©alb"][0] == "Unknown"):
                mp4["©alb"] = [album_name]

            raw_track_artists = mp4.get("©ART", [])
            if not raw_track_artists:
                raw_track_artists = [album_artist] if album_artist else []

            parsed_artists = split_artists(raw_track_artists)
            if parsed_artists:
                mp4["©ART"] = parsed_artists
                try:
                    mp4["----:com.apple.iTunes:ARTISTS"] = [
                        MP4FreeForm(a.encode("utf-8")) for a in parsed_artists
                    ]
                except Exception:
                    pass

            if release_date:
                mp4["©day"] = [release_date]
            elif "©day" in mp4 and mp4["©day"]:
                raw_day = str(mp4["©day"][0])
                if len(raw_day) == 8 and raw_day.isdigit():
                    mp4["©day"] = [f"{raw_day[:4]}-{raw_day[4:6]}-{raw_day[6:]}"]
            elif year:
                mp4["©day"] = [year]
            else:
                desc_text = ""
                for tag_name in ["desc", "ldes", "©cmt"]:
                    if tag_name in mp4 and mp4[tag_name]:
                        desc_text += " " + str(mp4[tag_name][0])
                m_year = re.search(r'(?:Released on:|℗)\s*(\d{4})', desc_text)
                if m_year:
                    mp4["©day"] = [m_year.group(1)]

            if genre:
                mp4["©gen"] = [genre]
            elif "©gen" not in mp4 or not mp4["©gen"] or mp4["©gen"] == ["Music"]:
                mp4["©gen"] = ["Latin" if is_single else "Music"]

            current_trkn = mp4.get("trkn")
            track_num = idx
            if not current_trkn or current_trkn == [(0, 0)]:
                m = re.match(r"^(\d{1,2})\s*-\s*", m4a_file.name)
                if m:
                    try:
                        track_num = int(m.group(1))
                    except Exception:
                        pass
                mp4["trkn"] = [(track_num, total_tracks)]
            elif current_trkn and current_trkn[0][0] > 0:
                track_num = current_trkn[0][0]

            if "disk" not in mp4:
                mp4["disk"] = [(1, 1)]

            is_comp = bool(album_artist and album_artist.lower() in ("various artists", "varios artistas"))
            mp4["cpil"] = is_comp

            mp4["----:com.apple.iTunes:RELEASETYPE"] = [b"single" if is_single else b"album"]
            mp4["----:com.apple.iTunes:MEDIA"] = [b"Digital Media"]

            if track_titles and 1 <= track_num <= len(track_titles):
                official_title = track_titles[track_num - 1]
                if official_title:
                    mp4["©nam"] = [official_title]
                    parts = re.split(r'\s*[\-\–\—]\s*', official_title)
                    jp_part = [p.strip() for p in parts if any('\u3040' <= c <= '\u30ff' or '\u4e00' <= c <= '\u9faf' for c in p)]
                    if jp_part:
                        mp4["----:com.apple.iTunes:originaltitle"] = [MP4FreeForm(jp_part[0].encode("utf-8"))]

            mp4.save()
            logger.info("Metadatos blindados para Picard en %s (Album Artist: '%s')", m4a_file.name, album_artist)
        except Exception as e:
            logger.warning("No se pudieron enriquecer metadatos en %s: %s", m4a_file.name, e)


def sanitize_downloaded_files(target_folder: Path):
    """Renames any files with problematic characters for Windows SMB/Samba."""
    if not target_folder.exists():
        return
    for f in list(target_folder.rglob("*")):
        if f.is_file():
            clean_name = f.name.replace("\u29f8", "-").replace("⧸", "-")
            clean_name = re.sub(r'[<>:"/\\|?*]', '_', clean_name)
            if clean_name != f.name:
                new_path = f.parent / clean_name
                try:
                    f.rename(new_path)
                    logger.info("Archivo renombrado para compatibilidad Windows SMB: '%s' -> '%s'", f.name, clean_name)
                except Exception as e:
                    logger.warning("No se pudo renombrar %s a %s: %s", f.name, clean_name, e)


def verify_album_integrity(target_folder: Path) -> Tuple[bool, int, str]:
    """Verifica la integridad de un álbum descargado en disco."""
    if not target_folder.is_dir():
        return False, 0, "La carpeta no existe"

    part_files = list(target_folder.glob("*.part")) + list(target_folder.glob("*.ytdl")) + list(target_folder.glob("*.tmp"))
    if part_files:
        return False, 0, f"Descarga incompleta detectada ({len(part_files)} archivo(s) .part/.ytdl pendientes)"

    audio_files = [
        f for f in target_folder.iterdir()
        if f.is_file() and f.suffix.lower() in ('.m4a', '.mp3', '.opus', '.webm', '.flac')
    ]
    if not audio_files:
        return False, 0, "No contiene pistas de audio"

    for f in audio_files:
        size = f.stat().st_size
        if size < 200 * 1024:
            return False, len(audio_files), f"Pista corrupta o truncada: {f.name} ({size // 1024} KB)"

    return True, len(audio_files), f"Álbum íntegro con {len(audio_files)} pista(s) válidas"
