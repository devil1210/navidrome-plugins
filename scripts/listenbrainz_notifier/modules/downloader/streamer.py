#!/usr/bin/env python3
"""
AlbumProgressStreamer: Live progress streaming for album downloads via Telegram Bot API 10.1+.
"""

import logging
import re
import threading
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

import requests

from ..models import ResolvedAlbum
from ..syncer import titles_match

if TYPE_CHECKING:
    from .queue import DownloadQueue

logger = logging.getLogger("listenbrainz_notifier")


class AlbumProgressStreamer:
    """
    Manages live rich streaming updates for album downloads via Telegram Bot API 10.1+
    (sendRichMessageDraft with fallback to editMessageText), displaying real-time
    track checkmarks (✅/⏳/⏸️) in an expanded <details open> view, and transitioning
    cleanly to the finalized rich card upon completion.
    """

    def __init__(
        self,
        queue: "DownloadQueue",
        chat_id: str,
        thread_id: Optional[str],
        album: ResolvedAlbum,
        tracks: Optional[List[Any]] = None,
        dest_folder: str = "",
        missing_indices: Optional[Set[int]] = None,
        initial_msg_id: Optional[int] = None,
    ):
        self.queue = queue
        self.chat_id = str(chat_id).strip() if chat_id else ""
        self.thread_id = str(thread_id).strip() if thread_id else None
        self.album = album
        self.dest_folder = dest_folder
        self.missing_indices = missing_indices
        self.token = (
            queue.bot_token
            or (queue.listener.bot_token if queue.listener else None)
            or (queue.telegram_sender.bot_token if queue.telegram_sender else None)
        )
        self.draft_id = (abs(hash(f"{album.artist_name}_{album.album_name}_{time.time()}")) % 2147483640) + 1

        self.tracks: List[Dict[str, Any]] = []
        raw_tracks = tracks or []
        for idx, trk in enumerate(raw_tracks, 1):
            if isinstance(trk, dict):
                t_idx = trk.get("index", idx)
                t_title = trk.get("title", f"Pista {idx}")
                t_dur = trk.get("duration", 0)
            else:
                t_idx = idx
                t_title = str(trk)
                t_dur = 0

            if self.missing_indices is not None:
                if t_idx in self.missing_indices:
                    status = "pending"
                else:
                    status = "done"
            else:
                status = "pending"

            self.tracks.append({
                "index": t_idx,
                "title": t_title,
                "status": status,
                "duration": t_dur,
                "pct": "",
            })

        for t in self.tracks:
            if t["status"] == "pending":
                t["status"] = "downloading"
                break

        self.completed_indices: Set[int] = {t["index"] for t in self.tracks if t["status"] == "done"}
        self.last_update_time = 0.0
        self.min_update_interval = 1.2  # Telegram rate limit safety
        self.fallback_msg_id: Optional[int] = initial_msg_id
        self.draft_supported = True if initial_msg_id is None else False
        self.lock = threading.Lock()
        self.started = False

    def start(self):
        """Sends the initial rich progress draft or message."""
        if not self.token or not self.chat_id:
            return
        self.started = True
        self._send_update(force=True)

    def handle_progress(self, d: Dict[str, Any]):
        """Progress callback invoked by yt-dlp hooks."""
        if not self.started:
            self.start()

        status = d.get("status")
        info = d.get("info_dict") or {}

        # Resolve track index
        p_idx = info.get("playlist_index") or d.get("playlist_index")
        if not p_idx and d.get("filename"):
            m = re.search(r"(?:^|[\\/])(\d{1,2})\s*-\s*", str(d.get("filename")))
            if m:
                try:
                    p_idx = int(m.group(1))
                except Exception:
                    pass

        raw_title = info.get("title") or ""
        force = False

        with self.lock:
            # If track list was empty initially, dynamically discover from yt-dlp
            if not self.tracks and (raw_title or p_idx):
                n_entries = info.get("n_entries") or info.get("playlist_count") or (p_idx if p_idx else 1)
                curr_idx = p_idx or 1
                for i in range(1, max(n_entries, curr_idx) + 1):
                    self.tracks.append({
                        "index": i,
                        "title": raw_title if i == curr_idx else f"Pista {i}",
                        "status": "pending",
                        "duration": 0,
                        "pct": "",
                    })
                if self.tracks:
                    self.tracks[0]["status"] = "downloading"
            elif p_idx and p_idx > len(self.tracks):
                for i in range(len(self.tracks) + 1, p_idx + 1):
                    self.tracks.append({
                        "index": i,
                        "title": raw_title if i == p_idx else f"Pista {i}",
                        "status": "pending",
                        "duration": 0,
                        "pct": "",
                    })

            # Match target track
            target_track = None
            if p_idx:
                for t in self.tracks:
                    if t["index"] == p_idx:
                        target_track = t
                        break

            if not target_track and raw_title:
                for t in self.tracks:
                    if titles_match(t["title"], raw_title):
                        target_track = t
                        p_idx = t["index"]
                        break

            if status == "downloading":
                if target_track:
                    target_track["status"] = "downloading"
                    if raw_title and not target_track["title"].startswith(raw_title[:10]):
                        target_track["title"] = raw_title

                # Ensure prior tracks are marked done
                if p_idx:
                    for t in self.tracks:
                        if t["index"] < p_idx and (self.missing_indices is None or t["index"] in self.missing_indices):
                            self.completed_indices.add(t["index"])
                            t["status"] = "done"
                return

            elif status == "finished":
                force = True
                if target_track:
                    target_track["status"] = "done"
                    dur = info.get("duration")
                    if dur:
                        target_track["duration"] = int(dur)
                    self.completed_indices.add(target_track["index"])

                # Mark next pending track as downloading
                for t in self.tracks:
                    if t["status"] == "pending":
                        t["status"] = "downloading"
                        break

        self._send_update(force=force)

    def _send_update(self, force: bool = False):
        if not self.token or not self.chat_id:
            return

        now = time.time()
        if not force and (now - self.last_update_time) < self.min_update_interval:
            return
        self.last_update_time = now

        with self.lock:
            completed_count = len(self.completed_indices)
            total_count = len(self.tracks)
            current_title = ""
            for t in self.tracks:
                if t["status"] == "downloading":
                    current_title = t["title"]
                    break
            if not current_title and completed_count < total_count:
                for t in self.tracks:
                    if t["status"] == "pending":
                        current_title = t["title"]
                        break

            html_content = self.queue._build_album_progress_rich_html(
                album_name=self.album.album_name,
                artist_name=self.album.artist_name,
                tracks=self.tracks,
                completed_count=completed_count,
                total_count=total_count,
                current_track_title=current_title,
                dest_folder=self.dest_folder,
            )

        rich_obj = {"html": html_content}

        # 1. Try sendRichMessageDraft (API 10.1+)
        if self.draft_supported and self.queue.listener:
            ok = self.queue.listener.send_rich_message_draft(
                chat_id=self.chat_id,
                draft_id=self.draft_id,
                rich_message=rich_obj,
                thread_id=self.thread_id,
            )
            if ok:
                return
            else:
                self.draft_supported = False

        elif self.draft_supported and self.token:
            draft_url = f"https://api.telegram.org/bot{self.token}/sendRichMessageDraft"
            payload: Dict[str, Any] = {
                "chat_id": self.chat_id,
                "draft_id": self.draft_id,
                "rich_message": rich_obj,
            }
            if self.thread_id:
                payload["message_thread_id"] = self.thread_id
            try:
                r = requests.post(draft_url, json=payload, timeout=6)
                if r.status_code == 200 and r.json().get("ok"):
                    return
                self.draft_supported = False
            except Exception:
                self.draft_supported = False

        # 2. Fallback: message editing
        if self.fallback_msg_id is None:
            if self.queue.listener:
                msg_id = self.queue.listener.send_reply(
                    chat_id=self.chat_id,
                    text="📥 <b>Iniciando descarga...</b>",
                    thread_id=self.thread_id,
                    rich_message=rich_obj,
                )
                if msg_id:
                    self.fallback_msg_id = msg_id
        else:
            if self.queue.listener:
                self.queue.listener.edit_message_text(
                    chat_id=self.chat_id,
                    message_id=self.fallback_msg_id,
                    text="📥 <b>Descargando pistas...</b>",
                    rich_message=rich_obj,
                )

    def finish(self, success: bool = True, final_note: str = ""):
        """Finalizes the progress stream and updates the message to the completed state."""
        with self.lock:
            for t in self.tracks:
                if success and t["status"] in ("downloading", "pending"):
                    t["status"] = "done"

            completed_count = len(self.completed_indices) if not success else len(self.tracks)
            total_count = len(self.tracks)

            html_content = self.queue._build_album_completed_rich_html(
                album_name=self.album.album_name,
                artist_name=self.album.artist_name,
                tracks=self.tracks,
                completed_count=completed_count,
                total_count=total_count,
                dest_folder=self.dest_folder,
                success=success,
                final_note=final_note,
            )

        rich_obj = {"html": html_content}
        if self.fallback_msg_id and self.queue.listener:
            try:
                self.queue.listener.edit_message_text(
                    chat_id=self.chat_id,
                    message_id=self.fallback_msg_id,
                    text="✅ <b>¡Descarga finalizada con éxito!</b>" if success else "⚠️ <b>Descarga finalizada con advertencias</b>",
                    rich_message=rich_obj,
                )
            except Exception as e:
                logger.debug("Error editing message to completed state: %s", e)
