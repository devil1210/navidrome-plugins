#!/usr/bin/env python3
"""
TelegramBotWebhookHandler: Lightweight HTTP webhook server handler for external triggers.
Allows integrations (like browser extensions or external scripts) to execute bot commands.
"""

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler
from typing import Any

logger = logging.getLogger("listenbrainz_notifier")


class TelegramBotWebhookHandler(BaseHTTPRequestHandler):
    """
    Lightweight HTTP server handler that allows external integrations (such as the
    Microsoft Edge extension or curl) to trigger bot commands directly (/add, etc.)
    without being blocked by Telegram Bot API's loop-prevention filter.
    """
    listener_ref: Any = None

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("Webhook HTTP: " + format, *args)

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        if self.path in ("/health", "/status", "/api/health", "/"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._send_cors_headers()
            self.end_headers()
            resp = json.dumps({"ok": True, "service": "navidrome-telegram-bot", "status": "running"})
            self.wfile.write(resp.encode("utf-8"))
            return

        self.send_response(404)
        self._send_cors_headers()
        self.end_headers()

    def do_POST(self) -> None:
        if self.path in ("/api/add", "/add", "/api/command"):
            content_length = int(self.headers.get("Content-Length", 0))
            post_body = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(post_body) if post_body else {}
                raw_text = data.get("url") or data.get("text") or data.get("command") or ""
                raw_text = raw_text.strip()
                if not raw_text:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self._send_cors_headers()
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": False, "error": "Campo 'url' requerido."}).encode("utf-8"))
                    return

                cmd_text = raw_text if raw_text.startswith("/") else f"/add {raw_text}"
                target_chat = str(data.get("chat_id") or "").strip()
                if not target_chat and self.listener_ref and self.listener_ref.authorized_chat_ids:
                    target_chat = list(self.listener_ref.authorized_chat_ids)[0]
                thread_id = data.get("thread_id")
                if not thread_id and self.listener_ref and hasattr(self.listener_ref, "get_or_create_topic"):
                    thread_id = self.listener_ref.get_or_create_topic(target_chat, "descargas", "📥 Descargas", icon_color=7322096, caller_thread_id=thread_id)

                mock_update = {
                    "message": {
                        "chat": {"id": target_chat},
                        "text": cmd_text,
                        "message_thread_id": thread_id,
                    }
                }
                logger.info("Triggering Webhook bot command: %s (chat: %s)", cmd_text, target_chat)
                if self.listener_ref:
                    threading.Thread(
                        target=self.listener_ref.handle_update,
                        args=(mock_update,),
                        daemon=True,
                    ).start()

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({
                    "ok": True,
                    "command": cmd_text,
                    "chat_id": target_chat,
                    "message": "Comando enviado al bot correctamente."
                }).encode("utf-8"))
                return
            except Exception as e:
                logger.error("Error processing Webhook POST: %s", e)
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode("utf-8"))
                return

        self.send_response(404)
        self._send_cors_headers()
        self.end_headers()
