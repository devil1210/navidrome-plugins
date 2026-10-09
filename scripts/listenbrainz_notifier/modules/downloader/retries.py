#!/usr/bin/env python3
"""
Retry session manager for download jobs with partial failures.
Persists sessions across restarts with a 24-hour expiration window.
"""

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("listenbrainz_notifier")


class RetrySessionManager:
    """Manages retry sessions for failed download items."""

    def __init__(self, retry_sessions_file: Optional[Path] = None):
        self.retry_sessions_file = retry_sessions_file
        self.retry_sessions: Dict[str, Dict[str, Any]] = self._load()

    def _load(self) -> Dict[str, Dict[str, Any]]:
        sessions = {}
        if not self.retry_sessions_file or not self.retry_sessions_file.is_file():
            return sessions
        try:
            with open(self.retry_sessions_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            now = time.time()
            for tok, s in data.items():
                if now - s.get("created_at", 0) < 86400:
                    sessions[tok] = s
        except Exception as e:
            logger.debug("Error loading retry sessions from %s: %s", self.retry_sessions_file, e)
        return sessions

    def save_to_disk(self):
        if not self.retry_sessions_file:
            return
        try:
            now = time.time()
            clean = {k: v for k, v in self.retry_sessions.items() if now - v.get("created_at", 0) < 86400}
            self.retry_sessions_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.retry_sessions_file.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(clean, f, ensure_ascii=False, indent=2)
            tmp.replace(self.retry_sessions_file)
        except Exception as e:
            logger.debug("Error saving retry sessions to %s: %s", self.retry_sessions_file, e)

    def save(self, token: str, data: Dict[str, Any]):
        self.retry_sessions[token] = data
        self.save_to_disk()

    def get(self, token: str) -> Optional[Dict[str, Any]]:
        return self.retry_sessions.get(token)

    def pop(self, token: str) -> Optional[Dict[str, Any]]:
        sess = self.retry_sessions.pop(token, None)
        self.save_to_disk()
        return sess
