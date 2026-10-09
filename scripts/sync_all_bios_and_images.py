#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sincronización y Corrección Masiva de Biografías e Imágenes en Navidrome
======================================================================
1. Garantiza biografías en ESPAÑOL:
   - Prioridad 1: Wikipedia en español (alta calidad, enciclopédico, nativo).
   - Prioridad 2: Traducción MyMemory con soporte multicuenta para evitar 429.
2. Obtiene fotos oficiales en alta resolución (1000x1000) desde Deezer / Wikipedia
   para todos los artistas sin imagen.
3. Completa biografías de artistas faltantes.
"""

import os
import sys
import re
import time
import json
import sqlite3
import urllib.parse
import urllib.request
from typing import Optional, Tuple, Dict, List

DB_PATH = os.getenv("NAVIDROME_DB", "/var/lib/navidrome/navidrome.db")
LASTFM_API_KEY = "c1e430c1de22b9e12971faacc5e1d565"

EMAILS = [
    "navidrome_admin@gmail.com",
    "navidrome_sync1@gmail.com",
    "navidrome_music@gmail.com",
    "navidrome_chile@gmail.com",
]
email_idx = 0

EN_WORDS = {
    "the", "is", "was", "were", "and", "of", "in", "to", "for", "with",
    "from", "her", "his", "their", "born", "band", "released", "known",
    "american", "member", "joined", "which", "who", "an", "as", "at",
    "by", "has", "have", "had", "he", "she", "they", "its", "also",
    "song", "songs", "album", "albums", "music", "career", "after"
}

ES_WORDS = {
    "el", "la", "los", "las", "de", "en", "y", "es", "fue", "fueron",
    "del", "por", "una", "un", "para", "con", "su", "sus", "nacido",
    "nacida", "banda", "álbum", "canción", "canciones", "conocido",
    "conocida", "quien", "quienes", "al", "ha", "han", "había", "más",
    "como", "este", "esta", "desde", "también", "grupo", "carrera"
}


def is_likely_english(text: str) -> bool:
    if not text or len(text) < 40:
        return False
    words = re.findall(r"\b[a-zA-ZáéíóúÁÉÍÓÚñÑ]+\b", text.lower())
    if len(words) < 8:
        return False
    en_matches = sum(1 for w in words if w in EN_WORDS)
    es_matches = sum(1 for w in words if w in ES_WORDS)
    return en_matches >= 3 and en_matches > es_matches


def clean_bio_junk(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'<a\s+href="[^"]*"\s*>.*?</a>', "", text, flags=re.IGNORECASE)
    text = re.sub(r"<a\s+href=[^>]*>.*?</a>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"User-contributed text is available under the Creative Commons.*?$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"El texto contribuido por los usuarios está disponible bajo la Licencia Creative Commons.*?$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"Read more on Last\.fm.*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"Lee más en Last\.fm.*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def get_next_email() -> str:
    global email_idx
    mail = EMAILS[email_idx % len(EMAILS)]
    email_idx += 1
    return mail


def translate_chunk(chunk: str) -> str:
    if not chunk or len(chunk.strip()) < 3:
        return chunk
    q = urllib.parse.quote(chunk.strip())
    mail = get_next_email()
    url = f"https://api.mymemory.translated.net/get?q={q}&langpair=en|es&de={mail}"
    req = urllib.request.Request(url, headers={"User-Agent": "Navidrome-Metadata/1.4"})
    try:
        with urllib.request.urlopen(req, timeout=7) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            res_data = data.get("responseData", {})
            translated = res_data.get("translatedText", "")
            if translated and "MYMEMORY WARNING" not in translated.upper():
                return translated
    except Exception:
        pass
    return chunk


def translate_text(text: str) -> str:
    clean = clean_bio_junk(text)
    if not clean:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", clean)
    chunks = []
    curr = ""
    for s in sentences:
        if len(curr) + len(s) + 1 > 400:
            if curr:
                chunks.append(curr.strip())
            curr = s
        else:
            curr = f"{curr} {s}" if curr else s
    if curr:
        chunks.append(curr.strip())

    translated_chunks = []
    for c in chunks:
        trans = translate_chunk(c)
        translated_chunks.append(trans)
        time.sleep(0.08)

    return " ".join(translated_chunks).strip()


def fetch_wikipedia_summary(artist_name: str, lang: str = "es") -> Optional[Tuple[str, Optional[str]]]:
    clean_name = re.sub(r"\s*[\(\[].*?[\)\]]", "", artist_name).strip()
    clean_name = clean_name.replace(" ", "_")
    url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(clean_name)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Navidrome-Metadata/1.4 (music-server; mailto:admin@local)"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            extract = data.get("extract", "").strip()
            thumb = data.get("thumbnail", {}).get("source")
            if extract and len(extract) >= 40:
                return extract, thumb
    except Exception:
        pass
    return None


def fetch_deezer_images(artist_name: str) -> Optional[Tuple[str, str, str]]:
    clean_name = re.sub(r"\s*[\(\[].*?[\)\]]", "", artist_name).strip()
    if not clean_name:
        return None
    url = f"https://api.deezer.com/search/artist?q={urllib.parse.quote(clean_name)}&limit=3"
    req = urllib.request.Request(url, headers={"User-Agent": "Navidrome/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            results = data.get("data", [])
            norm_target = re.sub(r"[^\w]", "", clean_name.lower())
            for r in results:
                cand_name = re.sub(r"[^\w]", "", r.get("name", "").lower())
                if norm_target == cand_name or (norm_target in cand_name) or (cand_name in norm_target):
                    med = r.get("picture_medium", "")
                    big = r.get("picture_big", "")
                    xl = r.get("picture_xl", "")
                    if xl:
                        return med, big, xl
    except Exception:
        pass
    return None


def fetch_lastfm_bio(artist_name: str) -> Optional[str]:
    clean_name = re.sub(r"\s*[\(\[].*?[\)\]]", "", artist_name).strip()
    url = f"http://ws.audioscrobbler.com/2.0/?method=artist.getinfo&artist={urllib.parse.quote(clean_name)}&api_key={LASTFM_API_KEY}&format=json&lang=es"
    req = urllib.request.Request(url, headers={"User-Agent": "Navidrome/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            bio = data.get("artist", {}).get("bio", {})
            content = bio.get("content", "").strip()
            summary = bio.get("summary", "").strip()
            res = content or summary
            if res and len(res) >= 40:
                return clean_bio_junk(res)
    except Exception:
        pass
    return None


def run():
    print(f"=== Sincronización de Biografías e Imágenes de Artistas ===")
    print(f"Base de datos: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT id, name, biography, small_image_url, medium_image_url, large_image_url FROM artist ORDER BY name;")
    all_artists = cur.fetchall()
    print(f"Total artistas en base de datos: {len(all_artists)}")

    english_artists = []
    missing_image_artists = []
    missing_bio_artists = []

    for a in all_artists:
        bio = a["biography"]
        large_img = a["large_image_url"]
        if is_likely_english(bio):
            english_artists.append(a)
        elif not bio or len(bio.strip()) < 30:
            missing_bio_artists.append(a)

        if not large_img or not large_img.strip():
            missing_image_artists.append(a)

    print(f"Artistas con biografía en inglés: {len(english_artists)}")
    print(f"Artistas sin imagen de artista: {len(missing_image_artists)}")
    print(f"Artistas sin biografía: {len(missing_bio_artists)}")

    # ==========================================
    # FASE 1: Corregir Biografías en Inglés a Español
    # ==========================================
    print("\n--- FASE 1: Corrigiendo Biografías en Inglés a Español ---")
    translated_count = 0
    wiki_es_count = 0
    mymemory_count = 0

    for idx, a in enumerate(english_artists, 1):
        artist_id = a["id"]
        artist_name = a["name"]
        old_bio = a["biography"]
        new_bio = None

        # 1. Intentar Wikipedia ES nativo primero (100% español y máxima calidad)
        wiki_res = fetch_wikipedia_summary(artist_name, "es")
        if wiki_res and wiki_res[0] and len(wiki_res[0]) >= 50:
            new_bio = wiki_res[0]
            wiki_es_count += 1
        
        # 2. Si no existe en Wikipedia ES, traducir con MyMemory
        if not new_bio:
            trans = translate_text(old_bio)
            if trans and not is_likely_english(trans) and len(trans) >= 30:
                new_bio = trans
                mymemory_count += 1

        if new_bio:
            cur.execute(
                "UPDATE artist SET biography = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
                (new_bio, artist_id)
            )
            translated_count += 1
            if translated_count % 10 == 0:
                conn.commit()
                print(f"[{idx}/{len(english_artists)}] OK: {artist_name} ({len(new_bio)} chars)")
        else:
            print(f"[{idx}/{len(english_artists)}] [OMITIDO] {artist_name}")

        time.sleep(0.05)

    conn.commit()
    print(f"Fase 1 finalizada: {translated_count}/{len(english_artists)} corregidas a español (Wikipedia ES: {wiki_es_count}, Traducción: {mymemory_count}).")

    # ==========================================
    # FASE 2: Asignar Imágenes Oficiales (Deezer / Wikipedia)
    # ==========================================
    print("\n--- FASE 2: Asignando Imágenes Oficiales en Alta Resolución ---")
    images_found = 0
    for idx, a in enumerate(missing_image_artists, 1):
        artist_id = a["id"]
        artist_name = a["name"]
        found = False
        try:
            # 1. Deezer 1000x1000
            img_tuple = fetch_deezer_images(artist_name)
            if img_tuple:
                med, big, xl = img_tuple
                cur.execute(
                    "UPDATE artist SET small_image_url = ?, medium_image_url = ?, large_image_url = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
                    (med, big, xl, artist_id)
                )
                images_found += 1
                found = True
                if images_found % 20 == 0:
                    conn.commit()
                    print(f"[{idx}/{len(missing_image_artists)}] Foto HD asignada: {artist_name}")

            # 2. Fallback Wikipedia thumbnail
            if not found:
                wiki_res = fetch_wikipedia_summary(artist_name, "es")
                if wiki_res and wiki_res[1]:
                    thumb = wiki_res[1]
                    cur.execute(
                        "UPDATE artist SET small_image_url = ?, medium_image_url = ?, large_image_url = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
                        (thumb, thumb, thumb, artist_id)
                    )
                    images_found += 1
                    found = True
        except Exception as e:
            pass
        time.sleep(0.04)

    conn.commit()
    print(f"Fase 2 finalizada: {images_found}/{len(missing_image_artists)} imágenes de artistas asignadas.")

    # ==========================================
    # FASE 3: Enriquecer biografías faltantes
    # ==========================================
    print("\n--- FASE 3: Enriqueciendo Biografías Faltantes ---")
    bios_enriched = 0
    for idx, a in enumerate(missing_bio_artists, 1):
        artist_id = a["id"]
        artist_name = a["name"]
        found_bio = None
        try:
            # 1. Wikipedia ES
            wiki_res = fetch_wikipedia_summary(artist_name, "es")
            if wiki_res and wiki_res[0]:
                found_bio = wiki_res[0]

            # 2. Last.fm
            if not found_bio:
                last_bio = fetch_lastfm_bio(artist_name)
                if last_bio:
                    if is_likely_english(last_bio):
                        found_bio = translate_text(last_bio)
                    else:
                        found_bio = last_bio

            if found_bio and len(found_bio) >= 35:
                cur.execute(
                    "UPDATE artist SET biography = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
                    (found_bio, artist_id)
                )
                bios_enriched += 1
                if bios_enriched % 20 == 0:
                    conn.commit()
                    print(f"[{idx}/{len(missing_bio_artists)}] Biografía agregada: {artist_name}")
        except Exception as e:
            pass
        time.sleep(0.04)

    conn.commit()
    conn.close()
    print(f"Fase 3 finalizada: {bios_enriched}/{len(missing_bio_artists)} biografías agregadas.")
    print("\n=== Proceso Completo Finalizado con Éxito ===")


if __name__ == "__main__":
    run()
