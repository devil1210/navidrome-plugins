#!/usr/bin/env python3
"""
Job processor for sequential download jobs (albums, tracks, playlists).
"""

import gc
import logging
import re
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..models import DownloadJob, ResolvedAlbum, ResolvedTrack
from ..syncer import titles_match
from .batch_view import build_batch_progress_rich_html
from .streamer import AlbumProgressStreamer

logger = logging.getLogger("listenbrainz_notifier")


def process_download_job(queue: Any, job: DownloadJob):
    """Executes a single DownloadJob sequentially, tracking progress and updating batch status."""
    chat_id = job.chat_id
    descargas_thread_id = (
        queue._resolve_topic_thread_id(
            chat_id, "descargas", "📥 Descargas", icon_color=7322096, caller_thread_id=job.thread_id
        )
        or job.thread_id
    )
    anadidos_thread_id = (
        queue._resolve_topic_thread_id(
            chat_id, "anadidos", "✨ Añadidos", icon_color=9367192, caller_thread_id=job.thread_id
        )
        or descargas_thread_id
        or job.thread_id
    )

    total_albums = len(job.albums)
    total_tracks = len(job.tracks)
    is_single_release = (total_albums == 1 and total_tracks == 0) or (total_albums == 0 and total_tracks == 1)

    batch_items: List[Dict[str, Any]] = []
    if not is_single_release:
        for b_idx, a in enumerate(job.albums, 1):
            itype = "Sencillo" if getattr(a, "is_single", False) else "Álbum"
            batch_items.append({"index": b_idx, "type": itype, "name": f"{a.artist_name} — {a.album_name}", "status": "pending"})
        for t_idx, trk in enumerate(job.tracks, len(job.albums) + 1):
            batch_items.append({"index": t_idx, "type": "Pista", "name": f"{trk.artist} — {trk.title}", "status": "pending"})

    batch_msg_id = None
    if not is_single_release and len(batch_items) > 1:
        init_html = queue._build_batch_progress_rich_html(
            title=job.title,
            items=batch_items,
            completed_count=0,
            total_count=len(batch_items),
            current_item_name=batch_items[0]["name"] if batch_items else "",
            dest_folder=queue.win_dest,
            finished=False,
        )
        if job.status_msg_id and queue.listener:
            queue.listener.edit_message_text(
                chat_id=chat_id,
                message_id=job.status_msg_id,
                text="📦 <b>Iniciando descarga por lote...</b>",
                rich_message={"html": init_html},
            )
            batch_msg_id = job.status_msg_id
        elif queue.listener:
            batch_msg_id = queue.listener.send_reply(
                chat_id,
                "📦 <b>Iniciando descarga por lote...</b>",
                descargas_thread_id,
                rich_message={"html": init_html},
            )

    def _update_batch_progress(curr_name: str = "", finished: bool = False, reply_markup: Optional[Dict[str, Any]] = None):
        if not batch_msg_id or not queue.listener:
            return
        done_cnt = sum(1 for it in batch_items if it.get("status") in ("done", "skipped", "failed"))
        b_html = queue._build_batch_progress_rich_html(
            title=job.title,
            items=batch_items,
            completed_count=done_cnt,
            total_count=len(batch_items),
            current_item_name=curr_name,
            dest_folder=queue.win_dest,
            finished=finished,
        )
        failed_in_batch = sum(1 for it in batch_items if it.get("status") == "failed")
        if finished:
            hdr_text = f"⚠️ <b>¡Lote finalizado con {failed_in_batch} error{'es' if failed_in_batch > 1 else ''}!</b>" if failed_in_batch > 0 else "✅ <b>¡Lote finalizado con éxito!</b>"
        else:
            hdr_text = "📦 <b>Progreso de Descarga por Lote</b>"

        try:
            queue.listener.edit_message_text(
                chat_id=chat_id,
                message_id=batch_msg_id,
                text=hdr_text,
                reply_markup=reply_markup,
                rich_message={"html": b_html},
            )
        except Exception as be:
            logger.debug("Error updating batch progress card: %s", be)

    if not is_single_release and not batch_msg_id:
        if len(queue.downloader.cookies_list) > 1:
            cookies_status = f"🍪 <b>Rotación activa:</b> {len(queue.downloader.cookies_list)} cuentas alternándose."
        elif len(queue.downloader.cookies_list) == 1:
            cookies_status = f"🍪 <b>Cookies activas:</b> 1 cuenta conectada (<code>{queue.downloader.cookies_list[0].name}</code>)."
        else:
            cookies_status = "⚡ <i>Descarga estándar activa (aceleración multi-hilo y JS V8).</i>"

        queue._send_reply(
            chat_id,
            f"⬇️ <b>Iniciando descarga directa al servidor</b>\n\n"
            f"• <b>Tarea:</b> {job.title}\n"
            f"• <b>Álbumes / Sencillos:</b> {total_albums}\n"
            f"• <b>Canciones sueltas:</b> {total_tracks}\n"
            f"• <b>Destino:</b> <code>{queue.win_dest}</code>\n"
            f"• <b>Formato:</b> M4A HD (carátulas <code>cover.jpg</code> e incrustación nativa)\n"
            f"• {cookies_status}",
            descargas_thread_id,
        )

    ok_count = 0
    skipped_count = 0
    failed_count = 0
    failed_albums: List[ResolvedAlbum] = []
    failed_tracks: List[ResolvedTrack] = []

    for idx, album in enumerate(job.albums, 1):
        while queue.paused and not queue.cancel_requested and queue.running:
            time.sleep(1.0)

        if queue.cancel_requested:
            logger.info("Cancelación de descarga solicitada para '%s'. Abortando cola.", job.title)
            queue._send_reply(
                chat_id,
                f"🛑 <b>Descarga cancelada a petición del usuario.</b>\n"
                f"• <b>Tarea:</b> {job.title}\n"
                f"• <b>Álbumes procesados antes de cancelar:</b> {idx - 1}/{total_albums}",
                descargas_thread_id,
            )
            break
        if not queue.running:
            break

        with queue.lock:
            queue.active_details = f"[{idx}/{total_albums}] {album.artist_name} - {album.album_name}"

        if not is_single_release and idx <= len(batch_items):
            batch_items[idx - 1]["status"] = "downloading"
            _update_batch_progress(batch_items[idx - 1]["name"])

        item_type = "Sencillo" if getattr(album, "is_single", False) else "Álbum"
        folder_name = queue.downloader.sanitize_name(album.album_name)
        target_folder = queue.downloader.download_dir / folder_name

        # 1. Check Navidrome library
        existing_album = None
        if queue.navidrome:
            try:
                existing_album = queue.navidrome.find_album_match(album.album_name, album.artist_name, strict_artist=True)
            except Exception as ex:
                logger.warning("Error comprobando álbum en Navidrome: %s", ex)

        disk_audio_files = []
        if target_folder.is_dir():
            disk_audio_files = [
                f for f in target_folder.iterdir()
                if f.is_file() and f.suffix.lower() in ('.m4a', '.mp3', '.opus', '.webm', '.flac')
            ]

        if existing_album:
            nav_tracks = []
            try:
                nav_tracks = queue.navidrome.get_album_tracks(existing_album["id"])
            except Exception as ex:
                logger.warning("Error obteniendo pistas de Navidrome para álbum '%s': %s", album.album_name, ex)

            official_tracks = []
            if not getattr(album, "is_single", False):
                try:
                    official_tracks = queue.downloader.get_album_tracklist(album)
                except Exception as ex:
                    logger.warning("Error obteniendo tracklist oficial para '%s': %s", album.album_name, ex)
            if not official_tracks:
                official_tracks = getattr(album, "tracks", []) or []

            if official_tracks:
                nav_titles = [t.get("title", "") for t in nav_tracks]
                disk_names = [f.stem for f in disk_audio_files]

                missing_indices = []
                for t_idx, trk in enumerate(official_tracks, 1):
                    track_name = trk.get("title", "") if isinstance(trk, dict) else str(trk)
                    idx_val = trk.get("index", t_idx) if isinstance(trk, dict) else t_idx
                    found_in_nav = any(titles_match(track_name, nt) for nt in nav_titles)
                    found_on_disk = any(titles_match(track_name, dn) for dn in disk_names)
                    if not found_in_nav and not found_on_disk:
                        missing_indices.append(idx_val)

                present_count = len(official_tracks) - len(missing_indices)
                if len(missing_indices) == 0:
                    logger.info(
                        "El %s '%s' ya está completo en Navidrome/disco (%d/%d pistas). Omitiendo.",
                        item_type.lower(), album.album_name, present_count, len(official_tracks)
                    )
                    skipped_count += 1
                    if getattr(queue, "state_tracker", None):
                        queue.state_tracker.mark_audited_complete(album.album_name, album.artist_name)
                    resolved_titles = [trk.get("title", "") if isinstance(trk, dict) else str(trk) for trk in official_tracks]
                    queue._clean_playlist_for_resolved_items(job, resolved_titles=resolved_titles, album_name=album.album_name)
                    if not is_single_release and idx <= len(batch_items):
                        batch_items[idx - 1]["status"] = "skipped"
                        _update_batch_progress(batch_items[idx - 1]["name"])
                        queue._send_reply(
                            chat_id,
                            f"💿 <b>[{idx}/{total_albums}] {item_type} ya completo en tu biblioteca:</b>\n"
                            f"• <b>{album.artist_name}</b> — <i>{album.album_name}</i>\n"
                            f"✨ <i>Verificadas {present_count}/{len(official_tracks)} pistas. Omitida la re-descarga.</i>",
                            descargas_thread_id,
                        )
                    elif is_single_release and job.status_msg_id and queue.listener:
                        queue.listener.edit_message_text(
                            chat_id,
                            job.status_msg_id,
                            f"💿 <b>{album.artist_name} — {album.album_name}</b> ya está completo en tu biblioteca ({present_count} pistas).",
                        )
                    continue
                elif present_count > 0:
                    logger.info(
                        "El %s '%s' está parcial en Navidrome (%d/%d pistas). Descargando %d pistas faltantes...",
                        item_type.lower(), album.album_name, present_count, len(official_tracks), len(missing_indices)
                    )
                    missing_preview = ", ".join(f"#{i}" for i in missing_indices[:10])
                    if len(missing_indices) > 10:
                        missing_preview += f" y {len(missing_indices) - 10} más"

                    queue._send_reply(
                        chat_id,
                        f"💿 <b>[{idx}/{total_albums}] {item_type} parcial en biblioteca:</b>\n"
                        f"• <b>{album.artist_name}</b> — <i>{album.album_name}</i>\n"
                        f"✨ <i>Detectadas {present_count}/{len(official_tracks)} pistas en Navidrome.</i>\n"
                        f"📥 <b>Descargando {len(missing_indices)} pistas faltantes:</b>\n"
                        f"• <i>{missing_preview}</i>\n"
                        f"• <b>Destino:</b> <code>{queue.win_dest}\\{folder_name}</code>",
                        descargas_thread_id,
                    )

                    streamer = AlbumProgressStreamer(
                        queue=queue,
                        chat_id=chat_id,
                        thread_id=descargas_thread_id,
                        album=album,
                        tracks=official_tracks,
                        dest_folder=f"{queue.win_dest}\\{folder_name}",
                        missing_indices=set(missing_indices),
                        initial_msg_id=(job.status_msg_id if is_single_release else None),
                    )
                    streamer.start()
                    queue.downloader.progress_callback = streamer.handle_progress
                    try:
                        ok, err = queue.downloader.download_album_tracks(album, missing_indices)
                    finally:
                        queue.downloader.progress_callback = None
                        streamer.finish(success=ok, final_note=str(err) if not ok else "")

                    if ok:
                        ok_count += 1
                        queue.consecutive_block_errors = 0
                        if getattr(queue, "state_tracker", None):
                            queue.state_tracker.mark_audited_complete(album.album_name, album.artist_name)
                        actual_folder = Path(err) if (err and Path(err).is_dir()) else target_folder
                        queue._send_album_rich_notification(chat_id, anadidos_thread_id, album, actual_folder)
                        if not is_single_release and idx <= len(batch_items):
                            batch_items[idx - 1]["status"] = "done"
                            _update_batch_progress(batch_items[idx - 1]["name"])
                        queue._send_reply(
                            chat_id,
                            f"📥 <b>[{idx}/{total_albums}] Pistas faltantes descargadas con éxito:</b>\n"
                            f"• <b>{album.artist_name}</b> — <i>{album.album_name}</i>\n"
                            f"• Descargadas <b>{len(missing_indices)}</b> pistas faltantes.",
                            descargas_thread_id,
                        )
                    else:
                        failed_count += 1
                        failed_albums.append(album)
                        if getattr(queue, "state_tracker", None):
                            queue.state_tracker.record_failed_item(
                                name=album.album_name,
                                artist=album.artist_name,
                                url=album.url,
                                reason=str(err),
                            )
                        err_str = str(err).lower()
                        if any(w in err_str for w in ["bloqueo temporal", "bot", "sign in", "confirm you’re not a bot", "video unavailable", "rate-limited"]):
                            queue.consecutive_block_errors += 1
                        else:
                            queue.consecutive_block_errors = 0

                        logger.error("Error descargando pistas faltantes de '%s': %s", album.album_name, err)
                        if not is_single_release and idx <= len(batch_items):
                            batch_items[idx - 1]["status"] = "failed"
                            _update_batch_progress(batch_items[idx - 1]["name"])
                            queue._send_reply(
                                chat_id,
                                f"⚠️ <b>[{idx}/{total_albums}] Error descargando pistas faltantes:</b>\n"
                                f"• <b>{album.artist_name}</b> — <i>{album.album_name}</i>\n"
                                f"• <i>{err}</i>",
                                descargas_thread_id,
                            )

                        if queue.consecutive_block_errors >= 3:
                            logger.warning("Circuit breaker activado: 3 fallos consecutivos por posible bloqueo de YouTube. Pausando cola.")
                            queue.paused = True
                            qsize = queue.queue.qsize() + (total_albums - idx)
                            queue._send_reply(
                                chat_id,
                                f"🛑 <b>Cola pausada por protección (Circuit Breaker):</b>\n"
                                f"• Se detectaron <b>3 fallos consecutivos</b> por posible bloqueo o restricción de YouTube.\n"
                                f"• Se <b>pausó la cola automáticamente</b> para no quemar tus cuentas ni prolongar bloqueos ({qsize} tareas en espera retenidas).\n"
                                f"💡 <i>Espera unos minutos o renueva cookies, luego usa <code>/reanudar</code> para continuar.</i>",
                                descargas_thread_id,
                            )
                            break
                    continue
            else:
                nav_cnt = len(nav_tracks)
                is_single = getattr(album, "is_single", False) or is_single_release
                track_numbers = [t.get("track") for t in nav_tracks if t.get("track") and isinstance(t.get("track"), int)]
                max_trkn = max(track_numbers) if track_numbers else nav_cnt
                has_missing_gaps = False
                if track_numbers and len(track_numbers) >= 2:
                    expected_set = set(range(min(track_numbers), max(track_numbers) + 1))
                    if len(expected_set) > len(track_numbers):
                        has_missing_gaps = True

                if is_single or (nav_cnt >= 8 and max_trkn <= nav_cnt and not has_missing_gaps):
                    logger.info("El %s '%s' de '%s' ya existe completo en Navidrome (ID: %s, %d pistas). Omitiendo.", item_type.lower(), album.album_name, album.artist_name, existing_album.get("id"), nav_cnt)
                    skipped_count += 1
                    if getattr(queue, "state_tracker", None):
                        queue.state_tracker.mark_audited_complete(album.album_name, album.artist_name)
                    resolved_titles = [trk.get("title", "") if isinstance(trk, dict) else str(trk) for trk in getattr(album, "tracks", [])]
                    queue._clean_playlist_for_resolved_items(job, resolved_titles=resolved_titles, album_name=album.album_name)
                    if not is_single_release and idx <= len(batch_items):
                        batch_items[idx - 1]["status"] = "skipped"
                        _update_batch_progress(batch_items[idx - 1]["name"])
                        queue._send_reply(
                            chat_id,
                            f"💿 <b>[{idx}/{total_albums}] {item_type} ya en tu biblioteca:</b>\n"
                            f"• <b>{album.artist_name}</b> — <i>{album.album_name}</i>\n"
                            f"✨ <i>Ya existe en tu colección de Navidrome ({nav_cnt} pistas). Se omitió la re-descarga.</i>",
                            descargas_thread_id,
                        )
                    elif is_single_release and job.status_msg_id and queue.listener:
                        queue.listener.edit_message_text(
                            chat_id,
                            job.status_msg_id,
                            f"💿 <b>{album.artist_name} — {album.album_name}</b> ya existe en tu colección de Navidrome ({nav_cnt} pistas).",
                        )
                    continue
                else:
                    logger.info("El álbum '%s' tiene %d pista(s) en Navidrome pero faltan pistas (max: %d, gaps: %s). Procediendo a descargar para completarlo.", album.album_name, nav_cnt, max_trkn, has_missing_gaps)

        # 2. Check local disk integrity
        is_single = getattr(album, "is_single", False) or is_single_release
        already_on_disk = False
        disk_path_str = f"{queue.win_dest}\\{folder_name}"
        track_count = 0
        if target_folder.is_dir():
            is_valid, track_count, status_msg = queue.downloader.verify_album_integrity(target_folder)
            if is_valid and (is_single or track_count >= 5):
                already_on_disk = True

        if not already_on_disk and queue.downloader.library_dir:
            clean_target_album = queue.downloader.sanitize_name(album.album_name).lower()
            clean_target_artist = queue.downloader.sanitize_name(album.artist_name)
            candidate_categories = ["General", "J-Music", "Compilaciones", "Soundtracks/Worldwide", "Soundtracks/J-Music", ""]
            for cat in candidate_categories:
                cat_dir = (queue.downloader.library_dir / cat) if cat else queue.downloader.library_dir
                if not cat_dir.is_dir():
                    continue
                artist_dir = cat_dir / clean_target_artist
                if artist_dir.is_dir():
                    for sub in artist_dir.iterdir():
                        if sub.is_dir():
                            sub_clean = re.sub(r"^\[\d{4}\]\s*-\s*", "", sub.name).strip().lower()
                            if sub_clean == clean_target_album or sub.name.lower() == clean_target_album:
                                is_valid, track_count, status_msg = queue.downloader.verify_album_integrity(sub)
                                if is_valid and (is_single or track_count >= 5):
                                    already_on_disk = True
                                    cat_win = f"\\{cat}" if cat else ""
                                    disk_path_str = f"{queue.win_dest}{cat_win}\\{artist_dir.name}\\{sub.name}".replace("/", "\\")
                                    break
                if already_on_disk:
                    break

        if already_on_disk:
            logger.info("El %s '%s' ya existe e íntegro en disco (%d pistas). Omitiendo.", item_type.lower(), album.album_name, track_count)
            skipped_count += 1
            if queue.navidrome:
                try:
                    queue.navidrome.start_scan(full_scan=False)
                except Exception:
                    pass
            resolved_titles = [trk.get("title", "") if isinstance(trk, dict) else str(trk) for trk in getattr(album, "tracks", [])]
            queue._clean_playlist_for_resolved_items(job, resolved_titles=resolved_titles, album_name=album.album_name)
            if not is_single_release and idx <= len(batch_items):
                batch_items[idx - 1]["status"] = "skipped"
                _update_batch_progress(batch_items[idx - 1]["name"])
                queue._send_reply(
                    chat_id,
                    f"📁 <b>[{idx}/{total_albums}] {item_type} verificado en disco:</b>\n"
                    f"• <b>{album.artist_name}</b> — <i>{album.album_name}</i>\n"
                    f"• <b>Carpeta:</b> <code>{disk_path_str}</code>\n"
                    f"✨ <i>Verificación de integridad exitosa ({track_count} pistas). Omitida la re-descarga.</i>",
                    descargas_thread_id,
                )
            elif is_single_release and job.status_msg_id and queue.listener:
                queue.listener.edit_message_text(
                    chat_id,
                    job.status_msg_id,
                    f"📁 <b>{album.artist_name} — {album.album_name}</b> verificado e íntegro en disco ({track_count} pistas).",
                )
            continue
        elif target_folder.is_dir():
            logger.info("La carpeta del %s '%s' existe pero incompleta (%s). Completando con yt-dlp...", item_type.lower(), album.album_name, status_msg)

        # 3. Real full download
        logger.info("Downloading %s %d/%d: '%s' by '%s'", item_type.lower(), idx, total_albums, album.album_name, album.artist_name)
        if not is_single_release:
            queue._send_reply(
                chat_id,
                f"💿 <b>[{idx}/{total_albums}] Descargando {item_type.lower()}:</b>\n"
                f"• <b>{album.artist_name}</b> — <i>{album.album_name}</i>\n"
                f"• <b>Destino:</b> <code>{queue.win_dest}\\{folder_name}</code>",
                descargas_thread_id,
            )

        official_tracklist = []
        if not getattr(album, "is_single", False):
            try:
                official_tracklist = queue.downloader.get_album_tracklist(album)
            except Exception:
                official_tracklist = []
        if not official_tracklist:
            official_tracklist = getattr(album, "tracks", []) or []

        streamer = AlbumProgressStreamer(
            queue=queue,
            chat_id=chat_id,
            thread_id=descargas_thread_id,
            album=album,
            tracks=official_tracklist,
            dest_folder=f"{queue.win_dest}\\{folder_name}",
            initial_msg_id=(job.status_msg_id if is_single_release else None),
        )
        streamer.start()
        queue.downloader.progress_callback = streamer.handle_progress
        try:
            success, err = queue.downloader.download_album(album)
        finally:
            queue.downloader.progress_callback = None
            streamer.finish(success=success, final_note=str(err) if not success else "")

        if success:
            ok_count += 1
            queue.consecutive_block_errors = 0
            if getattr(queue, "state_tracker", None):
                queue.state_tracker.mark_audited_complete(album.album_name, album.artist_name)
            actual_folder = Path(err) if (err and Path(err).is_dir()) else target_folder
            queue._send_album_rich_notification(chat_id, anadidos_thread_id, album, actual_folder)
            if not is_single_release and idx <= len(batch_items):
                batch_items[idx - 1]["status"] = "done"
                _update_batch_progress(batch_items[idx - 1]["name"])
            queue._send_reply(
                chat_id,
                f"📥 <b>[{idx}/{total_albums}] {item_type} descargado con éxito:</b>\n"
                f"• <b>{album.artist_name}</b> — <i>{album.album_name}</i>",
                descargas_thread_id,
            )
        else:
            failed_count += 1
            failed_albums.append(album)
            if getattr(queue, "state_tracker", None):
                queue.state_tracker.record_failed_item(
                    name=album.album_name,
                    artist=album.artist_name,
                    url=album.url,
                    reason=str(err),
                )
            err_str = str(err).lower()
            if any(w in err_str for w in ["bloqueo temporal", "bot", "sign in", "confirm you’re not a bot", "video unavailable", "rate-limited"]):
                queue.consecutive_block_errors += 1
            else:
                queue.consecutive_block_errors = 0

            logger.error("Error downloading %s '%s': %s", item_type.lower(), album.album_name, err)
            if not is_single_release and idx <= len(batch_items):
                batch_items[idx - 1]["status"] = "failed"
                _update_batch_progress(batch_items[idx - 1]["name"])
                queue._send_reply(
                    chat_id,
                    f"⚠️ <b>[{idx}/{total_albums}] Error descargando {item_type.lower()}:</b>\n"
                    f"• <b>{album.artist_name}</b> — <i>{album.album_name}</i>\n"
                    f"• <i>{err}</i>",
                    descargas_thread_id,
                )

            if queue.consecutive_block_errors >= 3:
                logger.warning("Circuit breaker activado: 3 fallos consecutivos por posible bloqueo de YouTube. Pausando cola.")
                queue.paused = True
                qsize = queue.queue.qsize() + (total_albums - idx)
                queue._send_reply(
                    chat_id,
                    f"🛑 <b>Cola pausada por protección (Circuit Breaker):</b>\n"
                    f"• Se detectaron <b>3 fallos consecutivos</b> por posible bloqueo o restricción de YouTube.\n"
                    f"• Se <b>pausó la cola automáticamente</b> para no quemar tus cuentas ni prolongar bloqueos ({qsize} tareas en espera retenidas).\n"
                    f"💡 <i>Espera unos minutos o renueva cookies, luego usa <code>/reanudar</code> para continuar.</i>",
                    descargas_thread_id,
                )
                break

        with queue.lock:
            if queue.current_job and idx < len(job.albums):
                queue.current_job.albums = job.albums[idx:]
                queue._save_queue_state()

    for t_idx, track in enumerate(job.tracks, 1):
        while queue.paused and not queue.cancel_requested and queue.running:
            time.sleep(1.0)

        if queue.cancel_requested:
            logger.info("Cancelación de descarga de canciones solicitada para '%s'. Abortando cola.", job.title)
            queue._send_reply(
                chat_id,
                f"🛑 <b>Descarga cancelada a petición del usuario.</b>\n"
                f"• <b>Tarea:</b> {job.title}\n"
                f"• <b>Canciones procesadas antes de cancelar:</b> {t_idx - 1}/{total_tracks}",
                descargas_thread_id,
            )
            break
        if not queue.running:
            break

        with queue.lock:
            queue.active_details = f"[Pista {t_idx}/{total_tracks}] {track.artist} - {track.title}"

        item_batch_idx = len(job.albums) + t_idx - 1
        if not is_single_release and item_batch_idx < len(batch_items):
            batch_items[item_batch_idx]["status"] = "downloading"
            _update_batch_progress(batch_items[item_batch_idx]["name"])

        # 1. Check Navidrome library for single track
        existing_track = None
        if queue.navidrome:
            try:
                existing_track = queue.navidrome.find_best_match(track.title, track.artist, strict_artist=True)
            except Exception as ex:
                logger.warning("Error buscando canción '%s' en Navidrome: %s", track.title, ex)

        if existing_track:
            skipped_count += 1
            queue._clean_playlist_for_resolved_items(job, resolved_titles=[track.title])
            matched_album = existing_track.get("album", "Biblioteca")
            logger.info("La pista '%s' de '%s' ya existe en Navidrome (álbum: '%s'). Omitiendo.", track.title, track.artist, matched_album)
            if not is_single_release and item_batch_idx < len(batch_items):
                batch_items[item_batch_idx]["status"] = "skipped"
                _update_batch_progress(batch_items[item_batch_idx]["name"])
                queue._send_reply(
                    chat_id,
                    f"💿 <b>[Pista {t_idx}/{total_tracks}] Canción ya en tu biblioteca:</b>\n"
                    f"• <b>{track.artist}</b> — <i>{track.title}</i>\n"
                    f"✨ <i>Encontrada en Navidrome (en '<b>{matched_album}</b>'). Se omitió la re-descarga.</i>",
                    descargas_thread_id,
                )
            elif is_single_release and job.status_msg_id and queue.listener:
                queue.listener.edit_message_text(
                    chat_id,
                    job.status_msg_id,
                    f"💿 <b>{track.artist} — {track.title}</b> ya existe en Navidrome (álbum: '{matched_album}').",
                )
            continue

        # 2. Check local disk in _Singles
        clean_track = queue.downloader.sanitize_name(track.title).lower()
        clean_artist = queue.downloader.sanitize_name(track.artist).lower() if track.artist else ""
        candidate_singles_folders = [queue.downloader.download_dir / "_Singles"]
        if queue.downloader.library_dir:
            candidate_singles_folders.extend([
                queue.downloader.library_dir / "downloads" / "_Singles",
                queue.downloader.library_dir / "_Singles",
            ])

        on_disk = False
        for s_folder in candidate_singles_folders:
            if s_folder.is_dir():
                if any(
                    clean_track in f.name.lower() and (not clean_artist or clean_artist in f.name.lower())
                    for f in s_folder.iterdir()
                    if f.is_file() and f.suffix.lower() in ('.m4a', '.mp3', '.opus', '.webm', '.flac')
                ):
                    on_disk = True
                    break

        if on_disk:
            skipped_count += 1
            if queue.navidrome:
                try:
                    queue.navidrome.start_scan(full_scan=False)
                except Exception:
                    pass
            queue._clean_playlist_for_resolved_items(job, resolved_titles=[track.title])
            logger.info("La pista '%s' ya existe en _Singles en disco. Omitiendo.", track.title)
            if not is_single_release and item_batch_idx < len(batch_items):
                batch_items[item_batch_idx]["status"] = "skipped"
                _update_batch_progress(batch_items[item_batch_idx]["name"])
                queue._send_reply(
                    chat_id,
                    f"📁 <b>[Pista {t_idx}/{total_tracks}] Canción ya en disco:</b>\n"
                    f"• <b>{track.artist}</b> — <i>{track.title}</i>\n"
                    f"✨ <i>Ya disponible en <code>{queue.win_dest}\\downloads\\_Singles</code>. Se omitió la re-descarga.</i>",
                    descargas_thread_id,
                )
            elif is_single_release and job.status_msg_id and queue.listener:
                queue.listener.edit_message_text(
                    chat_id,
                    job.status_msg_id,
                    f"📁 <b>{track.artist} — {track.title}</b> ya está disponible en disco (_Singles).",
                )
            continue

        # 3. Real track download
        success, err = queue.downloader.download_track(track)
        if success:
            ok_count += 1
            if not is_single_release and item_batch_idx < len(batch_items):
                batch_items[item_batch_idx]["status"] = "done"
                _update_batch_progress(batch_items[item_batch_idx]["name"])
                queue._send_reply(
                    chat_id,
                    f"📥 <b>[Pista {t_idx}/{total_tracks}] Canción descargada:</b>\n"
                    f"• <b>{track.artist}</b> — <i>{track.title}</i>\n"
                    f"• <b>Carpeta:</b> <code>{queue.win_dest}\\_Singles</code>",
                    descargas_thread_id,
                )
            elif is_single_release and job.status_msg_id and queue.listener:
                queue.listener.edit_message_text(
                    chat_id,
                    job.status_msg_id,
                    f"✅ <b>{track.artist} — {track.title}</b> descargada con éxito en <code>{queue.win_dest}\\_Singles</code>.",
                )
        else:
            failed_count += 1
            failed_tracks.append(track)
            if getattr(queue, "state_tracker", None):
                queue.state_tracker.record_failed_item(
                    name=track.title,
                    artist=track.artist,
                    url=track.url,
                    reason=str(err),
                )
            if not is_single_release and item_batch_idx < len(batch_items):
                batch_items[item_batch_idx]["status"] = "failed"
                _update_batch_progress(batch_items[item_batch_idx]["name"])
                queue._send_reply(
                    chat_id,
                    f"⚠️ <b>[Pista {t_idx}/{total_tracks}] Error descargando canción:</b>\n"
                    f"• <b>{track.artist}</b> — <i>{track.title}</i>\n"
                    f"• <i>{err}</i>",
                    descargas_thread_id,
                )
            elif is_single_release and job.status_msg_id and queue.listener:
                queue.listener.edit_message_text(
                    chat_id,
                    job.status_msg_id,
                    f"⚠️ <b>Error descargando {track.artist} — {track.title}:</b> {err}",
                )

        with queue.lock:
            if queue.current_job and t_idx < len(job.tracks):
                queue.current_job.tracks = job.tracks[t_idx:]
                queue._save_queue_state()

    if not is_single_release:
        retry_markup = None
        if failed_albums or failed_tracks:
            retry_token = uuid.uuid4().hex[:8]
            queue.save_retry_session(retry_token, {
                "chat_id": chat_id,
                "thread_id": descargas_thread_id,
                "title": job.title,
                "albums": [asdict(a) for a in failed_albums],
                "tracks": [asdict(t) for t in failed_tracks],
                "created_at": time.time(),
            })
            total_failed = len(failed_albums) + len(failed_tracks)
            retry_markup = {
                "inline_keyboard": [
                    [{"text": f"🔄 Descargar Fallidos ({total_failed})", "callback_data": f"retry_fail:{retry_token}"}]
                ]
            }

        _update_batch_progress("Completado", finished=True, reply_markup=retry_markup)
        if queue.cancel_requested or queue.consecutive_block_errors >= 3:
            final_icon = "🛑"
            final_title = "Descarga detenida / cancelada"
        else:
            final_icon = "✅" if failed_count == 0 else "⚠️"
            final_title = "¡Descarga finalizada con éxito!" if failed_count == 0 else f"Descarga finalizada con {failed_count} error{'es' if failed_count > 1 else ''}"
        queue._send_reply(
            chat_id,
            f"{final_icon} <b>{final_title}</b>\n\n"
            f"• <b>Tarea:</b> {job.title}\n"
            f"• <b>Descargados nuevos:</b> {ok_count}\n"
            f"• <b>Omitidos (ya en biblioteca/disco):</b> {skipped_count}\n"
            f"• <b>Fallidos:</b> {failed_count}\n"
            f"• <b>Ubicación:</b> <code>{queue.win_dest}</code>\n\n"
            f"💡 <i>Archivos catalogados, enriquecidos con plugins de Picard y organizados en la biblioteca.</i>",
            descargas_thread_id,
            reply_markup=retry_markup,
        )

    if queue.navidrome:
        try:
            queue.navidrome.start_scan(full_scan=False)
            logger.info("Triggered Navidrome library scan after completing download job.")
        except Exception as se:
            logger.debug("Could not trigger Navidrome scan: %s", se)

    try:
        gc.collect()
    except Exception:
        pass
