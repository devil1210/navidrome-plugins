#!/usr/bin/env python3
"""
System & Settings Handlers:
Handles /status, /limit, /descartar, and /restaurar commands.
"""

import html
import logging
import re
import time
from pathlib import Path
from typing import Any, List, Optional, Set, Union

try:
    from ..modules.parser import TrackParser
    from ..modules.auditor import IncompleteAlbumAuditor
    from ..modules.consolidator import FolderConsolidator
    from ..modules.nightly import NightlyDownloadManager
    from ..modules.models import DownloadJob, ResolvedAlbum
except (ImportError, ValueError):
    from modules.parser import TrackParser
    from modules.auditor import IncompleteAlbumAuditor
    from modules.consolidator import FolderConsolidator
    from modules.nightly import NightlyDownloadManager
    from modules.models import DownloadJob, ResolvedAlbum

logger = logging.getLogger("listenbrainz_notifier")


def to_windows_path(linux_path: Union[str, Path]) -> str:
    s = str(linux_path).replace("/", "\\")
    if "\\media\\" in s:
        idx = s.index("\\media\\")
        return "M:" + s[idx + len("\\media"):]
    if s.startswith("media\\"):
        return "M:" + s[len("media"):]
    return s


class SystemHandler:
    """Handles status queries, batch limit changes, discarding items, and restoring discarded items."""

    def __init__(
        self,
        state_tracker: Any,
        download_queue: Any,
        navidrome: Any,
        discoverer: Any,
        target_names: Set[str],
        default_max_albums: int,
        daemon_interval: int,
        nav_url: str,
        download_music_dir: str,
        library_music_dir: str,
    ):
        self.state_tracker = state_tracker
        self.download_queue = download_queue
        self.navidrome = navidrome
        self.discoverer = discoverer
        self.target_names = target_names
        self.default_max_albums = default_max_albums
        self.daemon_interval = daemon_interval
        self.nav_url = nav_url
        self.download_music_dir = download_music_dir
        self.library_music_dir = library_music_dir
        self.listener: Optional[Any] = None

    def set_listener(self, listener: Any):
        self.listener = listener

    def on_status_command(self) -> str:
        pls_str = ", ".join(self.target_names) if self.target_names else "Todas las playlists"
        last_sync = "Nunca"
        if self.state_tracker.state:
            times = [v.get("last_processed") for k, v in self.state_tracker.state.items() if isinstance(v, dict) and v.get("last_processed")]
            if times:
                last_sync = max(times)
        curr_limit = self.state_tracker.get_limit(default_limit=self.default_max_albums)
        limit_str = f"{curr_limit} álbumes" if curr_limit > 0 else "Sin límite"
        dl_status = self.download_queue.get_status()
        win_dest = to_windows_path(self.library_music_dir)
        ytm_status = f"Conectado ({self.discoverer.account_name})" if self.discoverer.is_authenticated else "Modo público"
        return (
            "🤖 <b>ListenBrainz Navidrome Notifier Bot</b>\n\n"
            f"• <b>Playlists vigiladas:</b> <code>{pls_str}</code>\n"
            f"• <b>Servidor Navidrome:</b> <code>{self.nav_url}</code>\n"
            f"• <b>YouTube Music:</b> <code>{ytm_status}</code>\n"
            f"• <b>Límite por lote:</b> <code>{limit_str}</code> (modificar con <code>/limit [N]</code>)\n"
            f"• <b>Motor de descarga:</b> <code>{dl_status}</code>\n"
            f"• <b>Caché temporal (SSD):</b> <code>{self.download_music_dir}</code>\n"
            f"• <b>Biblioteca final (HDD):</b> <code>{self.library_music_dir}</code> ({win_dest})\n"
            f"• <b>Última comprobación:</b> <code>{last_sync}</code>\n"
            f"• <b>Intervalo programado:</b> cada {self.daemon_interval} min\n\n"
            "<i>Envía <code>/sync</code> para lotes, <code>/descubrir</code> para recomendaciones, <code>/novedades</code> para estrenos o <code>/add</code> para demanda.</i>"
        )

    def on_limit_command(self, raw_args: str) -> str:
        args_str = raw_args.strip()
        if not args_str:
            curr = self.state_tracker.get_limit(default_limit=self.default_max_albums)
            limit_str = f"<b>{curr}</b> álbumes por lote" if curr > 0 else "<b>Sin límite</b> (todos los álbumes)"
            return (
                "⚙️ <b>Configuración de Límite por Lote</b>\n\n"
                f"• <b>Límite actual:</b> {limit_str}\n"
                "• <b>Prioridad:</b> Los álbumes con mayor cantidad de temas faltantes van primero.\n\n"
                "Para cambiar el límite predeterminado, envía:\n"
                "<code>/limit [cantidad]</code> (ej. <code>/limit 20</code>, <code>/limit 30</code> o <code>/limit 0</code> para sin límite).\n\n"
                "<i>También puedes pedir un lote específico directamente con <code>/sync [Nombre] lote 2</code>.</i>"
            )

        try:
            val = int(args_str.split()[0])
        except ValueError:
            return "❌ Por favor indica un número entero válido. Ejemplo: <code>/limit 20</code> (o <code>/limit 0</code> para sin límite)."

        if val < 0:
            return "❌ La cantidad no puede ser negativa. Usa <code>/limit 20</code> o <code>/limit 0</code> para sin límite."

        self.state_tracker.set_limit(val)
        if val == 0:
            return (
                "⚙️ <b>Límite actualizado:</b> Ahora se entregarán <b>todos</b> los álbumes en un solo lote (sin límite)."
            )
        return (
            f"⚙️ <b>Límite actualizado:</b> Ahora cada sincronización entregará los <b>{val}</b> álbumes prioritarios por lote.\n\n"
            f"💡 <i>Los álbumes están ordenados de mayor a menor cantidad de canciones faltantes. Puedes pedir los siguientes enviando <code>/sync [Playlist] lote 2</code>.</i>"
        )

    def on_discard_command(self, chat_id: str, thread_id: Optional[str], raw_args: str):
        if not self.listener:
            return

        clean_args = (raw_args or "").strip()
        if not clean_args or clean_args.lower() in ("lista", "list", "ver"):
            ignored = self.state_tracker.get_ignored()
            if not ignored:
                self.listener.send_reply(
                    chat_id,
                    "🗑 <b>Gestión de Temas Descartados / Ignorados</b>\n\n"
                    "<i>No hay ningún tema ni álbum en la lista de descartados.</i>\n\n"
                    "<b>¿Cómo descartar?</b>\n"
                    "• <code>/descartar 1</code> (descarta el elemento #1 del último lote de <code>/sync</code>)\n"
                    "• <code>/descartar 1, 3</code> (descarta múltiples elementos del lote)\n"
                    "• <code>/descartar [nombre de canción o álbum]</code>\n"
                    "• <code>/descartar limpiar</code> (vacía la lista completa)\n"
                    "• <code>/restaurar [nombre o número]</code> (vuelve a permitirlo)",
                    thread_id,
                )
                return

            items_text = []
            for i, item in enumerate(ignored, 1):
                art = f" ({html.escape(item['artist'])})" if item.get("artist") else ""
                date_str = item.get("added_at", "").split("T")[0]
                items_text.append(f"• <b>#{i}</b>: <b>{html.escape(item['name'])}</b>{art} <i>({date_str})</i>")

            body = "\n".join(items_text[:40])
            extra = f"\n<i>...y {len(ignored) - 40} más.</i>" if len(ignored) > 40 else ""
            self.listener.send_reply(
                chat_id,
                f"🗑 <b>Temas y Álbumes Descartados ({len(ignored)}):</b>\n"
                "<i>Estos elementos se omiten permanentemente de futuros /sync y descargas pendientes.</i>\n\n"
                f"{body}{extra}\n\n"
                "💡 <i>Para restaurar uno: <code>/restaurar [número o nombre]</code>.</i>\n"
                "💡 <i>Para vaciar la lista: <code>/descartar limpiar</code>.</i>",
                thread_id,
            )
            return

        if clean_args.lower() in ("limpiar", "clear", "reset", "borrar"):
            cnt = self.state_tracker.clear_ignored()
            self.listener.send_reply(
                chat_id,
                f"🗑 <b>Lista de descartados vaciada:</b> Se restauraron <b>{cnt}</b> elementos. Volverán a considerarse en las siguientes sincronizaciones.",
                thread_id,
            )
            return

        parts = re.split(r"[\s,]+", clean_args)
        indices = set()
        is_index_mode = True
        for part in parts:
            if not part:
                continue
            if "-" in part and part.replace("-", "").isdigit():
                start_s, end_s = part.split("-", 1)
                if start_s.isdigit() and end_s.isdigit():
                    s, e = int(start_s), int(end_s)
                    for x in range(min(s, e), max(s, e) + 1):
                        indices.add(x)
                else:
                    is_index_mode = False
                    break
            elif part.isdigit():
                indices.add(int(part))
            else:
                is_index_mode = False
                break

        last_batch = self.state_tracker.get_last_batch()
        if is_index_mode and indices and last_batch and (last_batch.get("albums") or last_batch.get("tracks")):
            batch_albums = list(last_batch.get("albums", []))
            batch_tracks = list(last_batch.get("tracks", []))

            discarded_names = []
            remaining_albums = []
            remaining_tracks = []

            for idx, a in enumerate(batch_albums, 1):
                if idx in indices:
                    alb_name = a["album_name"]
                    art_name = a.get("artist_name", "")
                    self.state_tracker.add_ignored(alb_name, art_name)
                    for t in a.get("tracks", []):
                        self.state_tracker.add_ignored(t, art_name)
                    discarded_names.append(f"💿 <b>{html.escape(alb_name)}</b> ({html.escape(art_name)})")
                else:
                    remaining_albums.append(a)

            for trk_idx, t in enumerate(batch_tracks, len(batch_albums) + 1):
                if trk_idx in indices:
                    title = t["title"]
                    art_name = t.get("artist", "")
                    self.state_tracker.add_ignored(title, art_name)
                    discarded_names.append(f"🎵 <b>{html.escape(title)}</b> ({html.escape(art_name)})")
                else:
                    remaining_tracks.append(t)

            if discarded_names:
                last_batch["albums"] = remaining_albums
                last_batch["tracks"] = remaining_tracks
                self.state_tracker.save()

                try:
                    discarded_titles_set = set()
                    for a in batch_albums:
                        if a not in remaining_albums:
                            discarded_titles_set.add(a.get("album_name", ""))
                            discarded_titles_set.update(a.get("tracks", []))
                    for t in batch_tracks:
                        if t not in remaining_tracks:
                            discarded_titles_set.add(t.get("title", ""))
                    if discarded_titles_set:
                        for pls in self.navidrome.get_playlists():
                            p_name = pls.get("name", "")
                            if not self.target_names or p_name in self.target_names or any(tn.lower() in p_name.lower() for tn in self.target_names):
                                pid = pls.get("id")
                                p_det = self.navidrome.get_playlist_details(pid)
                                p_comm = p_det.get("comment", "")
                                if p_comm and ("Tracks not found" in p_comm or "Tracks not matched" in p_comm):
                                    updated_comm = TrackParser.rebuild_comment_without_tracks(p_comm, discarded_titles_set)
                                    if updated_comm != p_comm:
                                        self.navidrome.update_playlist_comment(pid, updated_comm)
                                        logger.info("Updated playlist '%s' comment after /descartar", p_name)
                except Exception as ce:
                    logger.warning("Error updating playlist comments on discard: %s", ce)

                disc_list_str = "\n".join(f"• {x}" for x in discarded_names)
                rem_count = len(remaining_albums) + len(remaining_tracks)
                self.listener.send_reply(
                    chat_id,
                    f"🗑 <b>Elementos Descartados del Lote ({len(discarded_names)}):</b>\n"
                    f"{disc_list_str}\n\n"
                    f"✅ <i>Añadidos a la lista negra permanente y removidos de los pendientes. Quedan <b>{rem_count}</b> elementos en el lote actual.</i>",
                    thread_id,
                )
                return

        self.state_tracker.add_ignored(clean_args)
        if last_batch:
            rem_albs = [a for a in last_batch.get("albums", []) if not self.state_tracker.is_ignored(a["album_name"], a.get("artist_name"))]
            rem_trks = [t for t in last_batch.get("tracks", []) if not self.state_tracker.is_ignored(t["title"], t.get("artist"))]
            last_batch["albums"] = rem_albs
            last_batch["tracks"] = rem_trks
            self.state_tracker.save()

        try:
            for pls in self.navidrome.get_playlists():
                p_name = pls.get("name", "")
                if not self.target_names or p_name in self.target_names or any(tn.lower() in p_name.lower() for tn in self.target_names):
                    pid = pls.get("id")
                    p_det = self.navidrome.get_playlist_details(pid)
                    p_comm = p_det.get("comment", "")
                    if p_comm and ("Tracks not found" in p_comm or "Tracks not matched" in p_comm):
                        updated_comm = TrackParser.rebuild_comment_without_tracks(p_comm, {clean_args})
                        if updated_comm != p_comm:
                            self.navidrome.update_playlist_comment(pid, updated_comm)
                            logger.info("Updated playlist '%s' comment after /descartar '%s'", p_name, clean_args)
        except Exception as ce:
            logger.warning("Error updating playlist comments on discard: %s", ce)

        self.listener.send_reply(
            chat_id,
            f"🗑 <b>Elemento Descartado con Éxito:</b>\n"
            f"• <b>{html.escape(clean_args)}</b>\n\n"
            "✅ <i>Añadido a la lista negra permanente y eliminado de la descripción en Navidrome. Se omitirá de todas las futuras sincronizaciones.</i>",
            thread_id,
        )

    def on_restore_command(self, chat_id: str, thread_id: Optional[str], raw_args: str):
        if not self.listener:
            return

        clean_args = (raw_args or "").strip()
        if not clean_args:
            self.listener.send_reply(
                chat_id,
                "ℹ️ <b>Uso de /restaurar:</b>\n"
                "• <code>/restaurar [número]</code> (ej. <code>/restaurar 1</code>)\n"
                "• <code>/restaurar [nombre]</code> (ej. <code>/restaurar Tokyo Flash</code>)\n\n"
                "<i>Consulta la lista de temas descartados con <code>/descartar lista</code>.</i>",
                thread_id,
            )
            return

        success = self.state_tracker.remove_ignored(clean_args)
        if success:
            self.listener.send_reply(
                chat_id,
                f"✅ <b>Elemento restaurado con éxito:</b>\n"
                f"• <code>{html.escape(clean_args)}</code>\n\n"
                "<i>Ya no está en la lista de ignorados y volverá a ser analizado en el próximo /sync.</i>",
                thread_id,
            )
        else:
            self.listener.send_reply(
                chat_id,
                f"⚠️ No se encontró ningún elemento en la lista de descartados que coincida con: <code>{html.escape(clean_args)}</code>.\n"
                "<i>Revisa los elementos existentes con <code>/descartar lista</code>.</i>",
                thread_id,
            )

    def on_incompletos_command(self, chat_id: str, thread_id: Optional[str]):
        if not self.listener:
            return
        from ..modules.auditor import IncompleteAlbumAuditor
        auditor = IncompleteAlbumAuditor(
            navidrome=self.navidrome,
            downloader=getattr(self.download_queue, "downloader", None),
            state_tracker=self.state_tracker,
            library_dir=Path(self.library_music_dir) if self.library_music_dir else None,
            resolver=getattr(self.download_queue, "resolver", None),
        )
        incomplete = auditor.find_incomplete_albums(limit=30)
        if not incomplete:
            self.listener.send_reply(
                chat_id,
                "✅ <b>Auditoría de Biblioteca:</b> ¡Todos los álbumes de tu colección están completos! No se detectaron huecos de pistas.",
                thread_id,
            )
            return

        lines = [
            f"🔍 <b>Álbumes Incompletos Detectados ({len(incomplete)}):</b>\n",
        ]
        for idx, item in enumerate(incomplete[:20], 1):
            alb = html.escape(item["album_name"])
            art = html.escape(item["artist_name"])
            pres = item["present_count"]
            exp = item.get("expected_count", "?")
            lines.append(f"• <b>#{idx:02d}</b>: <b>{art}</b> — <i>{alb}</i> ({pres}/{exp} pistas)")

        if len(incomplete) > 20:
            lines.append(f"\n<i>... y {len(incomplete) - 20} álbumes más.</i>")

        lines.append("\n💡 <i>Envía <code>/noche</code> para programar o iniciar la descarga nocturna de pistas faltantes.</i>")
        self.listener.send_reply(chat_id, "\n".join(lines), thread_id)

    def on_unificar_command(self, chat_id: str, thread_id: Optional[str]):
        if not self.listener:
            return
        consolidator = FolderConsolidator(
            library_dir=Path(self.library_music_dir) if self.library_music_dir else None,
            download_dir=Path(self.download_music_dir) if self.download_music_dir else None,
            navidrome=self.navidrome,
        )
        results = consolidator.consolidate_all(dry_run=False)
        if not results:
            self.listener.send_reply(
                chat_id,
                "✨ <b>Consolidación de Biblioteca:</b> No se encontraron carpetas duplicadas ni fragmentadas. Tu biblioteca está limpia y unificada.",
                thread_id,
            )
            return

        lines = [
            f"📁 <b>Consolidación de Carpetas Finalizada ({len(results)} álbumes unificados):</b>\n",
        ]
        for idx, r in enumerate(results[:15], 1):
            alb = html.escape(r["album"])
            cnt = r["moved_files"]
            lines.append(f"• <b>#{idx:02d}</b>: <b>{alb}</b> $\\rightarrow$ {cnt} archivo(s) reubicados.")

        if len(results) > 15:
            lines.append(f"\n<i>... y {len(results) - 15} álbumes más.</i>")

        lines.append("\n🔄 <i>Escaneo de Navidrome iniciado automáticamente para reflejar los cambios.</i>")
        self.listener.send_reply(chat_id, "\n".join(lines), thread_id)

    def on_noche_command(self, chat_id: str, thread_id: Optional[str]):
        if not self.listener:
            return
        mgr = NightlyDownloadManager(
            navidrome=self.navidrome,
            download_queue=self.download_queue,
            state_tracker=self.state_tracker,
            library_dir=Path(self.library_music_dir) if self.library_music_dir else None,
            download_dir=Path(self.download_music_dir) if self.download_music_dir else None,
            telegram_sender=getattr(self.listener, "telegram_sender", None),
            descargas_thread_id=thread_id,
        )
        summary = mgr.run_maintenance(max_albums=15, dry_run=False)
        self.listener.send_reply(
            chat_id,
            f"🌙 <b>Mantenimiento Nocturno Ejecutado:</b>\n\n"
            f"• <b>Carpetas unificadas:</b> {summary['consolidated_folders']}\n"
            f"• <b>Incompletos detectados:</b> {summary['incomplete_detected']}\n"
            f"• <b>Encolados para descarga:</b> {summary['enqueued_for_download']}\n"
            f"• <b>Omitidos (ya completos / bloqueados):</b> {summary['skipped_already_complete']}\n\n"
            f"📥 <i>Las descargas se están procesando secuencialmente en la cola.</i>",
            thread_id,
        )

    def on_fallidos_command(self, chat_id: str, thread_id: Optional[str]):
        if not self.listener:
            return
        failed = self.state_tracker.get_failed_items()
        if not failed:
            self.listener.send_reply(
                chat_id,
                "🛡️ <b>Registro de Descargas Fallidas / Bloqueadas:</b>\n\n"
                "<i>No hay ninguna pista ni álbum registrado como fallido o bloqueado.</i>",
                thread_id,
            )
            return

        lines = [
            f"🛡️ <b>Registro de Pistas/Álbumes Bloqueados ({len(failed)}):</b>\n",
        ]
        for idx, itm in enumerate(failed[:20], 1):
            nm = html.escape(itm.get("name", ""))
            art = f" ({html.escape(itm['artist'])})" if itm.get("artist") else ""
            reason = html.escape(itm.get("last_reason", "Error")[:40])
            fc = itm.get("failure_count", 1)
            perm = "⛔ Permanente" if itm.get("is_permanent") else f"⚠️ {fc} fallos"
            lines.append(f"• <b>#{idx:02d}</b>: <b>{nm}</b>{art}\n  └ <i>{perm} — {reason}</i>")

        if len(failed) > 20:
            lines.append(f"\n<i>... y {len(failed) - 20} más.</i>")

        lines.append("\n💡 <i>Usa <code>/reintentar_fallidos</code> para limpiar la lista y permitir nuevos intentos.</i>")
        self.listener.send_reply(chat_id, "\n".join(lines), thread_id)

    def on_reintentar_fallidos_command(self, chat_id: str, thread_id: Optional[str]):
        if not self.listener:
            return
        count = self.state_tracker.clear_failed_items()
        self.listener.send_reply(
            chat_id,
            f"🔄 <b>Registro de Fallos Limpiado:</b>\n\n"
            f"• Se eliminaron <b>{count}</b> registros de fallos/bloqueos.\n"
            f"✅ <i>Ahora el sincronizador y el auditor nocturno podrán volver a intentar su descarga.</i>",
            thread_id,
        )

    def on_completar_command(self, chat_id: str, thread_id: Optional[str], args: str = ""):
        if not self.listener:
            return

        clean_args = (args or "").strip()
        parts = clean_args.split()
        count = 10
        offset = 0
        auto_go = False

        for p in parts:
            if p.isdigit():
                count = max(1, min(50, int(p)))
            elif p.lower() in ("ahora", "go", "auto", "directo", "ya"):
                auto_go = True
            elif p.lower().startswith("offset:"):
                try:
                    offset = int(p.split(":", 1)[1])
                except Exception:
                    pass

        auditor = IncompleteAlbumAuditor(
            navidrome=self.navidrome,
            downloader=getattr(self.download_queue, "downloader", None),
            state_tracker=self.state_tracker,
            library_dir=Path(self.library_music_dir) if self.library_music_dir else None,
            resolver=getattr(self.download_queue, "resolver", None),
        )
        candidates = auditor.find_incomplete_albums(limit=count, offset=offset)
        if not candidates:
            self.listener.send_reply(
                chat_id,
                "✨ <b>¡Excelente noticia!</b> No se encontraron álbumes incompletos pendientes de descarga.",
                thread_id,
            )
            return

        self.state_tracker.save_incomplete_batch(candidates)

        if auto_go:
            resolved_albums = [c["resolved_album"] for c in candidates if "resolved_album" in c]
            job = DownloadJob(
                job_id=f"diurno_{int(time.time()*1000)}",
                chat_id=chat_id,
                thread_id=thread_id,
                title=f"⚡ Lote Diurno: {len(resolved_albums)} álbumes incompletos",
                albums=resolved_albums,
                tracks=[],
                source_desc="Descarga Diurna de Incompletos",
            )
            self.download_queue.enqueue(job)
            self.listener.send_reply(
                chat_id,
                f"🚀 <b>Lote Diurno Iniciado ({len(resolved_albums)} álbumes):</b>\n"
                f"• Se encolaron para descarga secuencial.\n"
                f"• Puedes seguir el progreso en vivo en el tema de descargas.",
                thread_id,
            )
            return

        lines = [
            f"⚡ <b>Propuesta de Lote Diurno ({len(candidates)} álbumes incompletos):</b>\n",
        ]
        for idx, item in enumerate(candidates, 1):
            alb = html.escape(item["album_name"])
            art = html.escape(item["artist_name"])
            pres = item["present_count"]
            exp = item.get("expected_count", "?")
            lines.append(f"• <b>#{idx:02d}</b>: <b>{art}</b> — <i>{alb}</i> ({pres}/{exp} pistas)")

        lines.append("\n💡 <i>Presiona el botón para iniciar la descarga ahora, o usa <code>/completar [N] ahora</code> para omitir confirmación.</i>")

        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": f"▶️ Descargar este Lote ({len(candidates)} álbumes)", "callback_data": f"dl_inc_batch:{len(candidates)}"},
                ],
                [
                    {"text": "🎲 Siguiente Lote", "callback_data": f"next_inc_batch:{offset + count}"},
                    {"text": "❌ Cancelar", "callback_data": "cancel_inc_batch"},
                ],
            ]
        }
        self.listener.send_reply(chat_id, "\n".join(lines), thread_id, reply_markup=reply_markup)

    def on_download_incomplete_batch_callback(self, chat_id: str, thread_id: Optional[str], msg_id: int, count_str: str):
        if not self.listener:
            return
        candidates = self.state_tracker.get_incomplete_batch()
        if not candidates:
            count = int(count_str) if count_str.isdigit() else 10
            auditor = IncompleteAlbumAuditor(
                navidrome=self.navidrome,
                downloader=getattr(self.download_queue, "downloader", None),
                state_tracker=self.state_tracker,
                library_dir=Path(self.library_music_dir) if self.library_music_dir else None,
                resolver=getattr(self.download_queue, "resolver", None),
            )
            candidates = auditor.find_incomplete_albums(limit=count)

        if not candidates:
            self.listener.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text="⚠️ <i>No hay álbumes en el lote actual para descargar.</i>",
                reply_markup=None,
            )
            return

        resolved_albums = []
        for c in candidates:
            res_a = c.get("resolved_album")
            if isinstance(res_a, dict):
                resolved_albums.append(ResolvedAlbum(**res_a))
            elif res_a:
                resolved_albums.append(res_a)

        job = DownloadJob(
            job_id=f"diurno_{int(time.time()*1000)}",
            chat_id=chat_id,
            thread_id=thread_id,
            title=f"⚡ Lote Diurno: {len(resolved_albums)} álbumes incompletos",
            albums=resolved_albums,
            tracks=[],
            source_desc="Descarga Diurna de Incompletos",
        )
        self.download_queue.enqueue(job)
        self.listener.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text=f"✅ <b>Lote de {len(resolved_albums)} álbumes incompletos encolado con éxito.</b>\n"
                 f"📥 <i>Las descargas se están procesando secuencialmente en el tema de descargas.</i>",
            reply_markup=None,
        )


