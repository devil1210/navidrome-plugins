#!/usr/bin/env python3
"""
Telegram Command & Callback Dispatcher:
Routes incoming Telegram updates (slash commands and inline button callbacks)
to their corresponding registered handlers.
"""

import html
import logging
import os
import threading
from typing import Any, Dict

from .menus import PICARD_PLUGIN_NAMES, build_plugins_menu

logger = logging.getLogger("listenbrainz_notifier")


def dispatch_update(listener: Any, update: Dict[str, Any]) -> None:
    """Processes a single incoming Telegram update (callback query or slash command)."""
    # 1. Handle interactive inline button clicks (callback_query)
    callback_query = update.get("callback_query")
    if callback_query:
        _handle_callback_query(listener, callback_query)
        return

    # 2. Handle standard slash commands
    message = update.get("message")
    if message:
        _handle_message_command(listener, message)


def _handle_callback_query(listener: Any, callback_query: Dict[str, Any]) -> None:
    cq_id = str(callback_query.get("id"))
    cq_msg = callback_query.get("message") or {}
    cq_chat = cq_msg.get("chat") or {}
    cq_chat_id = str(cq_chat.get("id", ""))
    cq_thread_id = str(cq_msg.get("message_thread_id")) if cq_msg.get("message_thread_id") else None
    cq_data = callback_query.get("data", "")

    if listener.authorized_chat_ids and cq_chat_id not in listener.authorized_chat_ids:
        logger.warning("Ignored unauthorized callback_query from %s", cq_chat_id)
        return

    if cq_data.startswith("dl_rec:"):
        arg = cq_data.split(":", 1)[1]
        listener.answer_callback_query(cq_id, text=f"⏳ Descargando sugerencia #{arg}...")
        if listener.on_download_rec_command:
            listener.on_download_rec_command(cq_chat_id, cq_thread_id, arg)

    elif cq_data.startswith("dl_nov:"):
        arg = cq_data.split(":", 1)[1]
        listener.answer_callback_query(cq_id, text=f"⏳ Descargando novedad #{arg}...")
        if listener.on_download_nov_command:
            listener.on_download_nov_command(cq_chat_id, cq_thread_id, arg)

    elif cq_data.startswith("add_disc:"):
        cq_msg_id = cq_msg.get("message_id", 0)
        logger.info("Received callback_query 'add_disc' from chat %s: %s", cq_chat_id, cq_data)
        if listener.on_add_disc_callback:
            listener.on_add_disc_callback(cq_chat_id, cq_thread_id, cq_msg_id, cq_id, cq_data)

    elif cq_data.startswith("retry_fail:"):
        token = cq_data.split(":", 1)[1]
        cq_msg_id = cq_msg.get("message_id", 0)
        logger.info("Received callback_query 'retry_fail' (token: %s) from chat %s", token, cq_chat_id)
        if listener.on_retry_failed_callback:
            listener.on_retry_failed_callback(cq_chat_id, cq_thread_id, cq_msg_id, cq_id, token)

    elif cq_data.startswith("sync_dl:"):
        arg = cq_data.split(":", 1)[1]
        listener.answer_callback_query(cq_id, text="⚡ Añadiendo canciones a la cola de descargas...")
        if listener.on_download_command:
            listener.on_download_command(cq_chat_id, cq_thread_id, arg)

    elif cq_data.startswith("dl_inc_batch:"):
        count_str = cq_data.split(":", 1)[1]
        listener.answer_callback_query(cq_id, text="🚀 Iniciando lote de descargas...")
        if getattr(listener, "on_download_incomplete_batch_callback", None):
            listener.on_download_incomplete_batch_callback(cq_chat_id, cq_thread_id, cq_msg.get("message_id", 0), count_str)

    elif cq_data.startswith("next_inc_batch:"):
        offset_str = cq_data.split(":", 1)[1]
        listener.answer_callback_query(cq_id, text="🎲 Buscando siguiente lote...")
        if getattr(listener, "on_completar_command", None):
            listener.on_completar_command(cq_chat_id, cq_thread_id, f"10 offset:{offset_str}")

    elif cq_data == "cancel_inc_batch":
        listener.answer_callback_query(cq_id, text="❌ Lote cancelado")
        listener.edit_message_reply_markup(cq_chat_id, cq_msg.get("message_id", 0), reply_markup=None)

    elif cq_data.startswith("pcfg:"):
        arg = cq_data.split(":", 1)[1]
        cq_msg_id = cq_msg.get("message_id", 0)
        if not listener.state_tracker:
            listener.answer_callback_query(cq_id, text="⚠️ Gestor de estado no disponible.")
            return

        if arg == "close":
            listener.answer_callback_query(cq_id, text="💾 Configuración guardada")
            listener.edit_message_reply_markup(cq_chat_id, cq_msg_id, reply_markup=None)
        elif arg == "invert":
            plugins = listener.state_tracker.get_picard_plugins()
            for k in plugins:
                listener.state_tracker.toggle_picard_plugin(k)
            updated = listener.state_tracker.get_picard_plugins()
            _, markup = build_plugins_menu(updated)
            listener.edit_message_reply_markup(cq_chat_id, cq_msg_id, reply_markup=markup)
            listener.answer_callback_query(cq_id, text="🔄 Plugins invertidos")
        else:
            new_val = listener.state_tracker.toggle_picard_plugin(arg)
            updated = listener.state_tracker.get_picard_plugins()
            _, markup = build_plugins_menu(updated)
            listener.edit_message_reply_markup(cq_chat_id, cq_msg_id, reply_markup=markup)
            status_str = "Activado" if new_val else "Desactivado"
            label = PICARD_PLUGIN_NAMES.get(arg, arg).split(" (")[0]
            listener.answer_callback_query(cq_id, text=f"{label}: {status_str}")


def _handle_message_command(listener: Any, message: Dict[str, Any]) -> None:
    chat = message.get("chat", {})
    chat_id = str(chat.get("id"))
    thread_id = str(message.get("message_thread_id")) if message.get("message_thread_id") else None
    text = message.get("text", "").strip()

    if not text.startswith("/"):
        return

    # Security check: only accept commands from authorized chats
    if listener.authorized_chat_ids and chat_id not in listener.authorized_chat_ids:
        logger.warning("Ignored unauthorized command from chat %s: %s", chat_id, text)
        return

    parts = text.split(maxsplit=1)
    command = parts[0].lower().split("@")[0]  # Strip @botname if called in a group
    args = parts[1].strip() if len(parts) > 1 else ""

    logger.info("Received bot command '%s' (args: '%s') from chat %s", command, args, chat_id)

    if command in ("/sync", "/buscar", "/check", "/listenbrainz"):
        listener.send_reply(chat_id, "🔍 <i>Consultando Navidrome y resolviendo álbumes en YouTube...</i>", thread_id)
        try:
            listener.on_sync_command(chat_id, thread_id, args or None)
        except Exception as e:
            logger.error("Error executing sync command: %s", e, exc_info=True)
            listener.send_reply(chat_id, f"❌ <b>Error durante la sincronización:</b> {e}", thread_id)

    elif command in ("/add", "/agregar", "/bajar_artista", "/bajar_album"):
        if listener.on_add_command:
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
                listener.send_reply(chat_id, help_add, thread_id)
            else:
                # Run resolution in thread so long-polling never blocks
                threading.Thread(
                    target=listener.on_add_command,
                    args=(chat_id, thread_id, args),
                    daemon=True,
                ).start()
        else:
            listener.send_reply(chat_id, "⚠️ El comando /add no está disponible en este entorno.", thread_id)

    elif command in ("/download", "/dl", "/descargar", "/bajar"):
        if listener.on_download_command:
            try:
                listener.on_download_command(chat_id, thread_id, args or None)
            except Exception as e:
                logger.error("Error executing download command: %s", e, exc_info=True)
                listener.send_reply(chat_id, f"❌ <b>Error durante la descarga:</b> {e}", thread_id)
        else:
            listener.send_reply(chat_id, "⚠️ El motor de descarga directa no está disponible en este entorno.", thread_id)

    elif command in ("/descubrir", "/descubrimiento", "/descubrimientos", "/discover", "/recomendar", "/recomendaciones"):
        if listener.on_discover_command:
            threading.Thread(
                target=listener.on_discover_command,
                args=(chat_id, thread_id, args or None),
                daemon=True,
            ).start()
        else:
            listener.send_reply(chat_id, "⚠️ El motor de recomendaciones no está disponible en este entorno.", thread_id)

    elif command in ("/novedades", "/novedad", "/estrenos", "/new", "/releases", "/lanzamientos", "/ultimos_lanzamientos"):
        if listener.on_new_releases_command:
            threading.Thread(
                target=listener.on_new_releases_command,
                args=(chat_id, thread_id, args or None),
                daemon=True,
            ).start()
        else:
            listener.send_reply(chat_id, "⚠️ El motor de novedades no está disponible en este entorno.", thread_id)

    elif command in ("/dl_rec", "/bajar_rec", "/dlrec", "/download_rec"):
        if listener.on_download_rec_command:
            try:
                listener.on_download_rec_command(chat_id, thread_id, args or None)
            except Exception as e:
                logger.error("Error executing download rec command: %s", e, exc_info=True)
                listener.send_reply(chat_id, f"❌ <b>Error al programar la descarga:</b> {e}", thread_id)
        else:
            listener.send_reply(chat_id, "⚠️ El motor de descarga de recomendaciones no está disponible.", thread_id)

    elif command in ("/dl_nov", "/bajar_nov", "/dlnov", "/download_nov", "/dl_nuevo", "/dl_novedad"):
        if listener.on_download_nov_command:
            try:
                listener.on_download_nov_command(chat_id, thread_id, args or None)
            except Exception as e:
                logger.error("Error executing download nov command: %s", e, exc_info=True)
                listener.send_reply(chat_id, f"❌ <b>Error al programar la descarga:</b> {e}", thread_id)
        else:
            listener.send_reply(chat_id, "⚠️ El motor de descarga de novedades no está disponible.", thread_id)

    elif command in ("/plugins", "/picard", "/modulos", "/ajustes"):
        if listener.state_tracker:
            plugins = listener.state_tracker.get_picard_plugins()
            menu_text, markup = build_plugins_menu(plugins)
            listener.send_reply(chat_id, menu_text, thread_id, reply_markup=markup)
        else:
            listener.send_reply(chat_id, "⚠️ El gestor de estado de plugins no está disponible.", thread_id)

    elif command in ("/limit", "/limite", "/max", "/cantidad"):
        if listener.on_limit_command:
            reply_text = listener.on_limit_command(args)
        else:
            reply_text = "⚙️ Comando de límite no disponible."
        listener.send_reply(chat_id, reply_text, thread_id)

    elif command == "/status":
        status_text = listener.on_status_command()
        listener.send_reply(chat_id, status_text, thread_id)

    elif command in ("/cancel", "/cancelar", "/abort", "/detener"):
        if listener.download_worker:
            success, msg = listener.download_worker.cancel_active()
            if success:
                listener.send_reply(
                    chat_id,
                    f"🛑 <b>Cancelando descarga en curso:</b>\n• {html.escape(msg)}\n<i>Se detendrán los siguientes elementos de la cola.</i>",
                    thread_id,
                )
            else:
                listener.send_reply(chat_id, "ℹ️ <i>No hay ninguna descarga activa en curso.</i>", thread_id)
        else:
            listener.send_reply(chat_id, "⚠️ El motor de descargas no está disponible.", thread_id)

    elif command in ("/pause", "/pausar", "/detener_cola"):
        if listener.download_worker:
            success, msg = listener.download_worker.pause()
            listener.send_reply(chat_id, msg, thread_id)
        else:
            listener.send_reply(chat_id, "⚠️ El motor de descargas no está disponible.", thread_id)

    elif command in ("/resume", "/reanudar", "/continuar", "/seguir"):
        if listener.download_worker:
            success, msg = listener.download_worker.resume()
            listener.send_reply(chat_id, msg, thread_id)
        else:
            listener.send_reply(chat_id, "⚠️ El motor de descargas no está disponible.", thread_id)

    elif command in ("/descartar", "/ignorar", "/ignore", "/discard"):
        if listener.on_discard_command:
            try:
                listener.on_discard_command(chat_id, thread_id, args)
            except Exception as e:
                logger.error("Error executing discard command: %s", e, exc_info=True)
                listener.send_reply(chat_id, f"❌ <b>Error al descartar:</b> {e}", thread_id)
        else:
            listener.send_reply(chat_id, "⚠️ El comando /descartar no está disponible.", thread_id)

    elif command in ("/restaurar", "/restore", "/unignore"):
        if listener.on_restore_command:
            try:
                listener.on_restore_command(chat_id, thread_id, args)
            except Exception as e:
                logger.error("Error executing restore command: %s", e, exc_info=True)
                listener.send_reply(chat_id, f"❌ <b>Error al restaurar:</b> {e}", thread_id)
        else:
            listener.send_reply(chat_id, "⚠️ El comando /restaurar no está disponible.", thread_id)

    elif command in ("/inyectadas", "/injected", "/agregadas", "/historial_sync"):
        if listener.on_injected_command:
            try:
                listener.on_injected_command(chat_id, thread_id)
            except Exception as e:
                logger.error("Error executing injected command: %s", e, exc_info=True)
                listener.send_reply(chat_id, f"❌ <b>Error al consultar temas inyectados:</b> {e}", thread_id)
        else:
            listener.send_reply(chat_id, "⚠️ El comando /inyectadas no está disponible.", thread_id)

    elif command in ("/incompletos", "/incompleto", "/incomplete"):
        if listener.on_incompletos_command:
            try:
                listener.on_incompletos_command(chat_id, thread_id)
            except Exception as e:
                logger.error("Error executing incompletos command: %s", e, exc_info=True)
                listener.send_reply(chat_id, f"❌ <b>Error en auditoría de incompletos:</b> {e}", thread_id)
        else:
            listener.send_reply(chat_id, "⚠️ El auditor de álbumes incompletos no está disponible.", thread_id)

    elif command in ("/unificar", "/consolidar", "/unify", "/unificar_carpetas"):
        if listener.on_unificar_command:
            try:
                listener.on_unificar_command(chat_id, thread_id)
            except Exception as e:
                logger.error("Error executing unificar command: %s", e, exc_info=True)
                listener.send_reply(chat_id, f"❌ <b>Error en consolidación:</b> {e}", thread_id)
        else:
            listener.send_reply(chat_id, "⚠️ El consolidador de carpetas no está disponible.", thread_id)

    elif command in ("/noche", "/nocturno", "/nightly", "/mantenimiento"):
        if listener.on_noche_command:
            try:
                listener.on_noche_command(chat_id, thread_id)
            except Exception as e:
                logger.error("Error executing noche command: %s", e, exc_info=True)
                listener.send_reply(chat_id, f"❌ <b>Error en mantenimiento nocturno:</b> {e}", thread_id)
        else:
            listener.send_reply(chat_id, "⚠️ El gestor nocturno no está disponible.", thread_id)

    elif command in ("/fallidos", "/bloqueados", "/failed"):
        if listener.on_fallidos_command:
            try:
                listener.on_fallidos_command(chat_id, thread_id)
            except Exception as e:
                logger.error("Error executing fallidos command: %s", e, exc_info=True)
                listener.send_reply(chat_id, f"❌ <b>Error al consultar fallidos:</b> {e}", thread_id)
        else:
            listener.send_reply(chat_id, "⚠️ El registro de fallidos no está disponible.", thread_id)

    elif command in ("/reintentar_fallidos", "/limpiar_fallidos", "/clear_failed"):
        if listener.on_reintentar_fallidos_command:
            try:
                listener.on_reintentar_fallidos_command(chat_id, thread_id)
            except Exception as e:
                logger.error("Error executing reintentar fallidos command: %s", e, exc_info=True)
                listener.send_reply(chat_id, f"❌ <b>Error al limpiar fallidos:</b> {e}", thread_id)
        else:
            listener.send_reply(chat_id, "⚠️ El comando para limpiar fallidos no está disponible.", thread_id)

    elif command in ("/completar", "/avanzar", "/lote_incompletos", "/completar_lote"):
        if getattr(listener, "on_completar_command", None):
            try:
                listener.on_completar_command(chat_id, thread_id, args)
            except Exception as e:
                logger.error("Error executing completar command: %s", e, exc_info=True)
                listener.send_reply(chat_id, f"❌ <b>Error al preparar lote:</b> {e}", thread_id)
        else:
            listener.send_reply(chat_id, "⚠️ El comando /completar no está disponible.", thread_id)

    elif command in ("/topic", "/topics", "/tema", "/temas"):
        # Allows viewing or binding the active thread to a topic key (e.g. /topic anadidos)
        raw_key = args.lower().strip()
        if raw_key in ("descargas", "anadidos", "añadidos"):
            actual_key = "anadidos" if "anadid" in raw_key or "añadid" in raw_key else "descargas"
            if thread_id:
                try:
                    tid_int = int(thread_id)
                    listener.topics_cache.setdefault(chat_id, {})[actual_key] = tid_int
                    listener._save_topics_cache()
                    listener.send_reply(
                        chat_id,
                        f"✅ <b>Topic vinculado correctamente:</b>\n• Canal/Tema: <code>{actual_key}</code> ➔ ID: <code>{tid_int}</code>",
                        thread_id,
                    )
                except Exception as ex:
                    listener.send_reply(chat_id, f"❌ Error guardando topic: {ex}", thread_id)
            else:
                listener.send_reply(
                    chat_id,
                    f"⚠️ Para vincular <code>{actual_key}</code>, ejecuta <code>/topic {actual_key}</code> dentro del tema correspondiente.",
                    thread_id,
                )
        else:
            # Show current registered topics
            cache_for_chat = listener.topics_cache.get(chat_id, {})
            env_desc = os.environ.get("TELEGRAM_TOPIC_DESCARGAS_ID", cache_for_chat.get("descargas", "No configurado"))
            env_anad = os.environ.get("TELEGRAM_TOPIC_ANADIDOS_ID", cache_for_chat.get("anadidos", "No configurado"))
            auto_val = os.environ.get("TELEGRAM_AUTO_CREATE_TOPICS", "false")
            info_text = (
                "📌 <b>Configuración de Topics (Telegram Forum):</b>\n\n"
                f"• 📥 <b>Descargas:</b> <code>{env_desc}</code>\n"
                f"• ✨ <b>Añadidos:</b> <code>{env_anad}</code>\n"
                f"• 🔒 <b>Auto-creación:</b> <code>{auto_val}</code>\n"
                f"• 💬 <b>Tema actual:</b> <code>{thread_id or 'General / Raíz'}</code>\n\n"
                "<i>Para vincular este tema actual a una categoría, escribe:</i>\n"
                "• <code>/topic descargas</code>\n"
                "• <code>/topic anadidos</code>"
            )
            listener.send_reply(chat_id, info_text, thread_id)

    elif command in ("/help", "/start"):
        help_text = (
            "🤖 <b>ListenBrainz Navidrome Notifier Bot</b>\n\n"
            "<b>Comandos disponibles:</b>\n"
            "• <code>/sync</code> o <code>/buscar</code> — Sincroniza y lista álbumes faltantes (Lote 1 prioritario).\n"
            "• <code>/sync [Nombre] lote [N]</code> — Consulta un lote específico (ej. <code>/sync Descubrimiento Diario lote 2</code>).\n"
            "• <code>/inyectadas</code> — Muestra las canciones locales agregadas a tus listas en la última sincronización.\n"
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
        listener.send_reply(chat_id, help_text, thread_id)
