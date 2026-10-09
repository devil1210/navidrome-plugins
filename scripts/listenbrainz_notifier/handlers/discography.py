#!/usr/bin/env python3
"""
Discography & On-Demand Download Handlers:
Handles /add, /dl, artist discography interactive checklist, and failed download retries.
"""

import html
import json
import logging
import os
import re
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

try:
    from ..modules.models import DownloadJob, ResolvedAlbum, ResolvedTrack
    from ..modules.resolver import UniversalResolver, parse_sync_args
    from ..modules.syncer import run_sync
    from ..modules.telegram import build_checklist_menu, build_discography_menu
except (ImportError, ValueError):
    from modules.models import DownloadJob, ResolvedAlbum, ResolvedTrack
    from modules.resolver import UniversalResolver, parse_sync_args
    from modules.syncer import run_sync
    from modules.telegram import build_checklist_menu, build_discography_menu

logger = logging.getLogger("listenbrainz_notifier")


def format_queue_note(download_queue: Any, pos: int) -> str:
    if download_queue.paused:
        return " ⏸️ <i>(Cola pausada por seguridad. Usa /reanudar para comenzar)</i>"
    if pos > 0:
        return f" (Posición #{pos} en cola)"
    return " (Iniciando descarga inmediata)"


class DiscographyHandler:
    """Manages discography sessions, on-demand downloads (/add), and batch downloads (/dl)."""

    def __init__(
        self,
        download_queue: Any,
        universal_resolver: UniversalResolver,
        navidrome: Any,
        state_tracker: Any,
        enricher: Any,
        target_names_ordered: List[str],
        default_max_albums: int,
        fallback_to_track: bool,
        session_cache_file: Optional[Path] = None,
    ):
        self.download_queue = download_queue
        self.universal_resolver = universal_resolver
        self.navidrome = navidrome
        self.state_tracker = state_tracker
        self.enricher = enricher
        self.target_names_ordered = target_names_ordered
        self.default_max_albums = default_max_albums
        self.fallback_to_track = fallback_to_track
        self.session_cache_file = session_cache_file or (Path(__file__).resolve().parent.parent / ".disc_sessions.json")
        self.active_disc_sessions: Dict[str, Dict[str, Any]] = self._load_disc_sessions()
        self.listener: Optional[Any] = None

    def set_listener(self, listener: Any):
        self.listener = listener

    def _load_disc_sessions(self) -> Dict[str, Dict[str, Any]]:
        sessions: Dict[str, Dict[str, Any]] = {}
        if not self.session_cache_file.exists():
            return sessions
        try:
            with open(self.session_cache_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            now = time.time()
            for tok, data in raw.items():
                if now - data.get("created_at", 0) > 7200:
                    continue
                albums = [ResolvedAlbum(**a) for a in data.get("albums", [])]
                singles = [ResolvedAlbum(**s) for s in data.get("singles", [])]
                selected = set(data.get("selected", []))
                sessions[tok] = {
                    "artist": data.get("artist", ""),
                    "albums": albums,
                    "singles": singles,
                    "selected": selected,
                    "chat_id": data.get("chat_id"),
                    "thread_id": data.get("thread_id"),
                    "created_at": data.get("created_at", now),
                }
            if sessions:
                logger.info("Restored %d active discography session(s) from %s", len(sessions), self.session_cache_file)
        except Exception as e:
            logger.warning("Could not load discography sessions from %s: %s", self.session_cache_file, e)
        return sessions

    def _save_disc_sessions(self):
        try:
            raw: Dict[str, Any] = {}
            now = time.time()
            for tok, sess in self.active_disc_sessions.items():
                if now - sess.get("created_at", 0) > 7200:
                    continue
                raw[tok] = {
                    "artist": sess.get("artist", ""),
                    "albums": [asdict(a) for a in sess.get("albums", [])],
                    "singles": [asdict(s) for s in sess.get("singles", [])],
                    "selected": list(sess.get("selected", [])),
                    "chat_id": sess.get("chat_id"),
                    "thread_id": sess.get("thread_id"),
                    "created_at": sess.get("created_at", now),
                }
            tmp = self.session_cache_file.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False, indent=2)
            tmp.replace(self.session_cache_file)
        except Exception as e:
            logger.error("Could not save discography sessions to %s: %s", self.session_cache_file, e)

    def on_download_command(self, chat_id: str, thread_id: Optional[str], raw_args: Optional[str] = None):
        descargas_tid = thread_id
        if self.listener and hasattr(self.listener, "get_or_create_topic"):
            descargas_tid = self.listener.get_or_create_topic(chat_id, "descargas", "📥 Descargas", icon_color=7322096, caller_thread_id=thread_id) or thread_id

        current_limit = self.state_tracker.get_limit(default_limit=self.default_max_albums)
        target_pls, batch_num, batch_size = parse_sync_args(raw_args, default_limit=current_limit)
        is_all = (batch_size == 0)

        target_pls_name = target_pls
        last_batch = self.state_tracker.get_last_batch(target_pls)
        if not target_pls_name and last_batch:
            target_pls_name = last_batch.get("playlist_name")
        if not target_pls_name:
            target_pls_name = self.target_names_ordered[0] if self.target_names_ordered else "Descubrimiento Diario"

        albums_to_download: List[ResolvedAlbum] = []
        tracks_to_download: List[ResolvedTrack] = []

        can_use_cache = (
            last_batch is not None
            and (not target_pls or last_batch.get("playlist_name", "").lower() == target_pls.lower())
            and (
                (not is_all and last_batch.get("batch_number") == batch_num)
                or (is_all and last_batch.get("total_batches", 1) <= 1)
            )
        )

        if can_use_cache:
            for a in last_batch.get("albums", []):
                albums_to_download.append(ResolvedAlbum(
                    url=a["url"],
                    album_name=a["album_name"],
                    artist_name=a.get("artist_name", ""),
                    tracks=a.get("tracks", []),
                    cover_url=a.get("cover_url"),
                ))
            for t in last_batch.get("tracks", []):
                tracks_to_download.append(ResolvedTrack(
                    url=t["url"],
                    title=t["title"],
                    artist=t.get("artist", ""),
                    cover_url=t.get("cover_url"),
                ))
        else:
            desc_label = "Todos los lotes" if is_all else f"Lote {batch_num}"
            if self.listener:
                self.listener.send_reply(
                    chat_id,
                    f"🔍 <i>Consultando y resolviendo canciones de <b>{target_pls_name}</b> ({desc_label})...</i>",
                    descargas_tid,
                )
            query_names = {target_pls_name}
            run_sync(
                navidrome=self.navidrome,
                telegram=None,
                state_tracker=self.state_tracker,
                target_names=query_names,
                fallback_to_track=self.fallback_to_track,
                force=True,
                dry_run=True,
                enricher=self.enricher,
                batch_number=batch_num,
                batch_size=batch_size,
            )
            fresh_batch = self.state_tracker.get_last_batch(target_pls_name)
            if fresh_batch:
                for a in fresh_batch.get("albums", []):
                    albums_to_download.append(ResolvedAlbum(
                        url=a["url"],
                        album_name=a["album_name"],
                        artist_name=a.get("artist_name", ""),
                        tracks=a.get("tracks", []),
                        cover_url=a.get("cover_url"),
                    ))
                for t in fresh_batch.get("tracks", []):
                    tracks_to_download.append(ResolvedTrack(
                        url=t["url"],
                        title=t["title"],
                        artist=t.get("artist", ""),
                        cover_url=t.get("cover_url"),
                    ))

        total_items = len(albums_to_download) + len(tracks_to_download)
        if total_items == 0:
            if self.listener:
                self.listener.send_reply(
                    chat_id,
                    f"ℹ️ No se encontraron álbumes ni pistas pendientes para descargar en <b>{target_pls_name}</b>.",
                    descargas_tid,
                )
            return

        batch_desc = f"Lote {batch_num}" if not is_all else "Todos los lotes"
        job = DownloadJob(
            job_id=f"dl_{int(time.time()*1000)}",
            chat_id=chat_id,
            thread_id=descargas_tid,
            title=f"Playlist {target_pls_name} ({batch_desc})",
            albums=albums_to_download,
            tracks=tracks_to_download,
            source_desc=f"{target_pls_name} ({batch_desc})",
        )

        pos = self.download_queue.enqueue(job)
        if self.download_queue.paused:
            if self.listener:
                self.listener.send_reply(
                    chat_id,
                    f"⏸️ <b>Añadido a la cola en pausa:</b>\n"
                    f"• <b>{job.title}</b>\n"
                    f"• Elementos: {total_items} ({len(albums_to_download)} álbumes, {len(tracks_to_download)} pistas)\n\n"
                    f"💡 <i>La cola está pausada por seguridad (Circuit Breaker). Usa <code>/reanudar</code> para comenzar la descarga.</i>",
                    descargas_tid,
                )
        elif pos > 0:
            if self.listener:
                self.listener.send_reply(
                    chat_id,
                    f"⏳ <b>Añadido a la cola de descargas (Posición #{pos} en espera):</b>\n"
                    f"• <b>{job.title}</b>\n"
                    f"• Elementos: {total_items} ({len(albums_to_download)} álbumes, {len(tracks_to_download)} pistas)\n\n"
                    f"<i>Se descargará automáticamente al finalizar las tareas en curso.</i>",
                    descargas_tid,
                )

    def on_add_command(self, chat_id: str, thread_id: Optional[str], raw_args: str):
        descargas_tid = thread_id
        if self.listener and hasattr(self.listener, "get_or_create_topic"):
            descargas_tid = self.listener.get_or_create_topic(chat_id, "descargas", "📥 Descargas", icon_color=7322096, caller_thread_id=thread_id) or thread_id

        status_msg_id = None
        if self.listener:
            status_msg_id = self.listener.send_reply(
                chat_id,
                f"🔍 <i>Analizando solicitud:</i> <code>{raw_args}</code>...",
                descargas_tid,
            )

        raw_lower = raw_args.lower()
        filter_mode = None
        clean_args = raw_args
        if re.search(r'\b(?:solo\s+)?(?:albumes|álbumes|albums|discos)\b', raw_lower):
            filter_mode = "albums"
            clean_args = re.sub(r'\b(?:solo\s+)?(?:albumes|álbumes|albums|discos)\b', '', raw_args, flags=re.IGNORECASE).strip()
        elif re.search(r'\b(?:solo\s+)?(?:singles|sencillos|eps)\b', raw_lower):
            filter_mode = "singles"
            clean_args = re.sub(r'\b(?:solo\s+)?(?:singles|sencillos|eps)\b', '', raw_args, flags=re.IGNORECASE).strip()
        elif re.search(r'\b(?:todo|todos|all|completa|completo)\b', raw_lower):
            filter_mode = "all"
            clean_args = re.sub(r'\b(?:todo|todos|all|completa|completo)\b', '', raw_args, flags=re.IGNORECASE).strip()

        if not clean_args:
            clean_args = raw_args

        res = self.universal_resolver.resolve_input(clean_args)
        if res.get("status") != "success":
            fail_msg = f"⚠️ <b>No se pudo procesar la solicitud:</b>\n{res.get('message', 'Sin detalles')}"
            if self.listener:
                if status_msg_id:
                    self.listener.edit_message_text(chat_id, status_msg_id, fail_msg)
                else:
                    self.listener.send_reply(chat_id, fail_msg, descargas_tid)
            return

        albums = res.get("albums", [])
        tracks = res.get("tracks", [])
        if not albums and not tracks:
            empty_msg = f"⚠️ No se encontraron álbumes ni pistas disponibles para: <code>{raw_args}</code>"
            if self.listener:
                if status_msg_id:
                    self.listener.edit_message_text(chat_id, status_msg_id, empty_msg)
                else:
                    self.listener.send_reply(chat_id, empty_msg, descargas_tid)
            return

        kind = res.get("kind", "contenido")
        title_msg = res.get("title", raw_args)
        is_discography = ("total_albums" in res and "total_singles" in res) or kind == "artist"

        if is_discography and albums:
            studio_albums = [a for a in albums if not a.is_single]
            singles = [a for a in albums if a.is_single]
            artist_name = res.get("artist") or title_msg

            now = time.time()
            expired = [k for k, v in self.active_disc_sessions.items() if now - v.get("created_at", 0) > 7200]
            for k in expired:
                self.active_disc_sessions.pop(k, None)
            if expired:
                self._save_disc_sessions()

            if filter_mode == "albums":
                target_list = studio_albums or albums
                job = DownloadJob(
                    job_id=f"add_{int(time.time()*1000)}",
                    chat_id=chat_id,
                    thread_id=descargas_tid,
                    title=f"{artist_name} [Álbumes de estudio]",
                    albums=target_list,
                    tracks=[],
                    source_desc=f"/add {raw_args} (Solo Álbumes)",
                )
                pos = self.download_queue.enqueue(job)
                queue_note = format_queue_note(self.download_queue, pos)
                if self.listener:
                    self.listener.send_reply(
                        chat_id,
                        f"💿 <b>{html.escape(artist_name)}</b>: Descargando los <b>{len(target_list)} álbumes de estudio</b>.{queue_note}",
                        descargas_tid,
                    )
                return

            elif filter_mode == "singles":
                target_list = singles or albums
                job = DownloadJob(
                    job_id=f"add_{int(time.time()*1000)}",
                    chat_id=chat_id,
                    thread_id=descargas_tid,
                    title=f"{artist_name} [Sencillos y EPs]",
                    albums=target_list,
                    tracks=[],
                    source_desc=f"/add {raw_args} (Solo Sencillos)",
                )
                pos = self.download_queue.enqueue(job)
                queue_note = format_queue_note(self.download_queue, pos)
                if self.listener:
                    self.listener.send_reply(
                        chat_id,
                        f"🎵 <b>{html.escape(artist_name)}</b>: Descargando los <b>{len(target_list)} sencillos y EPs</b>.{queue_note}",
                        descargas_tid,
                    )
                return

            elif filter_mode == "all":
                job = DownloadJob(
                    job_id=f"add_{int(time.time()*1000)}",
                    chat_id=chat_id,
                    thread_id=descargas_tid,
                    title=f"{artist_name} [Discografía completa]",
                    albums=albums,
                    tracks=tracks,
                    source_desc=f"/add {raw_args} (Todo)",
                )
                pos = self.download_queue.enqueue(job)
                queue_note = format_queue_note(self.download_queue, pos)
                if self.listener:
                    self.listener.send_reply(
                        chat_id,
                        f"⚡ <b>{html.escape(artist_name)}</b>: Descargando discografía completa (<b>{len(albums)} lanzamientos</b>).{queue_note}",
                        descargas_tid,
                    )
                return

            else:
                token = uuid.uuid4().hex[:8]
                self.active_disc_sessions[token] = {
                    "artist": artist_name,
                    "albums": studio_albums,
                    "singles": singles,
                    "selected": set(range(len(studio_albums))),
                    "chat_id": chat_id,
                    "thread_id": descargas_tid,
                    "created_at": time.time(),
                }
                self._save_disc_sessions()
                menu_text, menu_markup, menu_rich = build_discography_menu(artist_name, len(studio_albums), len(singles), token)
                edited = False
                if self.listener and status_msg_id:
                    edited = self.listener.edit_message_text(
                        chat_id,
                        status_msg_id,
                        menu_text,
                        reply_markup=menu_markup,
                        rich_message=menu_rich,
                    )
                if not edited and self.listener:
                    self.listener.send_reply(
                        chat_id,
                        menu_text,
                        thread_id=descargas_tid,
                        reply_markup=menu_markup,
                        rich_message=menu_rich,
                    )
                return

        job = DownloadJob(
            job_id=f"add_{int(time.time()*1000)}",
            chat_id=chat_id,
            thread_id=descargas_tid,
            title=f"{title_msg} [{kind}]",
            albums=albums,
            tracks=tracks,
            source_desc=f"/add {raw_args}",
            status_msg_id=status_msg_id,
        )
        pos = self.download_queue.enqueue(job)
        total_items = len(albums) + len(tracks)
        if self.download_queue.paused:
            pause_text = (
                f"⏸️ <b>Añadido a la cola en pausa:</b>\n"
                f"• <b>{job.title}</b>\n"
                f"• Elementos: {total_items}\n\n"
                f"💡 <i>La cola está pausada por seguridad (Circuit Breaker). Usa <code>/reanudar</code> para comenzar la descarga.</i>"
            )
            if self.listener:
                if status_msg_id:
                    self.listener.edit_message_text(chat_id, status_msg_id, pause_text)
                else:
                    self.listener.send_reply(chat_id, pause_text, descargas_tid)
        elif pos > 0:
            wait_text = (
                f"⏳ <b>Añadido a la cola de descargas (Posición #{pos} en espera):</b>\n"
                f"• <b>{job.title}</b>\n"
                f"• Elementos: {total_items}\n\n"
                f"<i>Se descargará automáticamente al finalizar las tareas previas.</i>"
            )
            if self.listener:
                if status_msg_id:
                    self.listener.edit_message_text(chat_id, status_msg_id, wait_text)
                else:
                    self.listener.send_reply(chat_id, wait_text, descargas_tid)

    def on_add_disc_callback(self, chat_id: str, thread_id: Optional[str], message_id: int, cq_id: str, cq_data: str):
        if not self.listener:
            return

        descargas_tid = thread_id
        if hasattr(self.listener, "get_or_create_topic"):
            descargas_tid = self.listener.get_or_create_topic(chat_id, "descargas", "📥 Descargas", icon_color=7322096, caller_thread_id=thread_id) or thread_id

        parts = cq_data.split(":")
        action = parts[1] if len(parts) > 1 else ""
        token = parts[2] if len(parts) > 2 else ""

        logger.info("Received discography callback action '%s' (token: %s) from chat %s", action, token, chat_id)

        sess = self.active_disc_sessions.get(token)
        if not sess and action != "noop":
            logger.warning("Discography session not found or expired for token: %s", token)
            self.listener.answer_callback_query(cq_id, text="⚠️ La sesión ha expirado. Ejecuta /add nuevamente.", show_alert=True)
            return

        target_tid = descargas_tid or (sess.get("thread_id") if sess else None) or thread_id

        if action == "noop":
            self.listener.answer_callback_query(cq_id)
            return

        elif action == "cancel":
            self.active_disc_sessions.pop(token, None)
            self._save_disc_sessions()
            self.listener.answer_callback_query(cq_id, text="Descarga cancelada.")
            self.listener.edit_message_text(chat_id, message_id, f"❌ <i>Descarga cancelada para <b>{html.escape(sess['artist'])}</b>.</i>")

        elif action == "menu":
            self.listener.answer_callback_query(cq_id)
            text, markup, rich_msg = build_discography_menu(sess["artist"], len(sess["albums"]), len(sess["singles"]), token)
            self.listener.edit_message_text(chat_id, message_id, text, reply_markup=markup, rich_message=rich_msg)

        elif action == "check":
            self.listener.answer_callback_query(cq_id)
            page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
            text, markup, rich_msg = build_checklist_menu(sess, token, page=page)
            self.listener.edit_message_text(chat_id, message_id, text, reply_markup=markup, rich_message=rich_msg)

        elif action == "tog":
            idx = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else -1
            page = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 1
            if idx in sess["selected"]:
                sess["selected"].remove(idx)
            else:
                sess["selected"].add(idx)
            self._save_disc_sessions()
            self.listener.answer_callback_query(cq_id)
            text, markup, rich_msg = build_checklist_menu(sess, token, page=page)
            self.listener.edit_message_text(chat_id, message_id, text, reply_markup=markup, rich_message=rich_msg)

        elif action == "inv":
            page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
            if len(sess["selected"]) == len(sess["albums"]):
                sess["selected"].clear()
            else:
                sess["selected"] = set(range(len(sess["albums"])))
            self._save_disc_sessions()
            self.listener.answer_callback_query(cq_id)
            text, markup, rich_msg = build_checklist_menu(sess, token, page=page)
            self.listener.edit_message_text(chat_id, message_id, text, reply_markup=markup, rich_message=rich_msg)

        elif action == "albums":
            albums = sess["albums"]
            if not albums:
                self.listener.answer_callback_query(cq_id, text="No hay álbumes de estudio disponibles.", show_alert=True)
                return
            self.listener.answer_callback_query(cq_id, text=f"⏳ Descargando {len(albums)} álbumes...")
            job = DownloadJob(
                job_id=f"add_{int(time.time()*1000)}",
                chat_id=chat_id,
                thread_id=target_tid,
                title=f"{sess['artist']} [Álbumes de estudio]",
                albums=albums,
                tracks=[],
                source_desc=f"/add {sess['artist']} (Solo Álbumes)",
            )
            pos = self.download_queue.enqueue(job)
            queue_note = format_queue_note(self.download_queue, pos)
            self.listener.edit_message_text(
                chat_id,
                message_id,
                f"✅ <b>{html.escape(sess['artist'])}:</b> Se han añadido los <b>{len(albums)} álbumes de estudio</b> a la cola de descargas.{queue_note}",
            )
            self.active_disc_sessions.pop(token, None)
            self._save_disc_sessions()

        elif action == "singles":
            singles = sess["singles"]
            if not singles:
                self.listener.answer_callback_query(cq_id, text="No hay sencillos disponibles.", show_alert=True)
                return
            self.listener.answer_callback_query(cq_id, text=f"⏳ Descargando {len(singles)} sencillos...")
            job = DownloadJob(
                job_id=f"add_{int(time.time()*1000)}",
                chat_id=chat_id,
                thread_id=target_tid,
                title=f"{sess['artist']} [Sencillos y EPs]",
                albums=singles,
                tracks=[],
                source_desc=f"/add {sess['artist']} (Solo Sencillos)",
            )
            pos = self.download_queue.enqueue(job)
            queue_note = format_queue_note(self.download_queue, pos)
            self.listener.edit_message_text(
                chat_id,
                message_id,
                f"✅ <b>{html.escape(sess['artist'])}</b>: Se han añadido los <b>{len(singles)} sencillos y EPs</b> a la cola de descargas.{queue_note}",
            )
            self.active_disc_sessions.pop(token, None)
            self._save_disc_sessions()

        elif action == "all":
            all_items = sess["albums"] + sess["singles"]
            self.listener.answer_callback_query(cq_id, text=f"⏳ Descargando discografía completa ({len(all_items)})...")
            job = DownloadJob(
                job_id=f"add_{int(time.time()*1000)}",
                chat_id=chat_id,
                thread_id=target_tid,
                title=f"{sess['artist']} [Discografía completa]",
                albums=all_items,
                tracks=[],
                source_desc=f"/add {sess['artist']} (Todo)",
            )
            pos = self.download_queue.enqueue(job)
            queue_note = format_queue_note(self.download_queue, pos)
            self.listener.edit_message_text(
                chat_id,
                message_id,
                f"✅ <b>{html.escape(sess['artist'])}</b>: Se ha añadido la discografía completa (<b>{len(all_items)} lanzamientos</b>) a la cola de descargas.{queue_note}",
            )
            self.active_disc_sessions.pop(token, None)
            self._save_disc_sessions()

        elif action == "dl_sel":
            selected_idx = sorted(sess["selected"])
            if not selected_idx:
                self.listener.answer_callback_query(cq_id, text="⚠️ No has seleccionado ningún álbum.", show_alert=True)
                return
            selected_albums = [sess["albums"][i] for i in selected_idx]
            self.listener.answer_callback_query(cq_id, text=f"⏳ Descargando {len(selected_albums)} álbumes seleccionados...")
            job = DownloadJob(
                job_id=f"add_{int(time.time()*1000)}",
                chat_id=chat_id,
                thread_id=target_tid,
                title=f"{sess['artist']} [{len(selected_albums)} álbumes seleccionados]",
                albums=selected_albums,
                tracks=[],
                source_desc=f"/add {sess['artist']} (Seleccionados)",
            )
            pos = self.download_queue.enqueue(job)
            queue_note = format_queue_note(self.download_queue, pos)
            self.listener.edit_message_text(
                chat_id,
                message_id,
                f"✅ <b>{html.escape(sess['artist'])}</b>: Se han añadido los <b>{len(selected_albums)} álbumes seleccionados</b> a la cola de descargas.{queue_note}",
            )
            self.active_disc_sessions.pop(token, None)
            self._save_disc_sessions()

    def on_retry_failed_callback(self, chat_id: str, thread_id: Optional[str], message_id: int, cq_id: str, token: str):
        if not self.listener:
            return

        sess = self.download_queue.get_retry_session(token)
        if not sess:
            self.listener.answer_callback_query(cq_id, text="⚠️ La sesión de reintento ha expirado o ya fue procesada.", show_alert=True)
            return

        albums = [ResolvedAlbum(**a) for a in sess.get("albums", [])]
        tracks = [ResolvedTrack(**t) for t in sess.get("tracks", [])]
        total_items = len(albums) + len(tracks)
        if total_items == 0:
            self.listener.answer_callback_query(cq_id, text="No hay elementos fallidos para reintentar.", show_alert=True)
            return

        self.listener.answer_callback_query(cq_id, text=f"🔄 Reintentando {total_items} descargas...")
        descargas_tid = thread_id or sess.get("thread_id")
        if hasattr(self.listener, "get_or_create_topic"):
            descargas_tid = self.listener.get_or_create_topic(chat_id, "descargas", "📥 Descargas", icon_color=7322096, caller_thread_id=descargas_tid) or descargas_tid

        retry_job = DownloadJob(
            job_id=f"retry_{int(time.time()*1000)}",
            chat_id=chat_id,
            thread_id=descargas_tid,
            title=f"Reintento fallidos ({total_items}): {sess.get('title', 'Lote')}",
            albums=albums,
            tracks=tracks,
            source_desc=f"Reintento fallidos ({total_items})",
        )
        pos = self.download_queue.enqueue(retry_job)
        queue_note = format_queue_note(self.download_queue, pos)
        self.listener.send_reply(
            chat_id,
            f"🔄 <b>Reintentando descargas fallidas ({total_items}):</b>\n"
            f"• Se han añadido los <b>{len(albums)} álbumes/sencillos</b> y <b>{len(tracks)} canciones</b> pendientes a la cola de descargas.{queue_note}",
            descargas_tid,
        )
        self.download_queue.pop_retry_session(token)
        try:
            self.listener.edit_message_reply_markup(chat_id, message_id, reply_markup=None)
        except Exception:
            pass
