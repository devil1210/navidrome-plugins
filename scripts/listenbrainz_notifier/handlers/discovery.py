#!/usr/bin/env python3
"""
Discovery & Recommendations Handlers:
Handles /descubrir, /novedades, /dl_rec, and /dl_nov.
"""

import html
import logging
import time
from typing import Any, Dict, List, Optional

try:
    from ..modules.models import DownloadJob, ResolvedAlbum, ResolvedTrack
except (ImportError, ValueError):
    from modules.models import DownloadJob, ResolvedAlbum, ResolvedTrack

logger = logging.getLogger("listenbrainz_notifier")


class DiscoveryHandler:
    """Handles recommendations and new releases from YouTube Music and filters against Navidrome."""

    def __init__(
        self,
        discoverer: Any,
        navidrome: Any,
        state_tracker: Any,
        download_queue: Any,
    ):
        self.discoverer = discoverer
        self.navidrome = navidrome
        self.state_tracker = state_tracker
        self.download_queue = download_queue
        self.listener: Optional[Any] = None

    def set_listener(self, listener: Any):
        self.listener = listener

    def on_discover_command(self, chat_id: str, thread_id: Optional[str], raw_args: Optional[str] = None):
        if not self.listener:
            return

        acc_note = f" (cuenta <b>{self.discoverer.account_name}</b>)" if self.discoverer.account_name else ""
        self.listener.send_reply(
            chat_id,
            f"🎧 <i>Consultando recomendaciones en YouTube Music{acc_note} y filtrando álbumes existentes en Navidrome...</i>",
            thread_id,
        )
        limit = 8
        if raw_args and raw_args.strip().isdigit():
            parsed = int(raw_args.strip())
            if 1 <= parsed <= 25:
                limit = parsed

        try:
            recs = self.discoverer.get_recommendations(navidrome=self.navidrome, limit=limit)
        except Exception as ex:
            logger.error("Error obteniendo recomendaciones: %s", ex, exc_info=True)
            self.listener.send_reply(chat_id, f"❌ <b>Error al consultar recomendaciones:</b> {ex}", thread_id)
            return

        if not recs:
            self.listener.send_reply(
                chat_id,
                "✨ <b>¡Tu biblioteca está al día!</b>\nNo se encontraron nuevas recomendaciones en YouTube Music que no tengas ya en Navidrome.",
                thread_id,
            )
            return

        self.state_tracker.save_last_discovered(recs)

        items_lines = []
        for idx, item in enumerate(recs, 1):
            kind_icon = "💿" if item.get("kind") == "album" else "🎵"
            title = html.escape(item.get("title", "Desconocido"))
            artist = html.escape(item.get("artist", "Desconocido"))
            year = item.get("year", "")
            tracks_cnt = item.get("track_count", 0)
            extra = []
            if year:
                extra.append(year)
            if tracks_cnt > 1:
                extra.append(f"{tracks_cnt} pistas")
            extra_str = f" ({', '.join(extra)})" if extra else ""

            src_trk = item.get("source_track")
            if src_trk and src_trk.strip().lower() != item.get("title", "").strip().lower():
                trk_note = f" <i>(incluye «{html.escape(src_trk)}»)</i>"
            else:
                trk_note = ""

            items_lines.append(f"<b>{idx}.</b> {kind_icon} <b>{title}</b>{trk_note}\n   👤 <i>{artist}</i>{extra_str}")

        keyboard = []
        row = []
        for idx, item in enumerate(recs, 1):
            short_art = item.get("artist", "")
            if "," in short_art:
                short_art = short_art.split(",")[0].strip()
            short_art = (short_art[:13] + "..") if len(short_art) > 15 else short_art
            btn_text = f"💿 {idx}. {short_art}" if short_art else f"💿 #{idx}"
            row.append({"text": btn_text, "callback_data": f"dl_rec:{idx}"})
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append([{"text": f"⚡ Descargar Todos los Álbumes ({len(recs)})", "callback_data": "dl_rec:all"}])
        reply_markup = {"inline_keyboard": keyboard}

        content = (
            "✨ <b>Recomendaciones de YouTube Music</b>\n"
            f"<i>Filtradas contra tu biblioteca ({len(recs)} sugerencias no añadidas):</i>\n\n"
            "<blockquote>\n" + "\n\n".join(items_lines) + "\n</blockquote>\n\n"
            "⚡ <i>Descarga en 1 toque con los botones de abajo o escribe</i> <code>/dl_rec [N | all]</code>"
        )

        top_cover = recs[0].get("cover_url") if recs and recs[0].get("cover_url") else None
        self.listener.send_reply(
            chat_id,
            content,
            thread_id=thread_id,
            photo_url=top_cover,
            reply_markup=reply_markup,
        )

    def on_new_releases_command(self, chat_id: str, thread_id: Optional[str], raw_args: Optional[str] = None):
        if not self.listener:
            return

        acc_note = f" (cuenta <b>{self.discoverer.account_name}</b>)" if self.discoverer.account_name else ""
        self.listener.send_reply(
            chat_id,
            f"🆕 <i>Buscando nuevos lanzamientos en YouTube Music{acc_note} y filtrando contra Navidrome...</i>",
            thread_id,
        )
        limit = 8
        if raw_args and raw_args.strip().isdigit():
            parsed = int(raw_args.strip())
            if 1 <= parsed <= 25:
                limit = parsed

        try:
            releases = self.discoverer.get_new_releases(navidrome=self.navidrome, limit=limit)
        except Exception as ex:
            logger.error("Error obteniendo novedades: %s", ex, exc_info=True)
            self.listener.send_reply(chat_id, f"❌ <b>Error al consultar nuevos lanzamientos:</b> {ex}", thread_id)
            return

        if not releases:
            self.listener.send_reply(
                chat_id,
                "✨ <b>¡Tu biblioteca está al día con los estrenos!</b>\nNo se encontraron nuevos lanzamientos pendientes de añadir.",
                thread_id,
            )
            return

        self.state_tracker.save_last_new_releases(releases)

        items_lines = []
        for idx, item in enumerate(releases, 1):
            kind_icon = "💿" if item.get("kind") == "album" else "🎵"
            title = html.escape(item.get("title", "Desconocido"))
            artist = html.escape(item.get("artist", "Desconocido"))
            year = item.get("year", "")
            tracks_cnt = item.get("track_count", 0)
            extra = []
            if year:
                extra.append(year)
            if tracks_cnt > 1:
                extra.append(f"{tracks_cnt} pistas")
            extra_str = f" ({', '.join(extra)})" if extra else ""

            src_trk = item.get("source_track")
            if src_trk and src_trk.strip().lower() != item.get("title", "").strip().lower():
                trk_note = f" <i>(incluye «{html.escape(src_trk)}»)</i>"
            else:
                trk_note = ""

            items_lines.append(f"<b>{idx}.</b> {kind_icon} <b>{title}</b>{trk_note}\n   👤 <i>{artist}</i>{extra_str}")

        keyboard = []
        row = []
        for idx, item in enumerate(releases, 1):
            short_art = item.get("artist", "")
            if "," in short_art:
                short_art = short_art.split(",")[0].strip()
            short_art = (short_art[:13] + "..") if len(short_art) > 15 else short_art
            btn_text = f"🔥 {idx}. {short_art}" if short_art else f"🔥 #{idx}"
            row.append({"text": btn_text, "callback_data": f"dl_nov:{idx}"})
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append([{"text": f"⚡ Descargar Todas las Novedades ({len(releases)})", "callback_data": "dl_nov:all"}])
        reply_markup = {"inline_keyboard": keyboard}

        content = (
            "🔥 <b>Nuevos Lanzamientos & Estrenos de YouTube Music</b>\n"
            f"<i>Filtrados contra tu biblioteca ({len(releases)} novedades no añadidas):</i>\n\n"
            "<blockquote>\n" + "\n\n".join(items_lines) + "\n</blockquote>\n\n"
            "⚡ <i>Descarga en 1 toque con los botones de abajo o escribe</i> <code>/dl_nov [N | all]</code>"
        )

        top_cover = releases[0].get("cover_url") if releases and releases[0].get("cover_url") else None
        self.listener.send_reply(
            chat_id,
            content,
            thread_id=thread_id,
            photo_url=top_cover,
            reply_markup=reply_markup,
        )

    def on_download_rec_command(self, chat_id: str, thread_id: Optional[str], raw_args: Optional[str] = None):
        if not self.listener:
            return

        descargas_tid = thread_id
        if hasattr(self.listener, "get_or_create_topic"):
            descargas_tid = self.listener.get_or_create_topic(chat_id, "descargas", "📥 Descargas", icon_color=7322096, caller_thread_id=thread_id) or thread_id

        items = self.state_tracker.get_last_discovered()
        if not items:
            self.listener.send_reply(
                chat_id,
                "⚠️ No hay recomendaciones recientes registradas en memoria.\n"
                "Ejecuta primero <code>/descubrir</code> para consultar sugerencias.",
                descargas_tid,
            )
            return

        arg = (raw_args or "1").strip().lower()
        selected_items: List[Dict[str, Any]] = []
        if arg in ("all", "todo", "todos"):
            selected_items = items
            job_title = f"Lote de Recomendaciones ({len(selected_items)} álbumes)"
        elif arg.isdigit():
            idx = int(arg)
            if 1 <= idx <= len(items):
                selected_items = [items[idx - 1]]
                sel = selected_items[0]
                job_title = f"Recomendación: {sel.get('title')} - {sel.get('artist')}"
            else:
                self.listener.send_reply(
                    chat_id,
                    f"⚠️ Número inválido. Por favor indica un número entre 1 y {len(items)}, o <code>all</code>.\n"
                    f"Ejemplo: <code>/dl_rec 1</code>",
                    descargas_tid,
                )
                return
        else:
            self.listener.send_reply(
                chat_id,
                f"⚠️ Comando no reconocido. Usa <code>/dl_rec [1-{len(items)}]</code> o <code>/dl_rec all</code>.",
                descargas_tid,
            )
            return

        albums_to_download: List[ResolvedAlbum] = []
        tracks_to_download: List[ResolvedTrack] = []

        for it in selected_items:
            if it.get("kind") == "album":
                albums_to_download.append(ResolvedAlbum(
                    url=it["url"],
                    album_name=it["title"],
                    artist_name=it.get("artist", ""),
                    tracks=it.get("tracks", []),
                ))
            else:
                tracks_to_download.append(ResolvedTrack(
                    url=it["url"],
                    title=it["title"],
                    artist=it.get("artist", ""),
                ))

        job = DownloadJob(
            job_id=f"rec_{int(time.time()*1000)}",
            chat_id=chat_id,
            thread_id=descargas_tid,
            title=job_title,
            albums=albums_to_download,
            tracks=tracks_to_download,
            source_desc=f"/dl_rec {arg}",
        )

        pos = self.download_queue.enqueue(job)
        total_items = len(albums_to_download) + len(tracks_to_download)
        if self.download_queue.paused:
            self.listener.send_reply(
                chat_id,
                f"⏸️ <b>Añadido a la cola en pausa:</b>\n"
                f"• <b>{job.title}</b>\n"
                f"• Elementos: {total_items}\n\n"
                f"💡 <i>La cola está pausada por seguridad (Circuit Breaker). Usa <code>/reanudar</code> para comenzar la descarga.</i>",
                descargas_tid,
            )
        elif pos > 0:
            self.listener.send_reply(
                chat_id,
                f"⏳ <b>Añadido a la cola de descargas (Posición #{pos} en espera):</b>\n"
                f"• <b>{job.title}</b>\n"
                f"• Elementos: {total_items}\n\n"
                f"<i>Se descargará automáticamente al finalizar las tareas en curso.</i>",
                descargas_tid,
            )

    def on_download_nov_command(self, chat_id: str, thread_id: Optional[str], raw_args: Optional[str] = None):
        if not self.listener:
            return

        descargas_tid = thread_id
        if hasattr(self.listener, "get_or_create_topic"):
            descargas_tid = self.listener.get_or_create_topic(chat_id, "descargas", "📥 Descargas", icon_color=7322096, caller_thread_id=thread_id) or thread_id

        items = self.state_tracker.get_last_new_releases()
        if not items:
            self.listener.send_reply(
                chat_id,
                "⚠️ No hay novedades recientes registradas en memoria.\n"
                "Ejecuta primero <code>/novedades</code> para consultar estrenos.",
                descargas_tid,
            )
            return

        arg = (raw_args or "1").strip().lower()
        selected_items: List[Dict[str, Any]] = []
        if arg in ("all", "todo", "todos"):
            selected_items = items
            job_title = f"Lote de Novedades ({len(selected_items)} álbumes)"
        elif arg.isdigit():
            idx = int(arg)
            if 1 <= idx <= len(items):
                selected_items = [items[idx - 1]]
                sel = selected_items[0]
                job_title = f"Novedad: {sel.get('title')} - {sel.get('artist')}"
            else:
                self.listener.send_reply(
                    chat_id,
                    f"⚠️ Número inválido. Por favor indica un número entre 1 y {len(items)}, o <code>all</code>.\n"
                    f"Ejemplo: <code>/dl_nov 1</code>",
                    descargas_tid,
                )
                return
        else:
            self.listener.send_reply(
                chat_id,
                f"⚠️ Comando no reconocido. Usa <code>/dl_nov [1-{len(items)}]</code> o <code>/dl_nov all</code>.",
                descargas_tid,
            )
            return

        albums_to_download: List[ResolvedAlbum] = []
        tracks_to_download: List[ResolvedTrack] = []

        for it in selected_items:
            if it.get("kind") == "album":
                albums_to_download.append(ResolvedAlbum(
                    url=it["url"],
                    album_name=it["title"],
                    artist_name=it.get("artist", ""),
                    tracks=it.get("tracks", []),
                ))
            else:
                tracks_to_download.append(ResolvedTrack(
                    url=it["url"],
                    title=it["title"],
                    artist=it.get("artist", ""),
                ))

        job = DownloadJob(
            job_id=f"nov_{int(time.time()*1000)}",
            chat_id=chat_id,
            thread_id=descargas_tid,
            title=job_title,
            albums=albums_to_download,
            tracks=tracks_to_download,
            source_desc=f"/dl_nov {arg}",
        )

        pos = self.download_queue.enqueue(job)
        total_items = len(albums_to_download) + len(tracks_to_download)
        if self.download_queue.paused:
            self.listener.send_reply(
                chat_id,
                f"⏸️ <b>Añadido a la cola en pausa:</b>\n"
                f"• <b>{job.title}</b>\n"
                f"• Elementos: {total_items}\n\n"
                f"💡 <i>La cola está pausada por seguridad (Circuit Breaker). Usa <code>/reanudar</code> para comenzar la descarga.</i>",
                descargas_tid,
            )
        elif pos > 0:
            self.listener.send_reply(
                chat_id,
                f"⏳ <b>Añadido a la cola de descargas (Posición #{pos} en espera):</b>\n"
                f"• <b>{job.title}</b>\n"
                f"• Elementos: {total_items}\n\n"
                f"<i>Se descargará automáticamente al finalizar las tareas en curso.</i>",
                descargas_tid,
            )
