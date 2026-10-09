#!/usr/bin/env python3
"""
DownloadQueue: Sequential FIFO queue for executing downloads safely.
"""

import json
import logging
import os
import queue
import re
import threading
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from ..models import DownloadJob, ResolvedAlbum, ResolvedTrack
from ..navidrome import NavidromeClient
from .batch_view import (
    build_album_completed_rich_html,
    build_album_progress_rich_html,
    build_album_rich_html,
    build_batch_progress_rich_html,
    clean_rich_html,
    format_duration,
)
from .core import MusicDownloader
from .job_processor import process_download_job
from .notifications import resolve_topic_thread_id, send_album_rich_notification
from .retries import RetrySessionManager

if TYPE_CHECKING:
    from ..telegram import TelegramBotListener

logger = logging.getLogger("listenbrainz_notifier")


class DownloadQueue:
    """
    Sequential FIFO queue for executing downloads safely.
    Guarantees no race conditions, rate limit storms, or cookie collisions.
    """

    _format_duration = staticmethod(format_duration)
    _clean_rich_html = staticmethod(clean_rich_html)
    _build_album_rich_html = staticmethod(build_album_rich_html)
    _build_album_progress_rich_html = staticmethod(build_album_progress_rich_html)
    _build_album_completed_rich_html = staticmethod(build_album_completed_rich_html)
    _build_batch_progress_rich_html = staticmethod(build_batch_progress_rich_html)

    def __init__(
        self,
        downloader: MusicDownloader,
        navidrome: Optional[NavidromeClient],
        listener: Optional["TelegramBotListener"],
        win_dest: str,
        bot_token: Optional[str] = None,
        telegram_sender: Optional[Any] = None,
        queue_file: Optional[str] = None,
        state_tracker: Optional[Any] = None,
    ):
        self.downloader = downloader
        self.navidrome = navidrome
        self.listener = listener
        self.win_dest = win_dest
        self.bot_token = bot_token
        self.telegram_sender = telegram_sender
        self.queue: queue.Queue = queue.Queue()
        self.lock = threading.RLock()
        self.running = True
        self.paused = False
        self.cancel_requested = False
        self.current_job: Optional[DownloadJob] = None
        self.active_details: str = ""
        self.consecutive_block_errors: int = 0
        self.state_tracker = state_tracker

        self.queue_file = Path(queue_file) if queue_file else Path(downloader.download_dir) / ".download_queue.json"
        retry_path = Path(downloader.download_dir) / ".retry_sessions.json"
        self.retry_mgr = RetrySessionManager(retry_path)
        self.retry_sessions = self.retry_mgr.retry_sessions
        self.retry_sessions_file = retry_path

        self._load_queue_state()

        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True, name="DownloadQueueWorker")
        self.worker_thread.start()

    def _save_queue_state(self):
        if not self.queue_file:
            return
        try:
            pending_jobs = []
            if self.current_job:
                pending_jobs.append(asdict(self.current_job))
            with self.lock:
                for item in list(self.queue.queue):
                    pending_jobs.append(asdict(item))

            data = {
                "paused": self.paused,
                "saved_at": time.time(),
                "jobs": pending_jobs,
            }
            self.queue_file.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.queue_file.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            tmp_path.replace(self.queue_file)
        except Exception as e:
            logger.debug("Error saving download queue state to %s: %s", self.queue_file, e)

    def _load_queue_state(self):
        if not self.queue_file or not self.queue_file.is_file():
            return
        try:
            with open(self.queue_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.paused = data.get("paused", False)
            jobs_data = data.get("jobs", [])
            restored_count = 0
            for jd in jobs_data:
                albums = [
                    ResolvedAlbum(
                        artist_name=a.get("artist_name", ""),
                        album_name=a.get("album_name", ""),
                        url=a.get("url", ""),
                        cover_url=a.get("cover_url"),
                        tracks=a.get("tracks"),
                        is_single=a.get("is_single", False),
                        year=a.get("year"),
                    )
                    for a in jd.get("albums", [])
                ]
                tracks = [
                    ResolvedTrack(
                        title=t.get("title", ""),
                        artist=t.get("artist", ""),
                        url=t.get("url", ""),
                        cover_url=t.get("cover_url"),
                        album_name=t.get("album_name"),
                        duration=t.get("duration", 0),
                    )
                    for t in jd.get("tracks", [])
                ]
                job = DownloadJob(
                    job_id=jd.get("job_id", str(uuid.uuid4())),
                    title=jd.get("title", "Tarea restaurada"),
                    chat_id=jd.get("chat_id", ""),
                    thread_id=jd.get("thread_id"),
                    albums=albums,
                    tracks=tracks,
                    source_desc=jd.get("source_desc"),
                    status_msg_id=jd.get("status_msg_id"),
                )
                self.queue.put(job)
                restored_count += 1

            if restored_count > 0:
                logger.info(
                    "Restored %d queued download jobs (paused=%s) from %s",
                    restored_count,
                    self.paused,
                    self.queue_file,
                )
        except Exception as e:
            logger.warning("Error loading download queue state from %s: %s", self.queue_file, e)

    def save_retry_session(self, token: str, data: Dict[str, Any]):
        with self.lock:
            self.retry_mgr.save(token, data)

    def get_retry_session(self, token: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            return self.retry_mgr.get(token)

    def pop_retry_session(self, token: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            return self.retry_mgr.pop(token)

    def pause(self) -> Tuple[bool, str]:
        """Pauses the download queue so pending jobs are kept without processing."""
        with self.lock:
            self.paused = True
            self._save_queue_state()
            qsize = self.queue.qsize() + (1 if self.current_job else 0)
            return True, f"⏸️ Cola de descargas pausada ({qsize} tareas en espera retenidas)."

    def resume(self) -> Tuple[bool, str]:
        """Resumes the download queue."""
        with self.lock:
            self.paused = False
            self.consecutive_block_errors = 0
            self._save_queue_state()
            qsize = self.queue.qsize() + (1 if self.current_job else 0)
            return True, f"▶️ Cola de descargas reanudada ({qsize} tareas pendientes)."

    def cancel_active(self) -> Tuple[bool, str]:
        """Cancels the currently executing download job and flushes pending queue."""
        with self.lock:
            if self.current_job:
                self.cancel_requested = True
                cleared_count = 0
                while not self.queue.empty():
                    try:
                        self.queue.get_nowait()
                        self.queue.task_done()
                        cleared_count += 1
                    except Exception:
                        break
                title = self.current_job.title
                detail = f"{title} (y {cleared_count} tareas en cola descartadas)" if cleared_count else title
                self._save_queue_state()
                return True, detail
            return False, "No hay descargas activas en curso."

    def enqueue(self, job: DownloadJob) -> int:
        with self.lock:
            pos = self.queue.qsize()
            if self.current_job is not None:
                pos += 1
            self.queue.put(job)
            self._save_queue_state()
            return pos

    def get_status(self) -> str:
        with self.lock:
            if self.paused:
                qsize = self.queue.qsize() + (1 if self.current_job else 0)
                return f"Pausado ({qsize} tareas en espera)"
            if self.current_job:
                qsize = self.queue.qsize()
                queue_note = f" (En cola: {qsize})" if qsize > 0 else ""
                return f"En curso ({self.active_details}){queue_note}"
            return "Inactivo"

    def _send_reply(
        self,
        chat_id: str,
        text: str,
        thread_id: Optional[str],
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        if self.listener:
            return self.listener.send_reply(chat_id, text, thread_id, reply_markup=reply_markup)
        else:
            logger.info("[DownloadQueue Reply to %s] %s", chat_id, text)
            return None

    def _resolve_topic_thread_id(
        self,
        chat_id: str,
        topic_key: str,
        default_name: str,
        icon_color: Optional[int] = None,
        caller_thread_id: Optional[str] = None,
    ) -> Optional[str]:
        return resolve_topic_thread_id(self.listener, chat_id, topic_key, default_name, icon_color, caller_thread_id=caller_thread_id)

    def _send_album_rich_notification(
        self,
        chat_id: str,
        thread_id: Optional[str],
        album: ResolvedAlbum,
        target_folder: Path,
    ) -> bool:
        return send_album_rich_notification(self, chat_id, thread_id, album, target_folder)

    def _worker_loop(self):
        while self.running:
            try:
                job: DownloadJob = self.queue.get(timeout=2.0)
            except queue.Empty:
                continue

            with self.lock:
                self.current_job = job
                self.cancel_requested = False
                self.active_details = job.title
                self._save_queue_state()

            try:
                self._process_job(job)
            except Exception as e:
                logger.error("Error processing download job '%s': %s", job.title, e, exc_info=True)
            finally:
                with self.lock:
                    self.current_job = None
                    self.active_details = ""
                    self.cancel_requested = False
                    self._save_queue_state()
                self.queue.task_done()

    def _clean_playlist_for_resolved_items(
        self,
        job: DownloadJob,
        resolved_titles: Optional[List[str]] = None,
        album_name: Optional[str] = None,
    ):
        target_pls_name = ""
        if job.source_desc:
            target_pls_name = re.sub(r"\s*\([^)]*\)", "", job.source_desc).strip()
        elif job.title and "Playlist " in job.title:
            target_pls_name = job.title.split("Playlist ", 1)[1]
            target_pls_name = re.sub(r"\s*\([^)]*\)", "", target_pls_name).strip()

        if self.state_tracker:
            try:
                if album_name:
                    self.state_tracker.remove_from_last_batch(target_pls_name or None, album_name=album_name)
                if resolved_titles:
                    for t in resolved_titles:
                        self.state_tracker.remove_from_last_batch(target_pls_name or None, track_title=t)
            except Exception as ex:
                logger.debug("Error updating last batch in state_tracker: %s", ex)

        if not self.navidrome or not target_pls_name or not resolved_titles:
            return

        try:
            from ..parser import TrackParser
            pls = self.navidrome.get_playlists()
            if not isinstance(pls, list):
                return
            target_pl = None
            for p in pls:
                if isinstance(p, dict) and isinstance(p.get("name"), str) and target_pls_name.lower() in p.get("name", "").lower():
                    target_pl = p
                    break
            if not target_pl or not isinstance(target_pl, dict):
                return

            pl_id = target_pl.get("id")
            comment = ""
            details = self.navidrome.get_playlist_details(pl_id)
            if isinstance(details, dict) and details.get("comment"):
                comment = details.get("comment", "")
            elif isinstance(target_pl.get("comment"), str):
                comment = target_pl.get("comment", "")

            if isinstance(comment, str) and (("Tracks not matched" in comment) or ("Tracks not found in library" in comment)):
                new_comment = TrackParser.rebuild_comment_without_tracks(comment, set(resolved_titles))
                if new_comment != comment:
                    self.navidrome.update_playlist_comment(pl_id, new_comment)
                    logger.info("Downloader cleaned playlist comment for '%s' removing satisfied tracks: %s", target_pls_name, resolved_titles)
        except Exception as ex:
            logger.debug("Could not clean playlist comment from downloader: %s", ex)

    def _process_job(self, job: DownloadJob):
        process_download_job(self, job)
