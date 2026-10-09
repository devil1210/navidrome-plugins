#!/usr/bin/env python3
"""
Rich HTML and Telegram Block formatting for albums and batch download progress.
"""

import html
from typing import Any, Dict, List, Optional, Union


def format_duration(seconds: Union[int, float]) -> str:
    """Formats seconds into mm:ss or hh:mm:ss."""
    sec = int(seconds)
    if sec <= 0:
        return ""
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def clean_rich_html(input_str: str) -> str:
    """Cleans up raw HTML string for Telegram Bot API rich message compliance."""
    lines = [line.strip() for line in input_str.split("\n") if line.strip()]
    result = []
    for i, l in enumerate(lines):
        result.append(l)
        if i < len(lines) - 1:
            next_l = lines[i + 1]
            if not (l.endswith(">") or next_l.startswith("<")):
                result.append("<br/>")
    return "".join(result)


def build_album_rich_html(
    album_name: str,
    artist_name: str,
    year: str,
    genre: str,
    disc_count: int,
    song_count: int,
    total_duration_sec: int,
    songs: List[Dict[str, Any]],
    nav_url: str = "",
) -> str:
    esc_album = html.escape(album_name)
    esc_artist = html.escape(artist_name)
    esc_year = html.escape(str(year)) if year else ""
    esc_genre = html.escape(genre) if genre else ""
    dur_str = format_duration(total_duration_sec)

    if disc_count <= 1:
        song_lines = [
            "<table bordered striped compact>",
            '  <tr><th align="center">#</th><th align="left">Título</th><th align="center">Duración</th></tr>',
        ]
        for s in songs:
            trk = s.get("track", 0)
            trk_str = f"{trk:02d}" if trk > 0 else "-"
            dur = format_duration(s.get("duration", 0)) or "-"
            song_lines.append(
                f'  <tr><td align="center">{trk_str}</td><td align="left">{html.escape(s["title"])}</td><td align="center">{dur}</td></tr>'
            )
        song_lines.append("</table>")
        songs_formatted = "\n".join(song_lines)
    else:
        disc_groups: Dict[int, List[Dict[str, Any]]] = {}
        for s in songs:
            d = s.get("disc", 1)
            disc_groups.setdefault(d, []).append(s)

        disc_blocks = []
        for d in sorted(disc_groups.keys()):
            block = [
                f"<details open>\n  <summary>💿 Disco {d}</summary>",
                "  <table bordered striped compact>",
                '    <tr><th align="center">#</th><th align="left">Título</th><th align="center">Duración</th></tr>',
            ]
            for s in disc_groups[d]:
                trk = s.get("track", 0)
                trk_str = f"{trk:02d}" if trk > 0 else "-"
                dur = format_duration(s.get("duration", 0)) or "-"
                block.append(
                    f'    <tr><td align="center">{trk_str}</td><td align="left">{html.escape(s["title"])}</td><td align="center">{dur}</td></tr>'
                )
            block.append("  </table>\n</details>")
            disc_blocks.append("\n".join(block))
        songs_formatted = "\n".join(disc_blocks)

    nav_link = f'\n<hr/>\n<a href="{nav_url}">🌐 Escuchar en Navidrome</a>' if nav_url else ""

    raw_html = f"""<img src="tg://photo?id=cover" />
<h3>📚 {esc_album}</h3>
<h4>👤 {esc_artist}</h4>
<table bordered striped>
  <tr>
    <td><b>📅 Año</b></td>
    <td>{esc_year}</td>
  </tr>
  <tr>
    <td><b>📦 Género</b></td>
    <td>{esc_genre}</td>
  </tr>
  <tr>
    <td><b>💿 Discos</b></td>
    <td>{disc_count}</td>
  </tr>
  <tr>
    <td><b>🔢 Canciones</b></td>
    <td>{song_count}</td>
  </tr>
  <tr>
    <td><b>🕒 Duración</b></td>
    <td>{dur_str}</td>
  </tr>
</table>

<details>
  <summary>🎵 Ver Lista de Canciones</summary>
  {songs_formatted}
</details>{nav_link}"""

    return clean_rich_html(raw_html)


def build_album_progress_rich_html(
    album_name: str,
    artist_name: str,
    tracks: List[Dict[str, Any]],
    completed_count: int,
    total_count: int,
    current_track_title: str = "",
    dest_folder: str = "",
) -> str:
    esc_album = html.escape(album_name)
    esc_artist = html.escape(artist_name)
    esc_current = html.escape(current_track_title) if current_track_title else "Preparando..."

    pct = int((completed_count / total_count) * 100) if total_count > 0 else 0
    filled = int((pct / 100) * 10)
    bar = "▰" * filled + "▱" * (10 - filled)

    song_lines = [
        "<table bordered striped compact>",
        '  <tr><th align="center">#</th><th align="left">Título</th><th align="center">Estado</th></tr>',
    ]

    display_tracks = tracks
    if len(tracks) > 25:
        display_tracks = tracks[:25]

    for t in display_tracks:
        idx = t.get("index", 0)
        idx_str = f"{idx:02d}" if idx > 0 else "-"
        t_title = html.escape(t.get("title", ""))
        status_code = t.get("status", "pending")

        if status_code == "done":
            state_label = "✅"
        elif status_code == "downloading":
            state_label = "⏳"
        else:
            state_label = "⏸️"

        song_lines.append(
            f'  <tr><td align="center">{idx_str}</td><td align="left">{t_title}</td><td align="center">{state_label}</td></tr>'
        )

    if len(tracks) > len(display_tracks):
        remaining = len(tracks) - len(display_tracks)
        song_lines.append(
            f'  <tr><td align="center">-</td><td align="left"><i>... y {remaining} pistas más</i></td><td align="center">⏸️</td></tr>'
        )
    song_lines.append("</table>")
    songs_formatted = "\n".join(song_lines)

    dest_row = (
        f"\n  <tr>\n    <td><b>📁 Destino</b></td>\n    <td><code>{html.escape(dest_folder)}</code></td>\n  </tr>"
        if dest_folder else ""
    )

    raw_html = f"""<h3>📥 Descargando: {esc_album}</h3>
<h4>👤 {esc_artist}</h4>
<table bordered striped>
  <tr>
    <td><b>📊 Progreso</b></td>
    <td>{bar} <b>{pct}%</b> ({completed_count}/{total_count})</td>
  </tr>
  <tr>
    <td><b>🎵 Pista actual</b></td>
    <td>{esc_current}</td>
  </tr>{dest_row}
</table>

<details open>
  <summary>🎵 Progreso de Pistas (Desplegado)</summary>
  {songs_formatted}
</details>"""

    return clean_rich_html(raw_html)


def build_album_completed_rich_html(
    album_name: str,
    artist_name: str,
    tracks: List[Dict[str, Any]],
    completed_count: int,
    total_count: int,
    dest_folder: str = "",
    success: bool = True,
    final_note: str = "",
) -> str:
    esc_album = html.escape(album_name)
    esc_artist = html.escape(artist_name)
    title_icon = "✅" if success else "⚠️"
    title_text = "¡Descarga finalizada con éxito!" if success else "Descarga completada con advertencias"

    song_lines = [
        "<table bordered striped compact>",
        '  <tr><th align="center">#</th><th align="left">Título</th><th align="center">Estado</th></tr>',
    ]
    display_tracks = tracks if len(tracks) <= 30 else tracks[:30]
    for t in display_tracks:
        idx = t.get("index", 0)
        idx_str = f"{idx:02d}" if idx > 0 else "-"
        t_title = html.escape(t.get("title", ""))
        song_lines.append(
            f'  <tr><td align="center">{idx_str}</td><td align="left">{t_title}</td><td align="center">✅</td></tr>'
        )
    if len(tracks) > len(display_tracks):
        song_lines.append(
            f'  <tr><td align="center">-</td><td align="left"><i>... y {len(tracks) - len(display_tracks)} pistas más</i></td><td align="center">✅</td></tr>'
        )
    song_lines.append("</table>")
    songs_formatted = "\n".join(song_lines)

    dest_row = (
        f"\n  <tr>\n    <td><b>📁 Destino</b></td>\n    <td><code>{html.escape(dest_folder)}</code></td>\n  </tr>"
        if dest_folder else ""
    )
    note_row = (
        f"\n  <tr>\n    <td><b>⚠️ Nota</b></td>\n    <td>{html.escape(final_note)}</td>\n  </tr>"
        if final_note else ""
    )

    raw_html = f"""<h3>{title_icon} {title_text}</h3>
<h4>📚 {esc_album} — 👤 {esc_artist}</h4>
<table bordered striped>
  <tr>
    <td><b>📊 Progreso</b></td>
    <td>▰▰▰▰▰▰▰▰▰▰ <b>100%</b> ({completed_count}/{total_count} pistas)</td>
  </tr>{dest_row}
  <tr>
    <td><b>✨ Estado</b></td>
    <td>Archivos catalogados, enriquecidos con Picard y organizados en la biblioteca.</td>
  </tr>
  <tr>
    <td><b>🖼️ Notificación</b></td>
    <td>Ficha oficial y carátula enviadas al tema <b>✨ Añadidos</b></td>
  </tr>{note_row}
</table>

<details>
  <summary>🎵 Pistas descargadas ({total_count})</summary>
  {songs_formatted}
</details>"""
    return clean_rich_html(raw_html)


def build_batch_progress_rich_html(
    title: str,
    items: List[Dict[str, Any]],
    completed_count: int,
    total_count: int,
    current_item_name: str = "",
    dest_folder: str = "",
    finished: bool = False,
) -> str:
    esc_title = html.escape(title)
    esc_curr = html.escape(current_item_name) if current_item_name else "Iniciando..."

    ok_cnt = sum(1 for it in items if it.get("status") == "done")
    skipped_cnt = sum(1 for it in items if it.get("status") == "skipped")
    failed_cnt = sum(1 for it in items if it.get("status") == "failed")
    successful_cnt = ok_cnt + skipped_cnt
    total_items = len(items) if items else total_count

    if finished:
        pct = int((successful_cnt / total_items) * 100) if total_items > 0 else 0
        if failed_cnt > 0:
            status_header = f"⚠️ ¡Lote finalizado con {failed_cnt} error{'es' if failed_cnt > 1 else ''}!"
            prog_detail = f"{pct}% ({successful_cnt}/{total_items} · ⚠️ {failed_cnt} con error)"
        else:
            status_header = "✅ ¡Lote finalizado con éxito!"
            prog_detail = f"100% ({total_items}/{total_items})"
    else:
        pct = int((completed_count / total_items) * 100) if total_items > 0 else 0
        status_header = "📦 Progreso de Descarga por Lote"
        prog_detail = f"{pct}% ({completed_count}/{total_items})"

    filled = int((pct / 100) * 10)
    bar = "▰" * filled + "▱" * (10 - filled)

    item_lines = [
        "<table bordered striped compact>",
        '  <tr><th align="center">#</th><th align="left">Título</th><th align="center">Estado</th></tr>',
    ]
    display_items = items if len(items) <= 30 else items[:30]
    for it in display_items:
        idx_str = f"{it.get('index', 1):02d}"
        t_name = html.escape(it.get('name', ''))
        st = it.get('status', 'pending')
        if st == 'done':
            st_label = "✅"
        elif st == 'downloading':
            st_label = "⏳"
        elif st == 'skipped':
            st_label = "📁"
        elif st == 'failed':
            st_label = "⚠️"
        else:
            st_label = "⏸️"
        item_lines.append(
            f'  <tr><td align="center">{idx_str}</td><td align="left">{t_name}</td><td align="center">{st_label}</td></tr>'
        )
    if len(items) > len(display_items):
        item_lines.append(
            f'  <tr><td align="center">-</td><td align="left"><i>... y {len(items) - len(display_items)} más</i></td><td align="center">...</td></tr>'
        )
    item_lines.append("</table>")
    items_formatted = "\n".join(item_lines)

    dest_row = (
        f"\n  <tr>\n    <td><b>📁 Destino</b></td>\n    <td><code>{html.escape(dest_folder)}</code></td>\n  </tr>"
        if dest_folder else ""
    )
    curr_row = (
        f"\n  <tr>\n    <td><b>⏳ Procesando</b></td>\n    <td>{esc_curr}</td>\n  </tr>"
        if not finished else ""
    )

    raw_html = f"""<h3>{status_header}</h3>
<h4>📋 {esc_title}</h4>
<table bordered striped>
  <tr>
    <td><b>📊 Progreso Global</b></td>
    <td>{bar} <b>{prog_detail}</b></td>
  </tr>{curr_row}{dest_row}
</table>

<details open>
  <summary>📋 Lista de Álbumes y Sencillos ({total_items})</summary>
  {items_formatted}
</details>"""
    return clean_rich_html(raw_html)
