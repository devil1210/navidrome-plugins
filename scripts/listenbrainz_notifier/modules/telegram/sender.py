#!/usr/bin/env python3
"""
TelegramSender: Formats and transmits Rich Messages and documents to Telegram.
"""

import html
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

import requests

from ..models import ResolutionResult, TelegramTarget

logger = logging.getLogger("listenbrainz_notifier")


class TelegramSender:
    """Formats and sends messages to Telegram with MediaHuman-ready quotes."""

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        thread_id: Optional[str] = None,
        quote_style: str = "blockquote",
    ):
        self.bot_token = bot_token
        self.raw_chat_id = chat_id
        self.global_thread_id = thread_id
        self.quote_style = quote_style.lower().strip()
        self.targets: List[TelegramTarget] = self._parse_targets(chat_id, thread_id)

    @staticmethod
    def _parse_targets(raw_chat_id: str, default_thread_id: Optional[str]) -> List[TelegramTarget]:
        targets = []
        if not raw_chat_id:
            return targets

        for part in str(raw_chat_id).split(","):
            part = part.strip()
            if not part:
                continue
            if ":" in part:
                cid, tid = part.split(":", 1)
                targets.append(TelegramTarget(chat_id=cid.strip(), thread_id=tid.strip() or None))
            else:
                targets.append(TelegramTarget(chat_id=part, thread_id=default_thread_id))
        return targets

    @staticmethod
    def _escape_html(text: str) -> str:
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    def format_rich_html(
        self,
        playlist_name: str,
        result: ResolutionResult,
        total_searched: int = 0,
    ) -> List[str]:
        """
        Builds native Telegram Rich Message HTML with <table>, <details>, <summary>,
        <blockquote>, and <ul> lists.
        """
        safe_playlist_name = self._escape_html(playlist_name)
        total_links = result.total_links
        searched_count = total_searched if total_searched > 0 else total_links

        if result.total_batches > 1:
            header = (
                f"<h1>🎧 ListenBrainz — {safe_playlist_name}</h1>"
                f"<i>Canciones no encontradas en tu biblioteca de Navidrome</i>"
                f"<table bordered striped>"
                f"<tr><td><b>🔍 Analizadas</b></td><td>{searched_count}</td></tr>"
                f"<tr><td><b>💿 Total Álbumes</b></td><td>{result.total_albums_detected}</td></tr>"
                f"<tr><td><b>📦 Lote Actual</b></td><td><b>{result.batch_number} de {result.total_batches}</b> (Top {len(result.albums)})</td></tr>"
                f"<tr><td><b>🔗 Enlaces en Lote</b></td><td>{total_links}</td></tr>"
                f"</table>"
            )
            urls_section = (
                f"<details>"
                f"<summary>📥 Enlaces MediaHuman ({total_links} en Lote {result.batch_number})</summary>"
                f'<pre><code class="language-copy">{"\n".join(result.all_urls)}</code></pre>'
                f"</details>"
            )
            detail_items = []
            for alb in result.albums:
                safe_artist = self._escape_html(alb.artist_name)
                safe_album = self._escape_html(alb.album_name)
                track_count = len(alb.tracks)
                sample_tracks = ", ".join([f"<i>{self._escape_html(t)}</i>" for t in alb.tracks[:3]])
                if track_count > 3:
                    sample_tracks += f" y {track_count - 3} más..."
                if getattr(alb, "is_partial", False):
                    detail_items.append(f"<li><b>{safe_artist}</b> — <b>{safe_album}</b> <i>(⚠️ Álbum parcial en biblioteca: falta {sample_tracks})</i></li>")
                else:
                    detail_items.append(f"<li><b>{safe_artist}</b> — <b>{safe_album}</b> ({sample_tracks})</li>")

            for trk in result.tracks:
                safe_artist = self._escape_html(trk.artist)
                safe_title = self._escape_html(trk.title)
                detail_items.append(f"<li><b>{safe_artist}</b> — <i>{safe_title}</i> [Single]</li>")

            if result.unresolved:
                detail_items.append(f"<li>⚠️ <i>{len(result.unresolved)} tema(s) no encontrados en YouTube</i></li>")

            details_html = (
                f"<details>"
                f"<summary>📋 Detalle Álbumes (Lote {result.batch_number} de {result.total_batches})</summary>"
                f"<ul>" + "".join(detail_items) + "</ul>"
                f"</details>"
            )

            if result.batch_number < result.total_batches:
                batch_footer = (
                    f"<blockquote>"
                    f"💡 <b>Lote {result.batch_number} de {result.total_batches}</b> ({len(result.albums)} álbumes prioritarios con más temas).<br>"
                    f"Para ver el siguiente lote: <code>/sync {safe_playlist_name} lote {result.batch_number + 1}</code><br>"
                    f"Para cambiar la cantidad: <code>/limit [número]</code>"
                    f"</blockquote>"
                )
            else:
                batch_footer = (
                    f"<blockquote>"
                    f"🎉 <b>¡Último lote ({result.batch_number} de {result.total_batches})!</b> Todos los álbumes han sido procesados."
                    f"</blockquote>"
                )
            full_html = header + urls_section + details_html + batch_footer
            return [full_html]

        header = (
            f"<h1>🎧 ListenBrainz — {safe_playlist_name}</h1>"
            f"<i>Canciones no encontradas en tu biblioteca de Navidrome</i>"
            f"<table bordered striped>"
            f"<tr><td><b>🔍 Analizadas</b></td><td>{searched_count}</td></tr>"
            f"<tr><td><b>💿 Álbumes</b></td><td>{len(result.albums)}</td></tr>"
            f"<tr><td><b>🎵 Singles</b></td><td>{len(result.tracks)}</td></tr>"
            f"<tr><td><b>🔗 Total Enlaces</b></td><td>{total_links}</td></tr>"
            f"</table>"
        )

        urls_block = "\n".join(result.all_urls)
        urls_section = (
            f"<details>"
            f"<summary>📥 Enlaces para MediaHuman ({total_links})</summary>"
            f'<pre><code class="language-copy">{urls_block}</code></pre>'
            f"</details>"
        )

        detail_items = []
        for alb in result.albums:
            safe_artist = self._escape_html(alb.artist_name)
            safe_album = self._escape_html(alb.album_name)
            track_count = len(alb.tracks)
            sample_tracks = ", ".join([f"<i>{self._escape_html(t)}</i>" for t in alb.tracks[:3]])
            if getattr(alb, "is_partial", False):
                detail_items.append(f"<li><b>{safe_artist}</b> — <b>{safe_album}</b> <i>(⚠️ Álbum parcial en biblioteca: falta {sample_tracks})</i></li>")
            else:
                detail_items.append(f"<li><b>{safe_artist}</b> — <b>{safe_album}</b> ({sample_tracks})</li>")

        for trk in result.tracks:
            safe_artist = self._escape_html(trk.artist)
            safe_title = self._escape_html(trk.title)
            detail_items.append(f"<li><b>{safe_artist}</b> — <i>{safe_title}</i> [Single]</li>")

        if result.unresolved:
            detail_items.append(f"<li>⚠️ <i>{len(result.unresolved)} tema(s) no encontrados en YouTube</i></li>")

        details_html = (
            f"<details>"
            f"<summary>📋 Ver Detalle de Álbumes ({len(result.albums)})</summary>"
            f"<ul>" + "".join(detail_items) + "</ul>"
            f"</details>"
        )

        full_html = header + urls_section + details_html
        return [full_html]

    def send_document(
        self,
        chat_id: str,
        thread_id: Optional[str],
        file_name: str,
        content: str,
        caption: str = "",
    ) -> bool:
        """Sends a text file attachment to Telegram."""
        url = f"https://api.telegram.org/bot{self.bot_token}/sendDocument"
        data: Dict[str, Any] = {
            "chat_id": chat_id,
            "caption": caption,
            "parse_mode": "HTML",
        }
        if thread_id:
            data["message_thread_id"] = thread_id

        try:
            resp = requests.post(url, data=data, files={"document": (file_name, content.encode("utf-8"), "text/plain")}, timeout=20)
            resp.raise_for_status()
            res_json = resp.json()
            return res_json.get("ok", False)
        except Exception as e:
            logger.error("Failed to send document attachment to chat %s: %s", chat_id, e)
            return False

    def send_notification(
        self,
        playlist_name: str,
        result: ResolutionResult,
        total_searched: int = 0,
        send_file: bool = False,
    ) -> bool:
        """Sends the formatted rich notification to Telegram using sendRichMessage."""
        if not self.bot_token or not self.targets:
            logger.error("Cannot send Telegram notification: Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
            return False

        if result.total_links == 0:
            logger.info("No resolved links to send for playlist '%s'.", playlist_name)
            return True

        messages = self.format_rich_html(playlist_name, result, total_searched=total_searched)
        rich_url = f"https://api.telegram.org/bot{self.bot_token}/sendRichMessage"
        fallback_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

        all_success = True
        urls_file_content = "\n".join(result.all_urls) + "\n"
        clean_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', playlist_name)
        batch_suffix = f"_Lote_{result.batch_number}" if result.total_batches > 1 else ""
        file_name = f"{clean_name}{batch_suffix}_MediaHuman.txt"

        for target in self.targets:
            for idx, html_chunk in enumerate(messages, 1):
                # Try sendRichMessage first
                rich_payload = {
                    "chat_id": target.chat_id,
                    "rich_message": json.dumps({"html": html_chunk}),
                }
                if target.thread_id:
                    rich_payload["message_thread_id"] = target.thread_id

                try:
                    resp = requests.post(rich_url, data=rich_payload, timeout=15)
                    resp.raise_for_status()
                    data = resp.json()
                    if not data.get("ok"):
                        raise Exception(f"sendRichMessage error: {data.get('description')}")
                    logger.info("Telegram RichMessage [%d/%d] sent successfully to chat %s!", idx, len(messages), target.chat_id)
                except Exception as e:
                    logger.warning("sendRichMessage failed (%s). Falling back to sendMessage...", e)
                    # Fallback to standard sendMessage using expandable blockquote
                    batch_title = f" (Lote {result.batch_number}/{result.total_batches})" if result.total_batches > 1 else ""
                    fallback_text = (
                        f"🎧 <b>ListenBrainz — {self._escape_html(playlist_name)}{batch_title}</b>\n\n"
                        f"<blockquote expandable>\n" + "\n".join(result.all_urls) + "\n</blockquote>"
                    )
                    fallback_payload = {
                        "chat_id": target.chat_id,
                        "text": fallback_text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    }
                    if target.thread_id:
                        fallback_payload["message_thread_id"] = target.thread_id
                    try:
                        f_resp = requests.post(fallback_url, json=fallback_payload, timeout=15)
                        f_resp.raise_for_status()
                        logger.info("Telegram fallback message sent successfully to chat %s!", target.chat_id)
                    except Exception as fe:
                        logger.error("Failed to send fallback message to chat %s: %s", target.chat_id, fe)
                        all_success = False

                time.sleep(0.5)

            # Only send file attachment if explicitly requested
            if send_file:
                doc_caption = f"📄 <b>{self._escape_html(playlist_name)}</b> — {result.total_links} enlaces listos para MediaHuman"
                if result.total_batches > 1:
                    doc_caption += f" (Lote {result.batch_number}/{result.total_batches})"
                doc_ok = self.send_document(
                    chat_id=target.chat_id,
                    thread_id=target.thread_id,
                    file_name=file_name,
                    content=urls_file_content,
                    caption=doc_caption,
                )
                if doc_ok:
                    logger.info("Attached MediaHuman .txt file sent successfully to chat %s!", target.chat_id)

        return all_success
