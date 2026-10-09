#!/usr/bin/env python3
"""
TelegramBotListener:
Long-polling bot listener for Telegram commands, topics management,
message sending/editing, and IPC triggers.
"""

import json
import logging
import os
import threading
import time
from http.server import HTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests

from .dispatcher import dispatch_update
from .webhook import TelegramBotWebhookHandler

logger = logging.getLogger("listenbrainz_notifier")


class TelegramBotListener:
    """
    Listens for interactive Telegram slash commands (/sync, /buscar, /limit, /status, /help)
    via long-polling and triggers real-time synchronization.
    """

    def __init__(
        self,
        bot_token: str,
        authorized_chat_ids: List[str],
        on_sync_command: Callable[[str, Optional[str], Optional[str]], None],
        on_status_command: Callable[[], str],
        on_limit_command: Optional[Callable[[str], str]] = None,
        on_download_command: Optional[Callable[[str, Optional[str], Optional[str]], None]] = None,
        on_add_command: Optional[Callable[[str, Optional[str], str], None]] = None,
        on_discover_command: Optional[Callable[[str, Optional[str], Optional[str]], None]] = None,
        on_new_releases_command: Optional[Callable[[str, Optional[str], Optional[str]], None]] = None,
        on_download_rec_command: Optional[Callable[[str, Optional[str], Optional[str]], None]] = None,
        on_download_nov_command: Optional[Callable[[str, Optional[str], Optional[str]], None]] = None,
        on_add_disc_callback: Optional[Callable[[str, Optional[str], int, str, str], None]] = None,
        on_retry_failed_callback: Optional[Callable[[str, Optional[str], int, str, str], None]] = None,
        on_discard_command: Optional[Callable[[str, Optional[str], str], None]] = None,
        on_restore_command: Optional[Callable[[str, Optional[str], str], None]] = None,
        on_injected_command: Optional[Callable[[str, Optional[str]], None]] = None,
        on_incompletos_command: Optional[Callable[[str, Optional[str]], None]] = None,
        on_unificar_command: Optional[Callable[[str, Optional[str]], None]] = None,
        on_noche_command: Optional[Callable[[str, Optional[str]], None]] = None,
        on_fallidos_command: Optional[Callable[[str, Optional[str]], None]] = None,
        on_reintentar_fallidos_command: Optional[Callable[[str, Optional[str]], None]] = None,
        on_completar_command: Optional[Callable[[str, Optional[str], str], None]] = None,
        on_download_incomplete_batch_callback: Optional[Callable[[str, Optional[str], int, str], None]] = None,
        state_tracker: Optional[Any] = None,
    ):
        self.bot_token = bot_token
        self.authorized_chat_ids = {str(cid).strip() for cid in authorized_chat_ids if str(cid).strip()}
        self.on_sync_command = on_sync_command
        self.on_status_command = on_status_command
        self.on_limit_command = on_limit_command
        self.on_download_command = on_download_command
        self.on_add_command = on_add_command
        self.on_discover_command = on_discover_command
        self.on_new_releases_command = on_new_releases_command
        self.on_download_rec_command = on_download_rec_command
        self.on_download_nov_command = on_download_nov_command
        self.on_add_disc_callback = on_add_disc_callback
        self.on_retry_failed_callback = on_retry_failed_callback
        self.on_discard_command = on_discard_command
        self.on_restore_command = on_restore_command
        self.on_injected_command = on_injected_command
        self.on_incompletos_command = on_incompletos_command
        self.on_unificar_command = on_unificar_command
        self.on_noche_command = on_noche_command
        self.on_fallidos_command = on_fallidos_command
        self.on_reintentar_fallidos_command = on_reintentar_fallidos_command
        self.on_completar_command = on_completar_command
        self.on_download_incomplete_batch_callback = on_download_incomplete_batch_callback
        self.state_tracker = state_tracker
        self.download_worker: Optional[Any] = None
        self.last_update_id = 0
        self.running = False
        self.topics_cache_file = os.environ.get("TELEGRAM_TOPICS_CACHE_FILE") or (
            str(Path(getattr(state_tracker, "state_file", ".state.json")).parent / ".topics_cache.json")
            if state_tracker and getattr(state_tracker, "state_file", None)
            else ".topics_cache.json"
        )
        self.topics_cache: Dict[str, Dict[str, int]] = self._load_topics_cache()

    def _clear_invalid_thread_cache(self, chat_id: str, thread_id: Any):
        cid_str = str(chat_id).strip()
        tid_str = str(thread_id).strip()
        if cid_str in self.topics_cache:
            to_remove = [k for k, v in self.topics_cache[cid_str].items() if str(v) == tid_str]
            for k in to_remove:
                self.topics_cache[cid_str].pop(k, None)
                logger.info("Cleared invalid topic '%s' (thread_id=%s) from cache for chat %s", k, tid_str, cid_str)
            if to_remove:
                self._save_topics_cache()

    def send_reply(
        self,
        chat_id: str,
        text: str,
        thread_id: Optional[str] = None,
        parse_mode: str = "HTML",
        photo_url: Optional[str] = None,
        reply_markup: Optional[Dict[str, Any]] = None,
        rich_message: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        """Sends a text, photo, or rich_message reply to the user with optional inline keyboard buttons. Returns message_id."""
        if rich_message:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendRichMessage"
            payload: Dict[str, Any] = {
                "chat_id": chat_id,
                "rich_message": rich_message,
            }
            has_rich_buttons = any(b.get("type") == "buttons" for b in rich_message.get("blocks", []))
            if reply_markup and not has_rich_buttons:
                payload["reply_markup"] = reply_markup
            if thread_id:
                payload["message_thread_id"] = thread_id
            try:
                resp = requests.post(url, json=payload, timeout=10)
                if resp.status_code == 200 and resp.json().get("ok"):
                    return resp.json().get("result", {}).get("message_id")
                if thread_id and resp.status_code == 400 and "message thread not found" in resp.text:
                    logger.warning("Thread %s not found in chat %s during sendRichMessage; retrying without thread", thread_id, chat_id)
                    self._clear_invalid_thread_cache(chat_id, thread_id)
                    return self.send_reply(chat_id, text, thread_id=None, parse_mode=parse_mode, reply_markup=reply_markup, rich_message=rich_message)
                logger.warning("sendRichMessage failed (%s), falling back to sendMessage", resp.text)
            except Exception as e:
                logger.warning("sendRichMessage error (%s), falling back to sendMessage", e)

        if photo_url:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendPhoto"
            payload = {
                "chat_id": chat_id,
                "photo": photo_url,
                "caption": text[:1024],
                "parse_mode": parse_mode,
            }
            if reply_markup:
                payload["reply_markup"] = json.dumps(reply_markup)
            if thread_id:
                payload["message_thread_id"] = thread_id
            try:
                resp = requests.post(url, json=payload, timeout=12)
                if resp.status_code == 200 and resp.json().get("ok"):
                    msg_id = resp.json().get("result", {}).get("message_id")
                    if len(text) > 1024:
                        self.send_reply(chat_id, text[1024:], thread_id=thread_id, parse_mode=parse_mode, reply_markup=reply_markup)
                    return msg_id
                if thread_id and resp.status_code == 400 and "message thread not found" in resp.text:
                    logger.warning("Thread %s not found in chat %s during sendPhoto; retrying without thread", thread_id, chat_id)
                    self._clear_invalid_thread_cache(chat_id, thread_id)
                    return self.send_reply(chat_id, text, thread_id=None, parse_mode=parse_mode, photo_url=photo_url, reply_markup=reply_markup)
                logger.warning("sendPhoto failed (%s), falling back to sendMessage", resp.text)
            except Exception as pe:
                logger.warning("sendPhoto failed (%s), falling back to sendMessage", pe)

        # Standard text message
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)
        if thread_id:
            payload["message_thread_id"] = thread_id
        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 200 and resp.json().get("ok"):
                return resp.json().get("result", {}).get("message_id")
            if thread_id and resp.status_code == 400 and "message thread not found" in resp.text:
                logger.warning("Thread %s not found in chat %s, retrying without thread_id", thread_id, chat_id)
                self._clear_invalid_thread_cache(chat_id, thread_id)
                payload.pop("message_thread_id", None)
                retry_resp = requests.post(url, json=payload, timeout=10)
                if retry_resp.status_code == 200 and retry_resp.json().get("ok"):
                    return retry_resp.json().get("result", {}).get("message_id")
        except Exception as e:
            logger.error("Failed to send bot reply to %s: %s", chat_id, e)
        return None

    def edit_message_reply_markup(
        self,
        chat_id: str,
        message_id: int,
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Edits only the reply_markup (inline buttons) of an existing message."""
        url = f"https://api.telegram.org/bot{self.bot_token}/editMessageReplyMarkup"
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
        }
        if reply_markup is not None:
            payload["reply_markup"] = json.dumps(reply_markup)
        try:
            resp = requests.post(url, json=payload, timeout=8)
            return resp.status_code == 200 and resp.json().get("ok", False)
        except Exception as e:
            logger.debug("Error editing message reply markup: %s", e)
            return False

    def edit_message_text(
        self,
        chat_id: str,
        message_id: int,
        text: str,
        parse_mode: str = "HTML",
        reply_markup: Optional[Dict[str, Any]] = None,
        rich_message: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Edits the text and optional reply_markup of an existing message (supports rich_message)."""
        url = f"https://api.telegram.org/bot{self.bot_token}/editMessageText"
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
        }
        if rich_message:
            payload["rich_message"] = rich_message
            has_rich_buttons = any(b.get("type") == "buttons" for b in rich_message.get("blocks", []))
            if reply_markup is not None and not has_rich_buttons:
                payload["reply_markup"] = reply_markup
            try:
                resp = requests.post(url, json=payload, timeout=8)
                if resp.status_code == 200 and resp.json().get("ok", False):
                    return True
                logger.warning("editMessageText with rich_message failed (%s), falling back to standard text", resp.text)
            except Exception as re:
                logger.warning("editMessageText with rich_message error (%s), falling back", re)

        fallback_text = text or (rich_message.get("html") if rich_message else "") or "Actualizando..."
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": fallback_text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = json.dumps(reply_markup)
        try:
            resp = requests.post(url, json=payload, timeout=8)
            return resp.status_code == 200 and resp.json().get("ok", False)
        except Exception as e:
            logger.debug("Error editing message text: %s", e)
            return False

    def delete_message(self, chat_id: str, message_id: int) -> bool:
        """Deletes a message from the specified chat."""
        url = f"https://api.telegram.org/bot{self.bot_token}/deleteMessage"
        try:
            resp = requests.post(url, json={"chat_id": chat_id, "message_id": message_id}, timeout=6)
            return resp.status_code == 200 and resp.json().get("ok", False)
        except Exception as e:
            logger.debug("Error deleting message: %s", e)
            return False

    def send_rich_message_draft(
        self,
        chat_id: str,
        draft_id: int,
        rich_message: Dict[str, Any],
        thread_id: Optional[str] = None,
    ) -> bool:
        """Streams a live draft rich_message using Telegram Bot API 10.1+ sendRichMessageDraft."""
        url = f"https://api.telegram.org/bot{self.bot_token}/sendRichMessageDraft"
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "draft_id": draft_id,
            "rich_message": rich_message,
        }
        if thread_id:
            payload["message_thread_id"] = thread_id
        try:
            resp = requests.post(url, json=payload, timeout=6)
            return resp.status_code == 200 and resp.json().get("ok", False)
        except Exception as e:
            logger.debug("Error sending rich message draft: %s", e)
            return False

    def create_forum_topic(
        self,
        chat_id: str,
        name: str,
        icon_color: Optional[int] = None,
    ) -> Optional[int]:
        """Creates a forum topic in the given chat via Telegram Bot API createForumTopic."""
        url = f"https://api.telegram.org/bot{self.bot_token}/createForumTopic"
        payload: Dict[str, Any] = {
            "chat_id": str(chat_id).strip(),
            "name": name,
        }
        if icon_color:
            payload["icon_color"] = icon_color
        try:
            resp = requests.post(url, json=payload, timeout=8)
            if resp.status_code == 200 and resp.json().get("ok"):
                res_data = resp.json().get("result", {})
                tid = res_data.get("message_thread_id")
                logger.info("Created forum topic '%s' (thread_id=%s) in chat %s", name, tid, chat_id)
                return tid
            logger.warning("createForumTopic failed (%s): %s", resp.status_code, resp.text)
            return None
        except Exception as e:
            logger.warning("createForumTopic error: %s", e)
            return None

    def delete_forum_topic(
        self,
        chat_id: str,
        message_thread_id: int,
    ) -> bool:
        """Deletes a forum topic in the given chat via Telegram Bot API deleteForumTopic."""
        url = f"https://api.telegram.org/bot{self.bot_token}/deleteForumTopic"
        payload = {
            "chat_id": str(chat_id).strip(),
            "message_thread_id": int(message_thread_id),
        }
        try:
            resp = requests.post(url, json=payload, timeout=8)
            if resp.status_code == 200 and resp.json().get("ok"):
                logger.info("Deleted forum topic thread_id=%s in chat %s", message_thread_id, chat_id)
                return True
            logger.warning("deleteForumTopic failed (%s): %s", resp.status_code, resp.text)
            return False
        except Exception as e:
            logger.warning("deleteForumTopic error: %s", e)
            return False

    def _load_topics_cache(self) -> Dict[str, Dict[str, int]]:
        if not getattr(self, "topics_cache_file", None):
            return {}
        p = Path(self.topics_cache_file)
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception as e:
                logger.debug("Error reading topics cache from %s: %s", p, e)
        return {}

    def _save_topics_cache(self):
        if not getattr(self, "topics_cache_file", None):
            return
        p = Path(self.topics_cache_file)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp_p = p.with_suffix(".tmp")
            tmp_p.write_text(json.dumps(self.topics_cache, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp_p.replace(p)
        except Exception as e:
            logger.debug("Error writing topics cache to %s: %s", p, e)

    def get_or_create_topic(
        self,
        chat_id: str,
        topic_key: str,
        default_name: str,
        icon_color: Optional[int] = None,
        caller_thread_id: Optional[str] = None,
    ) -> Optional[str]:
        """
        Resolves or creates a Telegram forum topic for the given chat_id and logical topic_key.
        Checks environment variables first, then cache, then falls back to caller_thread_id,
        and only creates a new forum topic if TELEGRAM_AUTO_CREATE_TOPICS is enabled.
        """
        cid_str = str(chat_id).strip()
        if not cid_str:
            return None

        # 1. Environment variable override
        env_key = f"TELEGRAM_TOPIC_{topic_key.upper()}_ID"
        env_val = os.environ.get(env_key)
        if env_val and str(env_val).strip():
            return str(env_val).strip()

        # 2. Check in-memory / persisted cache
        cached_id = self.topics_cache.get(cid_str, {}).get(topic_key)
        if cached_id:
            return str(cached_id)

        # 3. If caller executed inside an active thread, bind it to this topic key to prevent duplicates
        if caller_thread_id and str(caller_thread_id).strip():
            try:
                tid_int = int(str(caller_thread_id).strip())
                self.topics_cache.setdefault(cid_str, {})[topic_key] = tid_int
                self._save_topics_cache()
                logger.info("Bound topic key '%s' to caller thread_id=%s for chat %s", topic_key, tid_int, cid_str)
                return str(tid_int)
            except ValueError:
                pass

        # 4. Only auto-create if explicitly enabled (defaults to false to prevent unwanted duplicate topics)
        auto_create = os.environ.get("TELEGRAM_AUTO_CREATE_TOPICS", "false").lower() in ("1", "true", "yes")
        if not auto_create:
            logger.debug("Auto-creation of forum topics disabled (TELEGRAM_AUTO_CREATE_TOPICS=false); topic '%s' not created", topic_key)
            return caller_thread_id

        # 5. Create topic via Telegram Bot API
        new_id = self.create_forum_topic(cid_str, default_name, icon_color=icon_color)
        if new_id:
            self.topics_cache.setdefault(cid_str, {})[topic_key] = new_id
            self._save_topics_cache()
            return str(new_id)

        return caller_thread_id

    def answer_callback_query(self, callback_query_id: str, text: Optional[str] = None, show_alert: bool = False):
        """Answers an incoming inline button callback to dismiss loading state or show alert."""
        url = f"https://api.telegram.org/bot{self.bot_token}/answerCallbackQuery"
        payload: Dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        if show_alert:
            payload["show_alert"] = True
        try:
            requests.post(url, json=payload, timeout=5)
        except Exception as e:
            logger.debug("Error answering callback query: %s", e)

    def register_commands(self):
        """Registers the slash commands with Telegram Bot API for the menu button."""
        url = f"https://api.telegram.org/bot{self.bot_token}/setMyCommands"
        commands = [
            {"command": "sync", "description": "Buscar temas faltantes y generar lotes de álbumes"},
            {"command": "buscar", "description": "Alias de /sync"},
            {"command": "descubrir", "description": "Recomendaciones personalizadas de YouTube Music"},
            {"command": "novedades", "description": "Nuevos lanzamientos y estrenos (no en biblioteca)"},
            {"command": "dl_rec", "description": "Descargar sugerencia de descubrimiento (ej. /dl_rec 1 o all)"},
            {"command": "dl_nov", "description": "Descargar sugerencia de novedades (ej. /dl_nov 1 o all)"},
            {"command": "add", "description": "Descargar URL, álbum o discografía completa de un artista"},
            {"command": "dl", "description": "Descargar lote directamente a M:\\music\\downloads para Picard"},
            {"command": "download", "description": "Alias de /dl (ej. /dl 1, /dl lote 2, /dl all)"},
            {"command": "plugins", "description": "Configurar plugins de Picard y reubicación en biblioteca"},
            {"command": "cancel", "description": "Cancelar la descarga activa en curso"},
            {"command": "descartar", "description": "Descartar canciones o álbumes de la lista de pendientes"},
            {"command": "restaurar", "description": "Restaurar canciones o álbumes descartados"},
            {"command": "inyectadas", "description": "Ver canciones encontradas y auto-inyectadas a las listas"},
            {"command": "limit", "description": "Consultar o cambiar el límite por lote (ej. /limit 20)"},
            {"command": "topic", "description": "Consultar o vincular temas (/topic descargas | anadidos)"},
            {"command": "status", "description": "Estado del notificador y playlists vigiladas"},
            {"command": "help", "description": "Ayuda y comandos disponibles"},
        ]
        try:
            requests.post(url, json={"commands": commands}, timeout=10)
        except Exception as e:
            logger.warning("Could not register bot commands: %s", e)

    def flush_old_updates(self):
        """Discards queued updates from while the bot was offline."""
        url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
        try:
            resp = requests.get(url, params={"offset": -1, "limit": 1}, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("result", [])
                if results:
                    self.last_update_id = results[-1]["update_id"]
        except Exception as e:
            logger.warning("Could not flush old Telegram updates: %s", e)

    def _check_ipc_trigger(self):
        """Checks for local trigger commands dropped into /tmp/notifier_cmd.json."""
        cmd_trigger = Path("/tmp/notifier_cmd.json")
        if cmd_trigger.is_file():
            try:
                with open(cmd_trigger, "r", encoding="utf-8") as f:
                    cmd_data = json.load(f)
                cmd_trigger.unlink(missing_ok=True)
                target_chat = cmd_data.get("chat_id")
                if not target_chat and self.authorized_chat_ids:
                    target_chat = list(self.authorized_chat_ids)[0]
                if target_chat:
                    mock_update = {
                        "message": {
                            "chat": {"id": target_chat},
                            "text": cmd_data.get("text", "/dl all"),
                            "message_thread_id": cmd_data.get("thread_id"),
                        }
                    }
                    logger.info("Triggering IPC bot command: %s (chat: %s)", cmd_data.get("text"), target_chat)
                    self.handle_update(mock_update)
            except Exception as ce:
                logger.error("Error reading command trigger %s: %s", cmd_trigger, ce)

    def poll_once(self):
        """Polls for new updates using long-polling."""
        self._check_ipc_trigger()
        url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
        params = {"offset": self.last_update_id + 1, "timeout": 20}
        try:
            resp = requests.get(url, params=params, timeout=25)
            if resp.status_code != 200:
                time.sleep(2)
                return
            data = resp.json()
            if not data.get("ok"):
                time.sleep(2)
                return

            for update in data.get("result", []):
                self.last_update_id = update["update_id"]
                self.handle_update(update)
        except requests.exceptions.Timeout:
            self._check_ipc_trigger()
        except Exception as e:
            logger.error("Error in Telegram long-polling: %s", e)
            time.sleep(3)

    def handle_update(self, update: Dict[str, Any]):
        """Delegates update processing to the dispatcher."""
        dispatch_update(self, update)

    def _start_webhook_server(self, port: int = 8085):
        """Starts a background HTTP server for browser extension and direct API commands."""
        try:
            handler_cls = type(
                "BoundTelegramBotWebhookHandler",
                (TelegramBotWebhookHandler,),
                {"listener_ref": self}
            )
            server = HTTPServer(("0.0.0.0", port), handler_cls)
            logger.info("🚀 Webhook HTTP server listening on 0.0.0.0:%d for Edge extension / API triggers", port)
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
        except Exception as e:
            logger.warning("Could not start Webhook HTTP server on port %d: %s", port, e)

    def start_polling_loop(self):
        """Starts the long-polling loop (blocking)."""
        self.running = True
        self.register_commands()
        self.flush_old_updates()
        webhook_port = int(os.environ.get("NOTIFIER_WEBHOOK_PORT", "8085"))
        self._start_webhook_server(port=webhook_port)
        logger.info("Telegram Bot Command Listener active. Listening for /sync, /buscar, /limit, /status...")
        while self.running:
            self.poll_once()

    def stop(self):
        self.running = False
