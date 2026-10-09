#!/usr/bin/env python3
"""
Interactive Telegram Menus: Discography selector, interactive checklist, and Picard plugin settings.
"""

import html
from typing import Any, Dict, List, Optional, Set, Tuple

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
                {"text": "📋 Seleccionar Álbumes (Checklist)", "callback_data": f"add_disc:check:{token}:1", "style": "primary"}
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

    fallback_buttons = []
    if num_albums > 0:
        fallback_buttons.append([{"text": "📋 Seleccionar Álbumes (Checklist)", "callback_data": f"add_disc:check:{token}:1"}])
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

    if total_pages > 1:
        nav_btns = []
        if page > 1:
            nav_btns.append({"text": "⬅️ Anterior", "callback_data": f"add_disc:check:{token}:{page-1}"})
        nav_btns.append({"text": f"📄 {page}/{total_pages}", "callback_data": f"add_disc:noop:{token}"})
        if page < total_pages:
            nav_btns.append({"text": "➡️ Siguiente", "callback_data": f"add_disc:check:{token}:{page+1}"})
        rich_blocks.append({"type": "buttons", "buttons": nav_btns})
        fallback_buttons.append(nav_btns)

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

    inline_keyboard.append([
        {"text": "🔄 Invertir", "callback_data": "pcfg:invert"},
        {"text": "💾 Guardar / Cerrar", "callback_data": "pcfg:close"},
    ])

    return "\n".join(lines), {"inline_keyboard": inline_keyboard}
