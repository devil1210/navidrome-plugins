#!/usr/bin/env python3
"""
Nightly Download Manager:
Executes scheduled off-peak maintenance, consolidating fragmented album folders,
auditing incomplete albums, dynamically verifying day-time completions, and
downloading missing tracks while recording blocked/unavailable tracks to prevent retry loops.
"""

import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .auditor import IncompleteAlbumAuditor
from .consolidator import FolderConsolidator
from .models import DownloadJob, ResolvedAlbum
from .navidrome import NavidromeClient
from .state import StateTracker

logger = logging.getLogger("listenbrainz_notifier")


class NightlyDownloadManager:
    """
    Manages nightly automated library repairs and missing track downloads.
    """

    def __init__(
        self,
        navidrome: Optional[NavidromeClient] = None,
        download_queue: Optional[Any] = None,
        state_tracker: Optional[StateTracker] = None,
        library_dir: Optional[Path] = None,
        download_dir: Optional[Path] = None,
        telegram_sender: Optional[Any] = None,
        descargas_thread_id: Optional[str] = None,
        resolver: Optional[Any] = None,
    ):
        self.navidrome = navidrome
        self.download_queue = download_queue
        self.state_tracker = state_tracker
        self.library_dir = library_dir
        self.download_dir = download_dir
        self.telegram_sender = telegram_sender
        self.descargas_thread_id = descargas_thread_id
        self.resolver = resolver

        self.consolidator = FolderConsolidator(
            library_dir=library_dir,
            download_dir=download_dir,
            navidrome=navidrome,
        )
        self.auditor = IncompleteAlbumAuditor(
            navidrome=navidrome,
            downloader=getattr(download_queue, "downloader", None),
            state_tracker=state_tracker,
            library_dir=library_dir,
            resolver=resolver,
        )

    def run_maintenance(self, max_albums: int = 10, dry_run: bool = False) -> Dict[str, Any]:
        """
        Executes the complete nightly maintenance pipeline.
        1. Consolidates fragmented duplicate folders.
        2. Audits incomplete albums.
        3. Enqueues missing tracks with dynamic real-time re-evaluation.
        """
        logger.info("🌙 Iniciando mantenimiento de biblioteca y descargas nocturnas...")
        start_time = time.time()

        # Step 1: Consolidate folders
        consolidated = self.consolidator.consolidate_all(dry_run=dry_run)

        # Step 2: Audit incomplete albums
        incomplete = self.auditor.find_incomplete_albums(limit=max_albums)

        completed_count = 0
        skipped_count = 0
        failed_count = 0
        processed_albums: List[str] = []

        if not dry_run and incomplete and self.download_queue:
            for item in incomplete:
                alb_name = item["album_name"]
                artist_name = item["artist_name"]
                alb_id = item["id"]

                # Dynamic check: Did the user already complete this album today?
                if self.navidrome:
                    try:
                        latest_tracks = self.navidrome.get_album_tracks(alb_id)
                        if latest_tracks and len(latest_tracks) > item["present_count"] and len(latest_tracks) >= item.get("expected_count", 999):
                            logger.info("🌙 Álbum '%s' de '%s' ya fue completado durante el día. Omitiendo.", alb_name, artist_name)
                            skipped_count += 1
                            continue
                    except Exception as e:
                        logger.debug("Error verificando estado actual en Navidrome: %s", e)

                # Check if permanently blocked
                if self.state_tracker.is_failed_blocked(alb_name, artist_name):
                    logger.info("🌙 Omitiendo '%s' (registrado como fallido/bloqueado persistentemente).", alb_name)
                    skipped_count += 1
                    continue

                # Enqueue album for completion
                job = DownloadJob(
                    job_id=f"nightly_{int(time.time()*1000)}",
                    chat_id=str(getattr(self.telegram_sender, "chat_id", "") or ""),
                    thread_id=self.descargas_thread_id,
                    title=f"🌙 Nocturno: {artist_name} — {alb_name}",
                    albums=[item["resolved_album"]],
                    tracks=[],
                    source_desc="Mantenimiento Nocturno",
                )
                self.download_queue.enqueue(job)
                completed_count += 1
                processed_albums.append(f"{artist_name} — {alb_name}")

        duration = int(time.time() - start_time)
        summary = {
            "consolidated_folders": len(consolidated),
            "incomplete_detected": len(incomplete),
            "enqueued_for_download": completed_count,
            "skipped_already_complete": skipped_count,
            "duration_seconds": duration,
            "processed_albums": processed_albums,
        }

        # Step 3: Send Telegram summary if Telegram sender is available
        if self.telegram_sender and (consolidated or completed_count > 0):
            msg_lines = [
                "🌙 <b>Reporte de Mantenimiento Nocturno</b>\n",
                f"• <b>Carpetas fragmentadas unificadas:</b> {len(consolidated)}",
                f"• <b>Álbumes incompletos detectados:</b> {len(incomplete)}",
                f"• <b>Encolados para completar:</b> {completed_count}",
                f"• <b>Omitidos (ya al día / bloqueados):</b> {skipped_count}",
                f"• <b>Duración:</b> {duration}s",
            ]
            if processed_albums:
                msg_lines.append("\n<b>Álbumes procesados:</b>")
                for a in processed_albums[:8]:
                    msg_lines.append(f"  ✓ <i>{a}</i>")
                if len(processed_albums) > 8:
                    msg_lines.append(f"  <i>... y {len(processed_albums) - 8} más</i>")

            try:
                self.telegram_sender.send_notification(
                    "\n".join(msg_lines),
                    thread_id=self.descargas_thread_id,
                )
            except Exception as e:
                logger.debug("Error enviando reporte nocturno a Telegram: %s", e)

        logger.info("🌙 Mantenimiento nocturno finalizado: %s", summary)
        return summary
