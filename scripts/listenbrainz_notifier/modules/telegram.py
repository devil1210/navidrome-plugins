#!/usr/bin/env python3
"""
Telegram communication layer: TelegramSender (Rich Messages), TelegramBotListener,
and interactive menus (Discography & Checklist).
"""

import html
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

import requests

from .models import DownloadJob, ResolutionResult, ResolvedAlbum, ResolvedTrack, TelegramTarget

logger = logging.getLogger("listenbrainz_notifier")


def build_discography_menu(
    artist: str,
    num_albums: int,
    num_singles: int,
    token: str,
) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
    """Builds menu options for an artist's discography (All, Albums, Singles, Checklist)."""
    text = (
        f"🔎 <b>Discografía de {html.escape(artist)}:</b>\n"
        f"• 💿 <b>Álbumes de estudio:</b> {num_albums}\n"
        f"• 🎵 <b>Sencillos y EPs:</b> {num_singles}\n"
        f"<i>Total disponible: {num_albums + num_singles} lanzamientos.</i>\n\n"
        f"👇 <b>Selecciona qué deseas descargar:</b>"
    )
    rich_blocks = [
        {
            "type": "heading",
            "text": f"🔎 Discografía de {artist}",
            "size": 2,
        },
        {
            "type": "list",
            "items": [
                {"blocks": [{"type": "paragraph", "text": f"💿 Álbumes de estudio: {num_albums}"}]},
                {"blocks": [{"type": "paragraph", "text": f"🎵 Sencillos y EPs: {num_singles}"}]},
                {"blocks": [{"type": "paragraph", "text": f"Total disponible: {num_albums + num_singles} lanzamientos"}]},
            ],
        },
        {
            "type": "paragraph",
            "text": "👇 Selecciona qué deseas descargar:",
        },
    ]
    if num_albums > 0:
        rich_blocks.append({
            "type": "buttons",
            "buttons": [
                {"text": f"📋 Seleccionar Álbumes (Checklist)", "callback_data": f"add_disc:check:{token}:1", "style": "primary"}
            ]
        })

    filter_row = []
    if num_albums > 0:
        filter_row.append({"text": f"💿 Solo Álbumes ({num_albums})", "callback_data": f"add_disc:albums:{token}"})
    if num_singles > 0:
        filter_row.append({"text": f"🎵 Solo Sencillos ({num_singles})", "callback_data": f"add_disc:singles:{token}"})
    if filter_row:
        rich_blocks.append({"type": "buttons", "buttons": filter_row})

    rich_blocks.append({
        "type": "buttons",
        "buttons": [
            {"text": f"⚡ Descargar Todo ({num_albums + num_singles})", "callback_data": f"add_disc:all:{token}", "style": "success"}
        ]
    })
    rich_blocks.append({
        "type": "buttons",
        "buttons": [
            {"text": "❌ Cancelar", "callback_data": f"add_disc:cancel:{token}", "style": "danger"}
        ]
    })

    # Fallback standard inline keyboard
    fallback_buttons = []
    if num_albums > 0:
        fallback_buttons.append([{"text": f"📋 Seleccionar Álbumes (Checklist)", "callback_data": f"add_disc:check:{token}:1"}])
    if filter_row:
        fallback_buttons.append(filter_row)
    fallback_buttons.append([{"text": f"⚡ Descargar Todo ({num_albums + num_singles})", "callback_data": f"add_disc:all:{token}"}])
    fallback_buttons.append([{"text": "❌ Cancelar", "callback_data": f"add_disc:cancel:{token}"}])

    return text, {"inline_keyboard": fallback_buttons}, {"blocks": rich_blocks}


def build_checklist_menu(
    sess: Dict[str, Any],
    token: str,
    page: int = 1,
) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
    """Builds an interactive checklist menu with embedded number buttons (up to 8/row)."""
    albums = sess.get("albums", [])
    selected = sess.get("selected", set())
    page_size = 16
    total_pages = max(1, (len(albums) + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))

    start = (page - 1) * page_size
    end = min(start + page_size, len(albums))
    page_items = albums[start:end]

    # Fallback HTML text
    list_lines = []
    for offset, alb in enumerate(page_items):
        idx = start + offset
        icon = "☑️" if idx in selected else "⬜"
        year_str = f" <i>({alb.year})</i>" if getattr(alb, 'year', None) else ""
        list_lines.append(f"{icon} <b>{idx+1}.</b> {html.escape(alb.album_name)}{year_str}")

    text = (
        f"📋 <b>Álbumes de {html.escape(sess['artist'])}</b>\n"
        f"• Marcados: <b>{len(selected)}</b> de {len(albums)} álbumes (Página {page}/{total_pages})\n\n"
        + "\n".join(list_lines) + "\n\n"
        f"👇 <i>Toca los botones para marcar (🔘) o desmarcar (⚪):</i>"
    )

    # Native Telegram Rich Message Checklist
    rich_items = []
    for offset, alb in enumerate(page_items):
        idx = start + offset
        is_chk = idx in selected
        year_str = f" ({alb.year})" if getattr(alb, 'year', None) else ""
        rich_items.append({
            "blocks": [{"type": "paragraph", "text": f"{idx+1}. {alb.album_name}{year_str}"}],
            "has_checkbox": True,
            "is_checked": is_chk,
        })

    rich_blocks = [
        {
            "type": "heading",
            "text": f"📋 Álbumes de {sess['artist']}",
            "size": 2,
        },
        {
            "type": "paragraph",
            "text": f"Marcados: {len(selected)} de {len(albums)} álbumes (Página {page}/{total_pages})\n"
                    f"Toca los números abajo para marcar o desmarcar:",
        },
        {
            "type": "list",
            "items": rich_items,
        },
    ]

    # 1. Embedded Number Buttons inside rich message (up to 8 per row)
    toggle_row = []
    fallback_toggle_row = []
    fallback_buttons = []
    for offset, alb in enumerate(page_items):
        idx = start + offset
        is_chk = idx in selected
        btn_label = f"🔘{idx+1}" if is_chk else f"⚪{idx+1}"
        toggle_row.append({
            "text": btn_label,
            "callback_data": f"add_disc:tog:{token}:{idx}:{page}",
            "style": "primary" if is_chk else "link",
        })
        fallback_toggle_row.append({
            "text": btn_label,
            "callback_data": f"add_disc:tog:{token}:{idx}:{page}",
        })
        if len(toggle_row) == 8:
            rich_blocks.append({"type": "buttons", "buttons": toggle_row})
            fallback_buttons.append(fallback_toggle_row)
            toggle_row = []
            fallback_toggle_row = []
    if toggle_row:
        rich_blocks.append({"type": "buttons", "buttons": toggle_row})
        fallback_buttons.append(fallback_toggle_row)

    # 2. Embedded Pagination inside rich message
    if total_pages > 1:
        nav_btns = []
        if page > 1:
            nav_btns.append({"text": "⬅️ Anterior", "callback_data": f"add_disc:check:{token}:{page-1}"})
        nav_btns.append({"text": f"📄 {page}/{total_pages}", "callback_data": f"add_disc:noop:{token}"})
        if page < total_pages:
            nav_btns.append({"text": "➡️ Siguiente", "callback_data": f"add_disc:check:{token}:{page+1}"})
        rich_blocks.append({"type": "buttons", "buttons": nav_btns})
        fallback_buttons.append(nav_btns)

    # 3. Embedded Prominent Action Button inside rich message
    rich_blocks.append({
        "type": "buttons",
        "buttons": [
            {
                "text": f"📥 Descargar Marcados ({len(selected)})",
                "callback_data": f"add_disc:dl_sel:{token}",
                "style": "success",
            }
        ],
    })
    fallback_buttons.append([{"text": f"📥 Descargar Marcados ({len(selected)})", "callback_data": f"add_disc:dl_sel:{token}"}])

    # 4. Embedded Utility Row inside rich message
    rich_blocks.append({
        "type": "buttons",
        "buttons": [
            {"text": "🔄 Invertir", "callback_data": f"add_disc:inv:{token}:{page}"},
            {"text": "⬅️ Volver", "callback_data": f"add_disc:menu:{token}"},
            {"text": "❌ Cancelar", "callback_data": f"add_disc:cancel:{token}", "style": "danger"},
        ],
    })
    fallback_buttons.append([
        {"text": "🔄 Invertir", "callback_data": f"add_disc:inv:{token}:{page}"},
        {"text": "⬅️ Volver", "callback_data": f"add_disc:menu:{token}"},
        {"text": "❌ Cancelar", "callback_data": f"add_disc:cancel:{token}"},
    ])

    return text, {"inline_keyboard": fallback_buttons}, {"blocks": rich_blocks}


PICARD_PLUGIN_NAMES: Dict[str, str] = {
    "auto_romanizer": "Auto Romanizer (Romaji)",
    "lrclib_lyrics": "LRCLib (Letras .lrc)",
    "lastfm": "Last.fm (Géneros)",
    "genre_mapper": "Genre Mapper (Mapeo Géneros)",
    "release_type": "Release Type (EP/Single)",
    "soundtrack": "Soundtrack / OST",
    "enhanced_titles": "Enhanced Titles",
    "feat_artists": "Feat. Artists",
    "hyphen_unicode": "Unicode Hyphen (-)",
    "deduplicator": "Deduplicador (Backup)",
    "library_relocation": "Reubicar Biblioteca",
    "playlist": "M3U Playlist",
    "collect_artists": "Collect Artists",
}


def build_plugins_menu(plugins_config: Dict[str, bool]) -> Tuple[str, Dict[str, Any]]:
    """Builds interactive checklist menu for Picard plugins and library relocation."""
    lines = [
        "🧩 <b>Configuración de Plugins de Picard</b>",
        "<blockquote>Activa o desactiva módulos de enriquecimiento de metadatos y reubicación automática en la biblioteca.</blockquote>",
        "",
        "<b>Estado actual de los módulos:</b>",
    ]
    for key, name in PICARD_PLUGIN_NAMES.items():
        enabled = plugins_config.get(key, False)
        status_icon = "☑️" if enabled else "⬜"
        lines.append(f"• {status_icon} <b>{name}</b>")

    lines.append("")
    lines.append("<i>Toca un botón para activar/desactivar en tiempo real:</i>")

    inline_keyboard = []
    row = []
    for key, full_name in PICARD_PLUGIN_NAMES.items():
        enabled = plugins_config.get(key, False)
        icon = "☑️" if enabled else "⬜"
        short_label = full_name.split(" (")[0]
        row.append({
            "text": f"{icon} {short_label}",
            "callback_data": f"pcfg:{key}",
        })
        if len(row) == 2:
            inline_keyboard.append(row)
            row = []
    if row:
        inline_keyboard.append(row)

    # Controls row
    inline_keyboard.append([
        {"text": "🔄 Invertir", "callback_data": "pcfg:invert"},
        {"text": "💾 Guardar / Cerrar", "callback_data": "pcfg:close"},
    ])

    return "\n".join(lines), {"inline_keyboard": inline_keyboard}


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
        on_discard_command: Optional[Callable[[str, Optional[str], str], None]] = None,
        on_restore_command: Optional[Callable[[str, Optional[str], str], None]] = None,
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
        self.on_discard_command = on_discard_command
        self.on_restore_command = on_restore_command
        self.state_tracker = state_tracker
        self.download_worker: Optional[Any] = None
        self.last_update_id = 0
        self.running = False

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
                else:
                    logger.warning("sendRichMessage failed (%s), falling back to sendMessage", resp.text)
            except Exception as e:
                logger.warning("sendRichMessage error (%s), falling back to sendMessage", e)

        if photo_url:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendPhoto"
            payload: Dict[str, Any] = {
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
                else:
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

        # Standard HTML/Markdown edit fallback
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
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
            {"command": "limit", "description": "Consultar o cambiar el límite por lote (ej. /limit 20)"},
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
        """Processes a single incoming Telegram update."""
        # 1. Handle interactive inline button clicks (callback_query)
        callback_query = update.get("callback_query")
        if callback_query:
            cq_id = str(callback_query.get("id"))
            cq_msg = callback_query.get("message") or {}
            cq_chat = cq_msg.get("chat") or {}
            cq_chat_id = str(cq_chat.get("id", ""))
            cq_thread_id = str(cq_msg.get("message_thread_id")) if cq_msg.get("message_thread_id") else None
            cq_data = callback_query.get("data", "")

            if self.authorized_chat_ids and cq_chat_id not in self.authorized_chat_ids:
                logger.warning("Ignored unauthorized callback_query from %s", cq_chat_id)
                return

            if cq_data.startswith("dl_rec:"):
                arg = cq_data.split(":", 1)[1]
                self.answer_callback_query(cq_id, text=f"⏳ Descargando sugerencia #{arg}...")
                if self.on_download_rec_command:
                    self.on_download_rec_command(cq_chat_id, cq_thread_id, arg)
            elif cq_data.startswith("dl_nov:"):
                arg = cq_data.split(":", 1)[1]
                self.answer_callback_query(cq_id, text=f"⏳ Descargando novedad #{arg}...")
                if self.on_download_nov_command:
                    self.on_download_nov_command(cq_chat_id, cq_thread_id, arg)
            elif cq_data.startswith("add_disc:"):
                cq_msg_id = cq_msg.get("message_id", 0)
                if self.on_add_disc_callback:
                    self.on_add_disc_callback(cq_chat_id, cq_thread_id, cq_msg_id, cq_id, cq_data)
            elif cq_data.startswith("pcfg:"):
                arg = cq_data.split(":", 1)[1]
                cq_msg_id = cq_msg.get("message_id", 0)
                if not self.state_tracker:
                    self.answer_callback_query(cq_id, text="⚠️ Gestor de estado no disponible.")
                    return

                if arg == "close":
                    self.answer_callback_query(cq_id, text="💾 Configuración guardada")
                    self.edit_message_reply_markup(cq_chat_id, cq_msg_id, reply_markup=None)
                elif arg == "invert":
                    plugins = self.state_tracker.get_picard_plugins()
                    for k in plugins:
                        self.state_tracker.toggle_picard_plugin(k)
                    updated = self.state_tracker.get_picard_plugins()
                    _, markup = build_plugins_menu(updated)
                    self.edit_message_reply_markup(cq_chat_id, cq_msg_id, reply_markup=markup)
                    self.answer_callback_query(cq_id, text="🔄 Plugins invertidos")
                else:
                    new_val = self.state_tracker.toggle_picard_plugin(arg)
                    updated = self.state_tracker.get_picard_plugins()
                    _, markup = build_plugins_menu(updated)
                    self.edit_message_reply_markup(cq_chat_id, cq_msg_id, reply_markup=markup)
                    status_str = "Activado" if new_val else "Desactivado"
                    label = PICARD_PLUGIN_NAMES.get(arg, arg).split(" (")[0]
                    self.answer_callback_query(cq_id, text=f"{label}: {status_str}")
            return

        # 2. Handle standard slash commands
        message = update.get("message")
        if not message:
            return

        chat = message.get("chat", {})
        chat_id = str(chat.get("id"))
        thread_id = str(message.get("message_thread_id")) if message.get("message_thread_id") else None
        text = message.get("text", "").strip()

        if not text.startswith("/"):
            return

        # Security check: only accept commands from authorized chats
        if self.authorized_chat_ids and chat_id not in self.authorized_chat_ids:
            logger.warning("Ignored unauthorized command from chat %s: %s", chat_id, text)
            return

        parts = text.split(maxsplit=1)
        command = parts[0].lower().split("@")[0]  # Strip @botname if called in a group
        args = parts[1].strip() if len(parts) > 1 else ""

        logger.info("Received bot command '%s' (args: '%s') from chat %s", command, args, chat_id)

        if command in ("/sync", "/buscar", "/check", "/listenbrainz"):
            self.send_reply(chat_id, "🔍 <i>Consultando Navidrome y resolviendo álbumes en YouTube...</i>", thread_id)
            try:
                self.on_sync_command(chat_id, thread_id, args or None)
            except Exception as e:
                logger.error("Error executing sync command: %s", e, exc_info=True)
                self.send_reply(chat_id, f"❌ <b>Error durante la sincronización:</b> {e}", thread_id)

        elif command in ("/add", "/agregar", "/bajar_artista", "/bajar_album"):
            if self.on_add_command:
                if not args:
                    help_add = (
                        "ℹ️ <b>Uso del comando /add:</b>\n\n"
                        "• <code>/add &lt;URL de YouTube o YouTube Music&gt;</code>\n"
                        "• <code>/add artista &lt;Nombre del Artista&gt;</code> (descarga todos los álbumes y sencillos)\n"
                        "• <code>/add album &lt;Nombre del Álbum&gt;</code> (descarga un álbum específico)\n"
                        "• <code>/add cancion &lt;Nombre de la Canción&gt;</code> (descarga una pista suelta)\n"
                        "• <code>/add &lt;Nombre libre&gt;</code> (autodetecta artista o álbum automáticamente)\n\n"
                        "<i>Ejemplos:</i>\n"
                        "• <code>/add artista Daft Punk</code>\n"
                        "• <code>/add album Random Access Memories</code>\n"
                        "• <code>/add https://music.youtube.com/playlist?list=...</code>"
                    )
                    self.send_reply(chat_id, help_add, thread_id)
                else:
                    # Run resolution in thread so long-polling never blocks
                    threading.Thread(
                        target=self.on_add_command,
                        args=(chat_id, thread_id, args),
                        daemon=True,
                    ).start()
            else:
                self.send_reply(chat_id, "⚠️ El comando /add no está disponible en este entorno.", thread_id)

        elif command in ("/download", "/dl", "/descargar", "/bajar"):
            if self.on_download_command:
                try:
                    self.on_download_command(chat_id, thread_id, args or None)
                except Exception as e:
                    logger.error("Error executing download command: %s", e, exc_info=True)
                    self.send_reply(chat_id, f"❌ <b>Error durante la descarga:</b> {e}", thread_id)
            else:
                self.send_reply(chat_id, "⚠️ El motor de descarga directa no está disponible en este entorno.", thread_id)

        elif command in ("/descubrir", "/discover", "/recomendar", "/recomendaciones"):
            if self.on_discover_command:
                threading.Thread(
                    target=self.on_discover_command,
                    args=(chat_id, thread_id, args or None),
                    daemon=True,
                ).start()
            else:
                self.send_reply(chat_id, "⚠️ El motor de recomendaciones no está disponible en este entorno.", thread_id)

        elif command in ("/novedades", "/estrenos", "/new", "/releases"):
            if self.on_new_releases_command:
                threading.Thread(
                    target=self.on_new_releases_command,
                    args=(chat_id, thread_id, args or None),
                    daemon=True,
                ).start()
            else:
                self.send_reply(chat_id, "⚠️ El motor de novedades no está disponible en este entorno.", thread_id)

        elif command in ("/dl_rec", "/bajar_rec", "/dlrec", "/download_rec"):
            if self.on_download_rec_command:
                try:
                    self.on_download_rec_command(chat_id, thread_id, args or None)
                except Exception as e:
                    logger.error("Error executing download rec command: %s", e, exc_info=True)
                    self.send_reply(chat_id, f"❌ <b>Error al programar la descarga:</b> {e}", thread_id)
            else:
                self.send_reply(chat_id, "⚠️ El motor de descarga de recomendaciones no está disponible.", thread_id)

        elif command in ("/dl_nov", "/bajar_nov", "/dlnov", "/download_nov", "/dl_nuevo", "/dl_novedad"):
            if self.on_download_nov_command:
                try:
                    self.on_download_nov_command(chat_id, thread_id, args or None)
                except Exception as e:
                    logger.error("Error executing download nov command: %s", e, exc_info=True)
                    self.send_reply(chat_id, f"❌ <b>Error al programar la descarga:</b> {e}", thread_id)
            else:
                self.send_reply(chat_id, "⚠️ El motor de descarga de novedades no está disponible.", thread_id)

        elif command in ("/plugins", "/picard", "/modulos", "/ajustes"):
            if self.state_tracker:
                plugins = self.state_tracker.get_picard_plugins()
                menu_text, markup = build_plugins_menu(plugins)
                self.send_reply(chat_id, menu_text, thread_id, reply_markup=markup)
            else:
                self.send_reply(chat_id, "⚠️ El gestor de estado de plugins no está disponible.", thread_id)

        elif command in ("/limit", "/limite", "/max", "/cantidad"):
            if self.on_limit_command:
                reply_text = self.on_limit_command(args)
            else:
                reply_text = "⚙️ Comando de límite no disponible."
            self.send_reply(chat_id, reply_text, thread_id)

        elif command == "/status":
            status_text = self.on_status_command()
            self.send_reply(chat_id, status_text, thread_id)

        elif command in ("/cancel", "/cancelar", "/abort", "/detener"):
            if self.download_worker:
                success, msg = self.download_worker.cancel_active()
                if success:
                    self.send_reply(
                        chat_id,
                        f"🛑 <b>Cancelando descarga en curso:</b>\n• {html.escape(msg)}\n<i>Se detendrán los siguientes elementos de la cola.</i>",
                        thread_id,
                    )
                else:
                    self.send_reply(chat_id, "ℹ️ <i>No hay ninguna descarga activa en curso.</i>", thread_id)
            else:
                self.send_reply(chat_id, "⚠️ El motor de descargas no está disponible.", thread_id)

        elif command in ("/pause", "/pausar", "/detener_cola"):
            if self.download_worker:
                success, msg = self.download_worker.pause()
                self.send_reply(chat_id, msg, thread_id)
            else:
                self.send_reply(chat_id, "⚠️ El motor de descargas no está disponible.", thread_id)

        elif command in ("/resume", "/reanudar", "/continuar", "/seguir"):
            if self.download_worker:
                success, msg = self.download_worker.resume()
                self.send_reply(chat_id, msg, thread_id)
            else:
                self.send_reply(chat_id, "⚠️ El motor de descargas no está disponible.", thread_id)

        elif command in ("/descartar", "/ignorar", "/ignore", "/discard"):
            if self.on_discard_command:
                try:
                    self.on_discard_command(chat_id, thread_id, args)
                except Exception as e:
                    logger.error("Error executing discard command: %s", e, exc_info=True)
                    self.send_reply(chat_id, f"❌ <b>Error al descartar:</b> {e}", thread_id)
            else:
                self.send_reply(chat_id, "⚠️ El comando /descartar no está disponible.", thread_id)

        elif command in ("/restaurar", "/restore", "/unignore"):
            if self.on_restore_command:
                try:
                    self.on_restore_command(chat_id, thread_id, args)
                except Exception as e:
                    logger.error("Error executing restore command: %s", e, exc_info=True)
                    self.send_reply(chat_id, f"❌ <b>Error al restaurar:</b> {e}", thread_id)
            else:
                self.send_reply(chat_id, "⚠️ El comando /restaurar no está disponible.", thread_id)

        elif command in ("/help", "/start"):
            help_text = (
                "🤖 <b>ListenBrainz Navidrome Notifier Bot</b>\n\n"
                "<b>Comandos disponibles:</b>\n"
                "• <code>/sync</code> o <code>/buscar</code> — Sincroniza y lista álbumes faltantes (Lote 1 prioritario).\n"
                "• <code>/sync [Nombre] lote [N]</code> — Consulta un lote específico (ej. <code>/sync Descubrimiento Diario lote 2</code>).\n"
                "• <code>/descubrir</code> — Recomendaciones personalizadas de YouTube Music (no en biblioteca).\n"
                "• <code>/dl_rec [N | all]</code> — Descarga sugerencia de descubrimiento (ej. <code>/dl_rec 1</code> o <code>/dl_rec all</code>).\n"
                "• <code>/novedades</code> — Nuevos lanzamientos y estrenos de artistas seguidos o sugeridos.\n"
                "• <code>/dl_nov [N | all]</code> — Descarga novedad/estreno sugerido (ej. <code>/dl_nov 1</code> o <code>/dl_nov all</code>).\n"
                "• <code>/add [URL | artista | album]</code> — Descarga directa bajo demanda de listas, álbumes o discografías completas.\n"
                "• <code>/dl</code> o <code>/download</code> — Descarga el lote actual directamente al servidor (<code>M:\\music\\downloads</code>) listo para Picard.\n"
                "• <code>/dl [N]</code> — Descarga el lote N directamente (ej. <code>/dl 2</code>).\n"
                "• <code>/dl all</code> — Descarga todos los lotes secuencialmente con pausas de seguridad.\n"
                "• <code>/cancel</code> — Cancela la descarga activa en curso inmediatamente.\n"
                "• <code>/descartar [N | nombre | lista]</code> — Descarta temas/álbumes para que no vuelvan a aparecer en pendientes.\n"
                "• <code>/restaurar [nombre | N]</code> — Quita un tema o álbum de la lista de descartados.\n"
                "• <code>/plugins</code> o <code>/picard</code> — Configurar y alternar plugins de Picard y reubicación automática.\n"
                "• <code>/limit [N]</code> — Establece la cantidad de álbumes por lote (default: 20, 0 = sin límite).\n"
                "• <code>/status</code> — Muestra el estado del notificador y playlists monitoreadas.\n"
                "• <code>/help</code> — Muestra este menú de ayuda.\n"
            )
            self.send_reply(chat_id, help_text, thread_id)

    def start_polling_loop(self):
        """Starts the long-polling loop (blocking)."""
        self.running = True
        self.register_commands()
        self.flush_old_updates()
        logger.info("Telegram Bot Command Listener active. Listening for /sync, /buscar, /limit, /status...")
        while self.running:
            self.poll_once()

    def stop(self):
        self.running = False
