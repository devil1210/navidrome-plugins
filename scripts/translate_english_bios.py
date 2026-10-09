#!/usr/bin/env python3
"""
Script de saneamiento masivo para traducir todas las biografías en inglés
al español neutro en la base de datos de Navidrome.
"""

import sqlite3
import urllib.request
import urllib.parse
import json
import time
import re
import sys

DB_PATH = "/var/lib/navidrome/navidrome.db"

EN_WORDS = {
    "the", "is", "and", "of", "to", "with", "in", "was", "her", "his", 
    "that", "for", "at", "who", "has", "she", "he", "their", "from", "they", 
    "first", "band", "group", "released", "album", "music", "song", "career",
    "born", "formed", "known"
}
ES_WORDS = {
    "el", "la", "los", "las", "de", "en", "y", "que", "con", "por", "para", 
    "es", "su", "una", "un", "como", "del", "al", "sus", "canciones", "banda", 
    "grupo", "álbum", "musica", "música", "carrera", "nació", "formó"
}


def is_likely_english(text: str) -> bool:
    if not text:
        return False
    words = re.findall(r'[a-zA-ZáéíóúÁÉÍÓÚñÑ]+', text.lower())
    if len(words) < 6:
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
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


CLIENTS = ["dict-chrome-ex", "webapp", "t"]

def translate_google(text: str) -> str:
    clean = clean_bio_junk(text)
    if not clean or len(clean.strip()) < 3:
        return clean

    # Si es menor a 4000 caracteres, traducir de una sola vez
    if len(clean) <= 4000:
        for client in CLIENTS:
            try:
                q = urllib.parse.quote(clean.strip())
                url = f"https://translate.googleapis.com/translate_a/single?client={client}&sl=auto&tl=es&dt=t&q={q}"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"})
                with urllib.request.urlopen(req, timeout=12) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    if isinstance(data, list) and len(data) > 0 and isinstance(data[0], list):
                        parts = [part[0] for part in data[0] if part and len(part) > 0 and part[0]]
                        res = "".join(parts).strip()
                        if res and not is_likely_english(res):
                            return res
            except Exception:
                continue

    # Si excede o falló, dividir por oraciones
    sentences = re.split(r"(?<=[.!?])\s+", clean)
    chunks = []
    curr = ""
    for s in sentences:
        if len(curr) + len(s) + 1 > 500:
            if curr:
                chunks.append(curr.strip())
            curr = s
        else:
            curr = f"{curr} {s}" if curr else s
    if curr:
        chunks.append(curr.strip())

    translated_chunks = []
    for c in chunks:
        trans_ok = False
        for client in CLIENTS:
            try:
                q = urllib.parse.quote(c.strip())
                url = f"https://translate.googleapis.com/translate_a/single?client={client}&sl=auto&tl=es&dt=t&q={q}"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    if isinstance(data, list) and len(data) > 0 and isinstance(data[0], list):
                        parts = [part[0] for part in data[0] if part and len(part) > 0 and part[0]]
                        res = "".join(parts).strip()
                        if res and not is_likely_english(res):
                            translated_chunks.append(res)
                            trans_ok = True
                            break
            except Exception:
                continue
        if not trans_ok:
            translated_chunks.append(c)
        time.sleep(0.08)

    return " ".join(translated_chunks).strip()


def main():
    print(f"Abriendo base de datos en {DB_PATH}...")
    conn = sqlite3.connect(DB_PATH, timeout=20.0)
    cur = conn.cursor()

    rows = cur.execute("SELECT id, name, biography FROM artist WHERE LENGTH(biography) > 20").fetchall()
    english_artists = []
    for aid, name, bio in rows:
        if is_likely_english(bio):
            english_artists.append((aid, name, bio))

    total = len(english_artists)
    print(f"Detectados {total} artistas con biografía en inglés para traducir a español.")

    success_count = 0
    start_time = time.time()

    for idx, (aid, name, bio) in enumerate(english_artists, 1):
        try:
            trans = translate_google(bio)
            if trans and not is_likely_english(trans):
                cur.execute("UPDATE artist SET biography = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (trans, aid))
                success_count += 1
                if idx % 10 == 0 or idx == total:
                    conn.commit()
                    elapsed = time.time() - start_time
                    rate = idx / elapsed if elapsed > 0 else 0
                    print(f"[{idx}/{total}] ({rate:.1f} art/s) Traducido: '{name}' ({len(trans)} chars)")
            else:
                print(f"[{idx}/{total}] Omitido (no cambió): '{name}'")
        except Exception as e:
            print(f"[{idx}/{total}] Error en '{name}': {e}")

        time.sleep(0.03)

    conn.commit()
    conn.close()
    print(f"\nProceso finalizado con éxito: {success_count}/{total} biografías traducidas al español.")


if __name__ == "__main__":
    main()
