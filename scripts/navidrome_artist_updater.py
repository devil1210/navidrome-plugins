#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Navidrome Artist Updater Service (Local Microservice)
=====================================================
Escucha en 127.0.0.1:4535 para recibir solicitudes de sincronización
de biografía e imágenes de artistas disparadas por el plugin WASM
nd-metadata durante la reproducción en Navidrome.

- Comprueba si el artista fue actualizado hace menos de 'interval_days' (por defecto 7).
- Si ha expirado, obtiene biografía en español (Wikipedia ES / MyMemory) y foto HD Deezer (1000x1000).
- Actualiza atómicamente navidrome.db y refresca la caché de arte de Navidrome con 'artwork refresh'.
"""

import os
import sys
import re
import time
import json
import sqlite3
import datetime
import threading
import subprocess
import urllib.parse
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional, Tuple, Dict, Any

DB_PATH = os.getenv("NAVIDROME_DB", "/var/lib/navidrome/navidrome.db")
NAVIDROME_BIN = os.getenv("NAVIDROME_BIN", "/opt/navidrome/navidrome")
CONFIG_FILE = os.getenv("NAVIDROME_CONFIG", "/var/lib/navidrome/navidrome.toml")
PORT = int(os.getenv("UPDATER_PORT", "4535"))
LASTFM_API_KEY = "c1e430c1de22b9e12971faacc5e1d565"

EMAILS = [
    "navidrome_admin@gmail.com",
    "navidrome_sync1@gmail.com",
    "navidrome_music@gmail.com",
    "navidrome_chile@gmail.com",
]
email_idx = 0
email_lock = threading.Lock()

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


def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [ArtistUpdater] {msg}", flush=True)


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
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def get_next_email() -> str:
    global email_idx
    with email_lock:
        mail = EMAILS[email_idx % len(EMAILS)]
        email_idx += 1
        return mail


def translate_google(text: str) -> Optional[str]:
    if not text or len(text.strip()) < 3:
        return text
    for client in ("dict-chrome-ex", "webapp", "t"):
        try:
            q = urllib.parse.quote(text.strip())
            url = f"https://translate.googleapis.com/translate_a/single?client={client}&sl=auto&tl=es&dt=t&q={q}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if isinstance(data, list) and len(data) > 0 and isinstance(data[0], list):
                    parts = [part[0] for part in data[0] if part and len(part) > 0 and part[0]]
                    res = "".join(parts).strip()
                    if res and not is_likely_english(res):
                        return clean_bio_junk(res)
        except Exception:
            continue
    return None


def translate_chunk_mymemory(chunk: str) -> str:
    if not chunk or len(chunk.strip()) < 3:
        return chunk
    q = urllib.parse.quote(chunk.strip())
    mail = get_next_email()
    url = f"https://api.mymemory.translated.net/get?q={q}&langpair=en|es&de={mail}"
    req = urllib.request.Request(url, headers={"User-Agent": "Navidrome-Metadata/1.5"})
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            res_data = data.get("responseData", {})
            translated = res_data.get("translatedText", "")
            if translated and "MYMEMORY WARNING" not in translated.upper():
                return clean_bio_junk(translated)
    except Exception as e:
        log(f"MyMemory translation warning: {e}")
    return chunk


def translate_text(text: str) -> str:
    clean = clean_bio_junk(text)
    if not clean:
        return ""
    
    # 1. Intentar traducción completa con Google Translate (sin límite de palabras)
    if len(clean) <= 4000:
        g_trans = translate_google(clean)
        if g_trans:
            return g_trans

    # 2. División por oraciones para textos muy largos o fallback
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
        trans = translate_google(c)
        if not trans or is_likely_english(trans):
            trans = translate_chunk_mymemory(c)
        translated_chunks.append(trans)
        time.sleep(0.05)

    res = " ".join(translated_chunks).strip()
    return res if res else clean


def fetch_wikipedia_summary(artist_name: str, lang: str = "es") -> Optional[Tuple[str, Optional[str]]]:
    clean_name = re.sub(r"\s*[\(\[].*?[\)\]]", "", artist_name).strip()
    clean_name = clean_name.replace(" ", "_")
    url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(clean_name)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Navidrome-Metadata/1.5 (music-server; mailto:admin@local)"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            extract = data.get("extract", "").strip()
            thumb = data.get("thumbnail", {}).get("source")
            if extract and len(extract) >= 40:
                return clean_bio_junk(extract), thumb
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
    except Exception as e:
        log(f"Deezer fetch error for '{artist_name}': {e}")
    return None


def fetch_lastfm_bio(artist_name: str) -> Optional[str]:
    clean_name = re.sub(r"\s*[\(\[].*?[\)\]]", "", artist_name).strip()
    url = f"https://ws.audioscrobbler.com/2.0/?method=artist.getinfo&artist={urllib.parse.quote(clean_name)}&api_key={LASTFM_API_KEY}&format=json&lang=es"
    req = urllib.request.Request(url, headers={"User-Agent": "Navidrome/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            bio = data.get("artist", {}).get("bio", {})
            content = bio.get("content", "").strip()
            summary = bio.get("summary", "").strip()
            res = content or summary
            if res and len(res) >= 40:
                cleaned = clean_bio_junk(res)
                if is_likely_english(cleaned):
                    return translate_text(cleaned)
                return cleaned
    except Exception as e:
        log(f"Last.fm fetch error for '{artist_name}': {e}")
    return None


def parse_db_timestamp(ts_str: Optional[str]) -> Optional[float]:
    if not ts_str or ts_str.startswith("1970") or ts_str.startswith("0000"):
        return None
    clean_ts = ts_str.split("+")[0].split("Z")[0].strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.datetime.strptime(clean_ts, fmt)
            return dt.replace(tzinfo=datetime.timezone.utc).timestamp()
        except ValueError:
            continue
    return None


def refresh_navidrome_artwork(artist_id: str):
    """Ejecuta en segundo plano el refresh de artwork para invalidar la caché de Navidrome."""
    if not os.path.exists(NAVIDROME_BIN):
        return
    def _run():
        try:
            cmd = [NAVIDROME_BIN, "artwork", "refresh", "ar", artist_id, "-c", CONFIG_FILE]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if res.returncode == 0:
                log(f"Artwork cache refreshed for artist {artist_id}")
            else:
                log(f"Artwork refresh returned {res.returncode}: {res.stderr.strip()}")
        except Exception as e:
            log(f"Artwork refresh error: {e}")
    threading.Thread(target=_run, daemon=True).start()


def sync_artist(payload: Dict[str, Any]) -> Dict[str, Any]:
    artist_id = str(payload.get("artist_id", "")).strip()
    artist_name = str(payload.get("artist_name", "")).strip()
    interval_days = int(payload.get("interval_days", 7))
    force = bool(payload.get("force", False))
    provided_bio = str(payload.get("biography", "")).strip()
    provided_image = str(payload.get("image_url", "")).strip()

    if not artist_id and not artist_name:
        return {"status": "error", "message": "artist_id or artist_name is required"}

    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        # 1. Buscar artista en base de datos
        row = None
        if artist_id:
            cur.execute("SELECT id, name, biography, small_image_url, medium_image_url, large_image_url, external_info_updated_at, updated_at FROM artist WHERE id = ?", (artist_id,))
            row = cur.fetchone()
        if not row and artist_name:
            cur.execute("SELECT id, name, biography, small_image_url, medium_image_url, large_image_url, external_info_updated_at, updated_at FROM artist WHERE name = ? COLLATE NOCASE", (artist_name,))
            row = cur.fetchone()

        if not row:
            return {"status": "error", "message": f"Artist '{artist_name or artist_id}' not found in database"}

        db_id = row["id"]
        db_name = row["name"]
        db_bio = row["biography"] or ""
        db_large_img = row["large_image_url"] or ""
        last_ext_ts = parse_db_timestamp(row["external_info_updated_at"])
        last_upd_ts = parse_db_timestamp(row["updated_at"])
        last_ts = max(filter(None, [last_ext_ts, last_upd_ts]), default=None)

        now = time.time()
        interval_seconds = max(interval_days, 1) * 86400

        # Si no se fuerza y tiene información completa (bio no en inglés y foto HD) y no ha expirado
        has_good_bio = len(db_bio.strip()) >= 40 and not is_likely_english(db_bio)
        has_good_image = len(db_large_img.strip()) > 0
        if not force and last_ts is not None and (now - last_ts < interval_seconds) and has_good_bio and has_good_image:
            days_ago = round((now - last_ts) / 86400, 1)
            log(f"Artist '{db_name}' already fresh (updated {days_ago} days ago, threshold: {interval_days}d). Skipping.")
            return {
                "status": "already_fresh",
                "artist_id": db_id,
                "artist_name": db_name,
                "days_since_update": days_ago,
                "updated_at": row["external_info_updated_at"] or row["updated_at"],
            }

        log(f"Synchronizing artist '{db_name}' (ID: {db_id}, interval: {interval_days}d, force={force})...")

        # 2. Resolver Biografía en Español
        new_bio = None
        if provided_bio and not is_likely_english(provided_bio):
            new_bio = clean_bio_junk(provided_bio)
        
        if not new_bio:
            wiki_res = fetch_wikipedia_summary(db_name, "es")
            if wiki_res and wiki_res[0] and len(wiki_res[0]) >= 40:
                new_bio = wiki_res[0]

        if not new_bio:
            lastfm_res = fetch_lastfm_bio(db_name)
            if lastfm_res and len(lastfm_res) >= 40:
                new_bio = lastfm_res

        if not new_bio and is_likely_english(db_bio):
            new_bio = translate_text(db_bio)

        # 3. Resolver Imágenes HD (Deezer / Wikipedia)
        new_med, new_big, new_xl = None, None, None
        if provided_image:
            new_med, new_big, new_xl = provided_image, provided_image, provided_image

        if not new_xl:
            deezer_imgs = fetch_deezer_images(db_name)
            if deezer_imgs:
                new_med, new_big, new_xl = deezer_imgs

        if not new_xl and not db_large_img:
            wiki_res = fetch_wikipedia_summary(db_name, "es")
            if wiki_res and wiki_res[1]:
                new_med = new_big = new_xl = wiki_res[1]

        # 4. Actualizar Base de Datos Atómicamente
        bio_to_write = new_bio if new_bio else db_bio
        med_to_write = new_med if new_med else row["medium_image_url"] or ""
        big_to_write = new_big if new_big else row["large_image_url"] or ""
        xl_to_write = new_xl if new_xl else row["large_image_url"] or ""

        cur.execute("""
            UPDATE artist SET
                biography = ?,
                small_image_url = ?,
                medium_image_url = ?,
                large_image_url = ?,
                external_info_updated_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?;
        """, (bio_to_write, med_to_write, big_to_write, xl_to_write, db_id))
        conn.commit()

        # 5. Invalidar caché de arte en Navidrome si cambió la imagen
        if new_xl and new_xl != db_large_img:
            refresh_navidrome_artwork(db_id)

        log(f"SUCCESS: Artist '{db_name}' updated. Bio len={len(bio_to_write)}, Image={xl_to_write[:60]}...")
        return {
            "status": "updated",
            "artist_id": db_id,
            "artist_name": db_name,
            "biography_len": len(bio_to_write),
            "image_url": xl_to_write,
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

    except Exception as e:
        log(f"ERROR syncing artist '{artist_name}': {e}")
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()


class RequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "service": "navidrome-artist-updater"}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path in ("/sync_artist", "/update_artist"):
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                payload = json.loads(body.decode("utf-8"))
            except Exception:
                payload = {}

            resp_data = sync_artist(payload)
            status_code = 200 if resp_data.get("status") in ("updated", "already_fresh") else 400

            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(resp_data).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def run():
    server_address = ("127.0.0.1", PORT)
    httpd = HTTPServer(server_address, RequestHandler)
    log(f"Servicio Navidrome Artist Updater iniciado en http://127.0.0.1:{PORT}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        log("Servidor detenido.")


if __name__ == "__main__":
    run()
