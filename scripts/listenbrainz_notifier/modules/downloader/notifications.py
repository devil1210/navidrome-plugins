#!/usr/bin/env python3
"""
Rich notifications dispatcher for downloaded albums in Telegram topics.
"""

import html
import json
import logging
import os
import re
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from ..models import ResolvedAlbum, TelegramTarget
from .batch_view import build_album_rich_html, format_duration

logger = logging.getLogger("listenbrainz_notifier")


def resolve_topic_thread_id(
    listener: Optional[Any],
    chat_id: str,
    topic_key: str,
    default_name: str,
    icon_color: Optional[int] = None,
    caller_thread_id: Optional[str] = None,
) -> Optional[str]:
    """Resolves or retrieves the topic thread_id for a given functional key (e.g. 'descargas', 'anadidos')."""
    env_key = f"TELEGRAM_TOPIC_{topic_key.upper()}_ID"
    env_val = os.environ.get(env_key)
    if env_val and str(env_val).strip():
        return str(env_val).strip()

    if listener and hasattr(listener, "get_or_create_topic"):
        try:
            raw_tid = listener.get_or_create_topic(
                chat_id=chat_id,
                topic_key=topic_key,
                default_name=default_name,
                icon_color=icon_color,
                caller_thread_id=caller_thread_id,
            )
            if isinstance(raw_tid, (int, str)):
                raw_str = str(raw_tid).strip()
                if raw_str and not raw_str.startswith("<MagicMock"):
                    return raw_str
        except Exception:
            pass
    return caller_thread_id


def send_album_rich_notification(
    queue: Any,
    chat_id: str,
    thread_id: Optional[str],
    album: ResolvedAlbum,
    target_folder: Path,
) -> bool:
    """
    Sends the standard Navidrome Telegram plugin rich notification with cover photo,
    metadata table (Año, Género, Discos, Canciones, Duración), expandable song list,
    and link to Navidrome once the album has finished downloading completely.
    """
    token = (
        queue.bot_token
        or (queue.listener.bot_token if queue.listener else None)
        or (queue.telegram_sender.bot_token if queue.telegram_sender else None)
    )
    if not token:
        logger.warning("No Telegram bot token available for album rich notification. Falling back to reply.")
        queue._send_reply(
            chat_id,
            f"📥 <b>Álbum descargado:</b>\n"
            f"• <b>{album.artist_name}</b> — <i>{album.album_name}</i>\n"
            f"• <b>Carpeta:</b> <code>{queue.win_dest}\\{target_folder.name}</code>\n"
            f"✨ <i>Listo con cover.jpg e indexándose en Navidrome.</i>",
            thread_id,
        )
        return False

    album_artist = album.artist_name
    album_name = album.album_name
    year = ""
    genre = ""
    songs: List[Dict[str, Any]] = []

    audio_files = []
    if target_folder.is_dir():
        audio_files = sorted(
            [
                f for f in target_folder.rglob("*")
                if f.is_file() and f.suffix.lower() in ('.m4a', '.mp3', '.opus', '.webm', '.flac')
            ],
            key=lambda f: f.name,
        )

    # 1. Read metadata and durations from audio files via mutagen
    for idx, f in enumerate(audio_files, 1):
        t_title = ""
        t_disc = 1
        t_track = idx
        t_dur = 0
        t_year = ""
        t_genre = ""

        try:
            if f.suffix.lower() == ".m4a":
                from mutagen.mp4 import MP4
                mp4 = MP4(f)
                if mp4.info and hasattr(mp4.info, "length"):
                    t_dur = int(mp4.info.length)
                if "©nam" in mp4 and mp4["©nam"]:
                    t_title = str(mp4["©nam"][0])
                if "trkn" in mp4 and mp4["trkn"]:
                    t_track = mp4["trkn"][0][0] or idx
                if "disk" in mp4 and mp4["disk"]:
                    t_disc = mp4["disk"][0][0] or 1
                if "©day" in mp4 and mp4["©day"]:
                    t_year = str(mp4["©day"][0])[:4]
                if "©gen" in mp4 and mp4["©gen"]:
                    t_genre = str(mp4["©gen"][0])
                if "aART" in mp4 and mp4["aART"]:
                    album_artist = str(mp4["aART"][0])
                if "©alb" in mp4 and mp4["©alb"]:
                    album_name = str(mp4["©alb"][0])
            else:
                import mutagen
                mf = mutagen.File(f)
                if mf:
                    if mf.info and hasattr(mf.info, "length"):
                        t_dur = int(mf.info.length)
                    if hasattr(mf, "tags") and mf.tags:
                        title_val = mf.tags.get("title")
                        if title_val:
                            t_title = str(title_val[0] if isinstance(title_val, list) else title_val)
        except Exception as me:
            logger.debug("Error leyendo tags de %s: %s", f.name, me)

        if not t_title:
            clean_name = f.stem
            clean_name = re.sub(r"^\d{1,2}\s*[\.\-]\s*", "", clean_name).strip()
            t_title = clean_name or f.stem

        if t_year and not year:
            year = t_year
        if t_genre and not genre:
            genre = t_genre

        songs.append({
            "disc": t_disc,
            "track": t_track,
            "title": t_title,
            "duration": t_dur,
        })

    # Fallback if no local audio files were found
    if not songs and album.tracks:
        for idx, trk_title in enumerate(album.tracks, 1):
            songs.append({
                "disc": 1,
                "track": idx,
                "title": trk_title,
                "duration": 0,
            })

    # Fallback for year and genre via iTunes quick lookup
    if not year or not genre:
        try:
            from .core import MusicDownloader
            meta = MusicDownloader._quick_metadata_lookup(album_name, album_artist)
            if meta:
                if not year and meta.get("release_date"):
                    year = meta["release_date"][:4]
                if not genre and meta.get("genre"):
                    genre = meta["genre"]
        except Exception:
            pass

    disc_numbers = {s["disc"] for s in songs if s.get("disc")} or {1}
    disc_count = len(disc_numbers)
    song_count = len(songs)
    total_duration_sec = sum(s["duration"] for s in songs)

    # Navidrome URL resolution
    nav_url = ""
    if queue.navidrome and queue.navidrome.base_url:
        nav_base = queue.navidrome.base_url.rstrip("/")
        try:
            existing = queue.navidrome.find_album_match(album_name, album_artist)
            if existing and existing.get("id"):
                nav_url = f"{nav_base}/#/album/{existing['id']}"
            else:
                nav_url = nav_base
        except Exception:
            nav_url = nav_base

    # Build clean HTML
    html_content = build_album_rich_html(
        album_name=album_name,
        artist_name=album_artist,
        year=year,
        genre=genre,
        disc_count=disc_count,
        song_count=song_count,
        total_duration_sec=total_duration_sec,
        songs=songs,
        nav_url=nav_url,
    )

    # Truncation safeguard if message approaches Telegram 4096 char limit
    if len(html_content) > 3800 and len(songs) > 25:
        truncated_songs = songs[:25]
        remaining = len(songs) - 25
        truncated_songs.append({
            "disc": 1,
            "track": 0,
            "title": f"... y {remaining} canciones más",
            "duration": 0,
        })
        html_content = build_album_rich_html(
            album_name=album_name,
            artist_name=album_artist,
            year=year,
            genre=genre,
            disc_count=disc_count,
            song_count=song_count,
            total_duration_sec=total_duration_sec,
            songs=truncated_songs,
            nav_url=nav_url,
        )

    # Read cover image
    img_bytes = None
    cover_file = target_folder / "cover.jpg"
    if not cover_file.is_file() and target_folder.is_dir():
        for cf in target_folder.rglob("cover.jpg"):
            cover_file = cf
            break
    if cover_file.is_file():
        try:
            img_bytes = cover_file.read_bytes()
        except Exception as e:
            logger.warning("No se pudo leer cover.jpg: %s", e)

    if not img_bytes and album.cover_url:
        try:
            req = urllib.request.Request(album.cover_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                img_bytes = resp.read()
        except Exception as e:
            logger.debug("No se pudo obtener cover desde cover_url: %s", e)

    targets: List[TelegramTarget] = []
    if chat_id:
        for part in str(chat_id).split(","):
            part = part.strip()
            if not part:
                continue
            if ":" in part:
                cid, tid = part.split(":", 1)
                targets.append(TelegramTarget(chat_id=cid.strip(), thread_id=tid.strip() or None))
            else:
                targets.append(TelegramTarget(chat_id=part, thread_id=thread_id))
    if not targets:
        targets = [TelegramTarget(chat_id=chat_id, thread_id=thread_id)]

    all_ok = True
    rich_url = f"https://api.telegram.org/bot{token}/sendRichMessage"

    for tgt in targets:
        payload: Dict[str, Any] = {"chat_id": tgt.chat_id}
        if tgt.thread_id:
            payload["message_thread_id"] = str(tgt.thread_id)

        rich_obj: Dict[str, Any] = {"html": html_content}
        files = {}
        if img_bytes:
            rich_obj["media"] = [{
                "id": "cover",
                "media": {
                    "type": "photo",
                    "media": "attach://cover",
                }
            }]
            files = {"cover": ("cover.jpg", img_bytes, "image/jpeg")}

        payload["rich_message"] = json.dumps(rich_obj)

        try:
            resp = requests.post(rich_url, data=payload, files=files if files else None, timeout=20)
            if tgt.thread_id and resp.status_code == 400 and "message thread not found" in resp.text:
                logger.warning("Thread %s not found in chat %s during sendRichMessage; retrying to chat root", tgt.thread_id, tgt.chat_id)
                payload.pop("message_thread_id", None)
                resp = requests.post(rich_url, data=payload, files=files if files else None, timeout=20)
            resp.raise_for_status()
            res_data = resp.json()
            if not res_data.get("ok"):
                raise Exception(f"sendRichMessage returned error: {res_data.get('description')}")
            logger.info("Notificación estándar de álbum enviada con éxito para '%s - %s' a chat %s", album_artist, album_name, tgt.chat_id)
        except Exception as err:
            logger.warning("Fallo al enviar sendRichMessage para '%s' (%s). Probando alternativa...", album_name, err)
            fallback_sent = False
            effective_tid = tgt.thread_id
            if "message thread not found" in str(err) or (hasattr(err, "response") and err.response and "message thread not found" in err.response.text):
                effective_tid = None

            if img_bytes:
                try:
                    dur_str = format_duration(total_duration_sec)
                    dur_line = f"🕒 <b>Duración:</b> {dur_str}\n" if dur_str else ""
                    link_line = f"\n<a href=\"{nav_url}\">🌐 Escuchar en Navidrome</a>" if nav_url else ""
                    caption = (
                        f"📚 <b>{html.escape(album_name)}</b>\n"
                        f"👤 <b>{html.escape(album_artist)}</b>\n\n"
                        f"📅 <b>Año:</b> {year or 'N/A'} | 📦 <b>Género:</b> {genre or 'N/A'}\n"
                        f"💿 <b>Discos:</b> {disc_count} | 🔢 <b>Canciones:</b> {song_count}\n"
                        f"{dur_line}{link_line}"
                    )
                    photo_url = f"https://api.telegram.org/bot{token}/sendPhoto"
                    photo_data = {"chat_id": tgt.chat_id, "caption": caption, "parse_mode": "HTML"}
                    if effective_tid:
                        photo_data["message_thread_id"] = str(effective_tid)
                    p_resp = requests.post(photo_url, data=photo_data, files={"photo": ("cover.jpg", img_bytes, "image/jpeg")}, timeout=15)
                    if effective_tid and p_resp.status_code == 400 and "message thread not found" in p_resp.text:
                        photo_data.pop("message_thread_id", None)
                        effective_tid = None
                        p_resp = requests.post(photo_url, data=photo_data, files={"photo": ("cover.jpg", img_bytes, "image/jpeg")}, timeout=15)
                    if p_resp.status_code == 200 and p_resp.json().get("ok"):
                        fallback_sent = True
                except Exception as pe:
                    logger.error("Error en fallback sendPhoto: %s", pe)

            if not fallback_sent:
                queue._send_reply(
                    tgt.chat_id,
                    f"📚 <b>{html.escape(album_name)}</b>\n"
                    f"👤 <b>{html.escape(album_artist)}</b>\n"
                    f"✨ <i>Álbum completo descargado ({song_count} canciones).</i>",
                    effective_tid,
                )
            all_ok = False

    return all_ok
