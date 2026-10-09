#!/usr/bin/env python3
"""
Sync & Injected Tracks Handlers:
Handles /sync, /buscar, /inyectadas, and builds rich summary messages for synchronized batches.
"""

import html
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    from ..modules.resolver import parse_sync_args
    from ..modules.syncer import run_sync
    from ..modules.telegram import TelegramSender
except (ImportError, ValueError):
    from modules.resolver import parse_sync_args
    from modules.syncer import run_sync
    from modules.telegram import TelegramSender

logger = logging.getLogger("listenbrainz_notifier")


def format_injected_feedback(
    injected_map: Dict[str, List[Dict[str, Any]]],
    max_per_playlist: int = 25,
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Builds both HTML section and rich_message blocks displaying tracks auto-injected into Navidrome.
    """
    def _norm(s: str) -> str:
        return re.sub(r"[^\w\s]", "", (s or "").lower()).strip()

    if not injected_map:
        return "", []

    total_inj = sum(len(trks) for trks in injected_map.values() if trks)
    if total_inj == 0:
        return "", []

    html_sections = [f"📥 <b>Canciones agregadas a tus listas ({total_inj}):</b>"]
    rich_sections: List[Dict[str, Any]] = [
        {
            "type": "heading",
            "text": f"📥 Canciones agregadas a tus listas ({total_inj})",
            "size": 3,
        }
    ]

    for pls_name, trks in injected_map.items():
        if not trks:
            continue
        safe_pls = html.escape(pls_name)
        cnt = len(trks)
        html_sections.append(f"\n<b>📋 {safe_pls} ({cnt}):</b>")
        rich_sections.append({
            "type": "paragraph",
            "text": f"📋 {pls_name} ({cnt}):",
        })

        list_items = []
        shown = trks[:max_per_playlist]
        for t in shown:
            req = t.get("requested", "").strip()
            art = t.get("artist", "").strip()
            tit = t.get("title", "").strip()
            alb = t.get("album", "").strip()

            art_html = f"<b>{html.escape(art)}</b> — " if art else ""
            tit_html = html.escape(tit)
            alb_html = f" <i>[{html.escape(alb)}]</i>" if alb else ""

            note_html = ""
            if req and _norm(req) != _norm(tit):
                note_html = f" <i>(coincide con: «{html.escape(req)}»)</i>"

            html_sections.append(f"• {art_html}{tit_html}{alb_html}{note_html}")

            art_txt = f"{art} — " if art else ""
            alb_txt = f" [{alb}]" if alb else ""
            note_txt = f" (coincide con: '{req}')" if req and _norm(req) != _norm(tit) else ""
            list_items.append({
                "blocks": [{"type": "paragraph", "text": f"🎵 {art_txt}{tit}{alb_txt}{note_txt}"}]
            })

        if cnt > max_per_playlist:
            extra = cnt - max_per_playlist
            html_sections.append(f"• <i>... y {extra} canciones más agregadas con éxito.</i>")
            list_items.append({
                "blocks": [{"type": "paragraph", "text": f"➕ ... y {extra} canciones más agregadas con éxito."}]
            })

        rich_sections.append({
            "type": "list",
            "items": list_items,
        })

    return "\n".join(html_sections), rich_sections


def format_reordered_feedback(reorder_map: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Builds both HTML section and rich blocks displaying playlist canonical order status.
    """
    if not reorder_map:
        return "", []

    html_lines = ["\n🔄 <b>Estado del orden oficial (ListenBrainz):</b>"]
    list_items = []
    for pls_name, info in reorder_map.items():
        count = info.get("track_count", 0)
        off_count = info.get("official_count", 0)
        safe_name = html.escape(pls_name)
        if info.get("was_reordered"):
            html_lines.append(f"• <b>{safe_name}</b>: 🔀 Se reordenaron {count} canciones según la lista oficial.")
            list_items.append({
                "blocks": [{"type": "paragraph", "text": f"🔀 {pls_name}: Se reordenaron {count} canciones según la lista oficial."}]
            })
        else:
            cnt_str = f"{count}/{off_count}" if off_count > 0 else f"{count}"
            html_lines.append(f"• <b>{safe_name}</b>: ✅ Orden oficial verificado ({cnt_str} canciones en orden canónico).")
            list_items.append({
                "blocks": [{"type": "paragraph", "text": f"✅ {pls_name}: Orden oficial verificado ({cnt_str} canciones en orden canónico)."}]
            })

    rich_blocks = [
        {
            "type": "heading",
            "text": "🔄 Estado del orden oficial (ListenBrainz)",
            "size": 3,
        },
        {
            "type": "list",
            "items": list_items,
        }
    ]
    return "\n".join(html_lines), rich_blocks


class SyncHandler:
    """Handles playlist synchronization (/sync) and local library injection reporting (/inyectadas)."""

    def __init__(
        self,
        navidrome: Any,
        state_tracker: Any,
        target_names: Set[str],
        default_max_albums: int,
        fallback_to_track: bool,
        enricher: Any,
        tg_token: str,
        tg_style: str = "blockquote",
    ):
        self.navidrome = navidrome
        self.state_tracker = state_tracker
        self.target_names = target_names
        self.default_max_albums = default_max_albums
        self.fallback_to_track = fallback_to_track
        self.enricher = enricher
        self.tg_token = tg_token
        self.tg_style = tg_style
        self.listener: Optional[Any] = None

    def set_listener(self, listener: Any):
        self.listener = listener

    def on_sync_command(self, chat_id: str, thread_id: Optional[str], raw_args: Optional[str] = None):
        if not self.listener:
            return

        current_limit = self.state_tracker.get_limit(default_limit=self.default_max_albums)
        target_pls, batch_num, batch_size = parse_sync_args(raw_args, default_limit=current_limit)
        query_names = {target_pls} if target_pls else self.target_names
        logger.info(
            "Bot /sync triggered for playlists: %s from chat %s (thread: %s) [Lote: %d, Limit: %d]",
            query_names, chat_id, thread_id, batch_num, batch_size
        )
        cmd_target = f"{chat_id}:{thread_id}" if thread_id else chat_id
        cmd_telegram = TelegramSender(
            bot_token=self.tg_token,
            chat_id=cmd_target,
            thread_id=thread_id,
            quote_style=self.tg_style,
        )
        injected_details: Dict[str, List[Dict[str, Any]]] = {}
        reorder_details: Dict[str, Any] = {}
        found_count, processed_count = run_sync(
            navidrome=self.navidrome,
            telegram=cmd_telegram,
            state_tracker=self.state_tracker,
            target_names=query_names,
            fallback_to_track=self.fallback_to_track,
            force=True,
            dry_run=False,
            enricher=self.enricher,
            batch_number=batch_num,
            batch_size=batch_size,
            injected_accumulator=injected_details,
            reorder_accumulator=reorder_details,
        )
        if found_count == 0:
            self.listener.send_reply(
                chat_id,
                f"⚠️ No se encontró ninguna playlist en Navidrome que coincida con: <code>{', '.join(query_names)}</code>",
                thread_id,
            )
        elif processed_count == 0:
            order_html, order_blocks = format_reordered_feedback(reorder_details)
            msg = f"✅ <b>Todo al día:</b> La playlist <code>{', '.join(query_names)}</code> no tiene canciones faltantes registradas en sus comentarios."
            if order_html:
                msg += f"\n{order_html}"
            rich_msg = None
            if order_blocks:
                rich_msg = {
                    "blocks": [
                        {
                            "type": "heading",
                            "text": "✅ Todo al día",
                            "size": 2,
                        },
                        {
                            "type": "paragraph",
                            "text": f"La playlist {', '.join(query_names)} no tiene canciones faltantes registradas en sus comentarios.",
                        },
                        *order_blocks,
                    ]
                }
            self.listener.send_reply(
                chat_id,
                msg,
                thread_id,
                rich_message=rich_msg,
            )
        else:
            if target_pls:
                last_batch = self.state_tracker.get_last_batch(target_pls)
            else:
                last_batch = self.state_tracker.get_last_batch()
            remaining_albums = len(last_batch.get("albums", [])) if last_batch else 0
            remaining_tracks = len(last_batch.get("tracks", [])) if last_batch else 0
            tot_rem = remaining_albums + remaining_tracks
            tot_batches = last_batch.get("total_batches", 1) if last_batch else 1
            pls_label = f"{processed_count} playlists analizadas" if processed_count > 1 else f"{processed_count} playlist analizada"

            inj_html, inj_blocks = format_injected_feedback(injected_details)
            order_html, order_blocks = format_reordered_feedback(reorder_details)
            total_injected = sum(len(trks) for trks in injected_details.values() if trks)

            if tot_rem == 0:
                if total_injected > 0:
                    status_line = (
                        f"• Se auto-inyectaron <b>{total_injected} canciones</b> existentes en tu biblioteca local a Navidrome.\n"
                        f"• <i>No quedan álbumes ni temas pendientes por descargar.</i>"
                    )
                    para_text = f"Se encontraron y auto-inyectaron {total_injected} canciones en tus listas desde tu biblioteca local. No quedan pendientes por descargar."
                else:
                    status_line = (
                        f"• Todas las canciones faltantes ya se encontraban en tu biblioteca local y fueron auto-inyectadas a Navidrome.\n"
                        f"• <i>No quedan álbumes ni temas pendientes por descargar.</i>"
                    )
                    para_text = "Todas las canciones faltantes ya se encontraban en tu biblioteca local y fueron auto-inyectadas a Navidrome. No quedan álbumes ni temas pendientes por descargar."

                summary_parts = [
                    f"✅ <b>Sincronización completada ({pls_label}):</b>\n{status_line}"
                ]
                if inj_html:
                    summary_parts.append(inj_html)
                if order_html:
                    summary_parts.append(order_html)

                summary_text = "\n\n".join(summary_parts)
                if len(summary_text) > 3800:
                    summary_text = summary_text[:3700] + "\n\n<i>... [Lista truncada por límite de Telegram. Usa /inyectadas para ver el registro]</i>"

                rich_blocks = [
                    {
                        "type": "heading",
                        "text": f"✅ Sincronización completada ({pls_label})",
                        "size": 2,
                    },
                    {
                        "type": "paragraph",
                        "text": para_text,
                    },
                ]
                if inj_blocks:
                    rich_blocks.extend(inj_blocks)
                if order_blocks:
                    rich_blocks.extend(order_blocks)

                rich_summary = {"blocks": rich_blocks}
                self.listener.send_reply(chat_id, summary_text, thread_id, rich_message=rich_summary)
            else:
                pls_for_cb = target_pls or (last_batch.get("playlist_name") if last_batch else None)
                dl_cb = f"sync_dl:{pls_for_cb[:40]} all".strip() if pls_for_cb else "sync_dl:all"

                if total_injected > 0:
                    inj_note = f"• Se inyectaron <b>{total_injected} temas</b> existentes en Navidrome y se actualizaron comentarios.\n"
                    para_inj = f"Se inyectaron {total_injected} temas existentes en Navidrome y se actualizaron comentarios."
                else:
                    inj_note = "• Se inyectaron temas existentes en Navidrome y se actualizaron comentarios.\n"
                    para_inj = "Se inyectaron temas existentes en Navidrome y se actualizaron comentarios."

                summary_parts = [
                    (
                        f"✅ <b>Sincronización completada ({pls_label}):</b>\n"
                        f"{inj_note}"
                        f"• Quedan <b>{tot_rem}</b> elementos pendientes (Lote {batch_num} de {tot_batches}).\n"
                        f"• <i>Toca el botón abajo para descargarlos con el bot o cópialos a MediaHuman.</i>"
                    )
                ]
                if inj_html:
                    summary_parts.append(inj_html)
                if order_html:
                    summary_parts.append(order_html)

                summary_text = "\n\n".join(summary_parts)
                if len(summary_text) > 3800:
                    summary_text = summary_text[:3700] + "\n\n<i>... [Lista truncada por límite de Telegram. Usa /inyectadas para ver el registro]</i>"

                rich_blocks = [
                    {
                        "type": "heading",
                        "text": f"✅ Sincronización completada ({pls_label})",
                        "size": 2,
                    },
                    {
                        "type": "list",
                        "items": [
                            {"blocks": [{"type": "paragraph", "text": para_inj}]},
                            {"blocks": [{"type": "paragraph", "text": f"Quedan {tot_rem} elementos pendientes de descarga (Lote {batch_num} de {tot_batches})."}]},
                        ],
                    },
                ]
                if inj_blocks:
                    rich_blocks.extend(inj_blocks)
                if order_blocks:
                    rich_blocks.extend(order_blocks)

                rich_blocks.append({
                    "type": "buttons",
                    "buttons": [
                        {
                            "text": f"⚡ Descargar Todas las Canciones ({tot_rem})",
                            "callback_data": dl_cb,
                            "style": "success",
                        }
                    ],
                })

                rich_summary = {"blocks": rich_blocks}
                fallback_markup = {
                    "inline_keyboard": [
                        [{"text": f"⚡ Descargar Todas las Canciones ({tot_rem})", "callback_data": dl_cb, "style": "success"}]
                    ]
                }
                self.listener.send_reply(
                    chat_id,
                    summary_text,
                    thread_id,
                    reply_markup=fallback_markup,
                    rich_message=rich_summary,
                )

    def on_injected_command(self, chat_id: str, thread_id: Optional[str]):
        if not self.listener:
            return

        all_injected = self.state_tracker.get_last_injected()
        if not all_injected:
            self.listener.send_reply(
                chat_id,
                "ℹ️ <i>No hay registro de canciones auto-inyectadas en la base de datos de sincronización.</i>",
                thread_id,
            )
            return

        injected_map = {}
        for k, data in all_injected.items():
            if isinstance(data, dict):
                pname = data.get("playlist_name", k)
                trks = data.get("tracks", [])
                if trks:
                    injected_map[pname] = trks

        if not injected_map:
            self.listener.send_reply(
                chat_id,
                "ℹ️ <i>No hay canciones auto-inyectadas registradas recientemente.</i>",
                thread_id,
            )
            return

        inj_html, inj_blocks = format_injected_feedback(injected_map, max_per_playlist=30)
        total_inj = sum(len(v) for v in injected_map.values())
        summary_text = (
            f"📥 <b>Historial de Canciones Auto-Inyectadas ({total_inj}):</b>\n\n"
            f"{inj_html}\n\n"
            "💡 <i>Estas canciones se encontraban en tu biblioteca local de Navidrome y fueron añadidas directamente a las listas correspondientes.</i>"
        )
        if len(summary_text) > 3800:
            summary_text = summary_text[:3700] + "\n\n<i>... [Lista truncada por límite de Telegram]</i>"

        rich_summary = {
            "blocks": [
                {
                    "type": "heading",
                    "text": f"📥 Historial de Canciones Inyectadas ({total_inj})",
                    "size": 2,
                },
                {
                    "type": "paragraph",
                    "text": f"Se han detectado y auto-inyectado un total de {total_inj} canciones en tus listas desde tu biblioteca local.",
                },
            ] + inj_blocks
        }
        self.listener.send_reply(chat_id, summary_text, thread_id, rich_message=rich_summary)
