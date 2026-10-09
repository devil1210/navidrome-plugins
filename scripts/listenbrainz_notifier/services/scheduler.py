#!/usr/bin/env python3
"""
Background periodic scheduler service:
Periodically checks Navidrome playlists and triggers sync notifications.
"""

import gc
import logging
import threading
import time
from typing import Any, Set

try:
    from ..modules.syncer import run_sync
except (ImportError, ValueError):
    from modules.syncer import run_sync

logger = logging.getLogger("listenbrainz_notifier")


def start_background_scheduler(
    listener: Any,
    daemon_interval: int,
    navidrome: Any,
    telegram: Any,
    state_tracker: Any,
    target_names: Set[str],
    fallback_to_track: bool,
    enricher: Any,
    default_max_albums: int,
) -> threading.Thread:
    """Spawns a daemon thread executing periodic background synchronization and 03:00 AM nightly maintenance."""

    def background_scheduler():
        logger.info("Background timer scheduler started (every %d min).", daemon_interval)
        last_sync_time = time.time()
        last_nightly_day = None

        while listener.running:
            time.sleep(min(daemon_interval * 60, 60))
            if not listener.running:
                break

            now = time.time()
            now_local = time.localtime()

            # 1. Check for 03:00 AM Nightly Maintenance
            if now_local.tm_hour == 3 and last_nightly_day != now_local.tm_yday:
                last_nightly_day = now_local.tm_yday
                try:
                    logger.info("🌙 Disparando mantenimiento de biblioteca y descargas nocturnas programadas (03:00 AM)...")
                    try:
                        from ..modules.nightly import NightlyDownloadManager
                    except (ImportError, ValueError):
                        from modules.nightly import NightlyDownloadManager

                    worker = getattr(listener, "download_worker", None)
                    downloader = getattr(worker, "downloader", None)
                    lib_dir = getattr(downloader, "library_dir", None) or getattr(worker, "library_dir", None)
                    dl_dir = getattr(downloader, "download_dir", None) or getattr(worker, "download_dir", None)
                    resolver = getattr(worker, "resolver", None)
                    descargas_tid = getattr(telegram, "descargas_thread_id", None) or getattr(worker, "descargas_thread_id", None)

                    nm = NightlyDownloadManager(
                        navidrome=navidrome,
                        download_queue=worker,
                        state_tracker=state_tracker,
                        library_dir=Path(lib_dir) if lib_dir else None,
                        download_dir=Path(dl_dir) if dl_dir else None,
                        telegram_sender=telegram,
                        descargas_thread_id=descargas_tid,
                        resolver=resolver,
                    )
                    nm.run_maintenance(max_albums=15, dry_run=False)
                except Exception as ne:
                    logger.error("Error en mantenimiento nocturno programado: %s", ne, exc_info=True)

            # 2. Check for periodic playlist sync
            if (now - last_sync_time) >= (daemon_interval * 60):
                last_sync_time = now
                try:
                    logger.info("Executing scheduled periodic check for playlists...")
                    curr_limit = state_tracker.get_limit(default_limit=default_max_albums)
                    run_sync(
                        navidrome=navidrome,
                        telegram=telegram,
                        state_tracker=state_tracker,
                        target_names=target_names,
                        fallback_to_track=fallback_to_track,
                        force=False,
                        dry_run=False,
                        enricher=enricher,
                        batch_number=1,
                        batch_size=curr_limit,
                    )
                    try:
                        gc.collect()
                    except Exception:
                        pass
                except Exception as e:
                    logger.error("Error in scheduled periodic check: %s", e, exc_info=True)

    sched_thread = threading.Thread(target=background_scheduler, daemon=True)
    sched_thread.start()
    return sched_thread
