# Navidrome ListenBrainz Missing Jams Notifier (MediaHuman)

Script automatizado en Python que monitoriza las playlists de Navidrome generadas por el plugin de ListenBrainz (`daily-jams`, etc.), extrae las canciones no encontradas en tu biblioteca (`Tracks not matched`), resuelve el **álbum oficial completo en YouTube Music** (`https://www.youtube.com/playlist?list=OLAK5uy_...`), deduplica por álbum y te envía la lista formateada a Telegram dentro de una cita expandible (`<blockquote expandable>`).

Con un **solo toque o clic** en Telegram se copian todas las URLs al portapapeles, listas para pegarse directamente en **MediaHuman YouTube Downloader**.

---

## Características

- 🔍 **Detección Automática**: Detecta listas de ListenBrainz a través de la API Subsonic de Navidrome.
- 💿 **Resolución de Álbumes Oficiales**: Usa `ytmusicapi` para encontrar los álbumes oficiales en YouTube Music sin necesidad de claves de Google Cloud Console ni límites de cuota.
- 🎯 **Deduplicación Inteligente**: Si varias canciones faltantes pertenecen al mismo álbum (ej. 3 temas de *Bob Marley* de *Exodus*), solo extrae y envía el enlace del álbum una sola vez.
- 📱 **Telegram 1-Click Copy**: Formatea todos los enlaces dentro de `<blockquote expandable>` o `<pre><code>` para copiar todo de golpe en móviles o escritorio.
- 🧠 **Control de Estado (`.state.json`)**: Recuerda las listas y comentarios ya procesados para no repetir notificaciones.
- 🛡️ **Tolerancia a Fallos y Caracteres Complejos**: Parsea correctamente canciones con comas en el título (ej. `Boom, Boom, Boom, Boom!!`, `Harder, Better, Faster, Stronger`) y caracteres Unicode / japoneses.

---

## Instalación de Requisitos

```bash
pip install -r requirements.txt
```

---

## Configuración (.env)

Copia el archivo `.env.example` a `.env` y configura tus valores:

```bash
cp .env.example .env
```

```env
# Navidrome Subsonic API
NAVIDROME_URL=http://localhost:4533
NAVIDROME_USER=admin
NAVIDROME_PASSWORD=mi_contraseña

# Telegram Bot
TELEGRAM_BOT_TOKEN=123456789:ABCDEF...
TELEGRAM_CHAT_ID=-1001234567890
TELEGRAM_THREAD_ID=

# Estilo de cita: "blockquote" o "code"
TELEGRAM_QUOTE_STYLE=blockquote

# Límite de álbumes por lote entregados por sincronización (default: 20, 0 = sin límite)
MAX_ALBUMS_PER_SYNC=20
```

---

## Comandos Interactivos en Telegram (Modo Bot `--bot`)

Cuando el notificador se ejecuta con `--bot` (por defecto en el servicio systemd):

| Comando | Descripción |
| :--- | :--- |
| `/sync` o `/buscar` | Sincroniza y entrega el **Lote 1** de álbumes prioritarios (por defecto 20). |
| `/sync [Nombre]` | Sincroniza una playlist específica (ej. `/sync Descubrimiento Diario`). |
| `/sync [Nombre] lote [N]` | Entrega el **Lote N** de la playlist (ej. `/sync Descubrimiento Diario lote 2` o `/sync Descubrimiento Diario 2`). |
| `/sync [Nombre] all` | Entrega **todos** los álbumes en un solo mensaje sin límite. |
| `/limit [N]` | Establece la cantidad predeterminada de álbumes por lote (ej. `/limit 20`, `/limit 30` o `/limit 0` para sin límite). |
| `/limit` | Muestra el límite actual configurado y cómo cambiarlo. |
| `/status` | Muestra el estado del daemon, playlists vigiladas, límite actual y última comprobación. |
| `/help` | Menú de ayuda con todos los comandos disponibles. |

---

## Modos de Uso por Consola (CLI)

### 1. Modo Bot Interactivo + Programador en segundo plano
```bash
python notifier.py --bot --interval 30
```

### 2. Comprobación única (Modo Cron o Tarea Programada)
```bash
python notifier.py --check --limit 20 --batch 1
```

### 3. Modo Demonio en segundo plano
```bash
python notifier.py --daemon --interval 30
```

### 4. Prueba en consola sin enviar a Telegram (`--dry-run`)
```bash
python notifier.py --dry-run --check
```

### 5. Probar con un texto o comentario de muestra
```bash
python notifier.py --dry-run --sample-text "Tracks not matched Three Little Birds by Bob Marley & The Wailers, I’m Just a Kid by Simple Plan, Harder, Better, Faster, Stronger by Daft Punk"
```

### 6. Forzar reenvío ignorando el historial previo (`--force`)
```bash
python notifier.py --force
```

---

## Automatización Desatendida

### En Servidor Linux / LXC (systemd)
1. Copia la carpeta `listenbrainz_notifier` a `/opt/navidrome-scripts/listenbrainz_notifier`.
2. Instala los archivos de servicio y timer:
   ```bash
   cp deploy/navidrome-jams-notifier.service /etc/systemd/system/
   cp deploy/navidrome-jams-notifier.timer /etc/systemd/system/
   systemctl daemon-reload
   systemctl enable --now navidrome-jams-notifier.timer
   ```
3. Verifica el estado:
   ```bash
   systemctl list-timers | grep navidrome
   ```

### En Windows (Programador de Tareas)
1. Abre el **Programador de Tareas** (`taskschd.msc`).
2. Crea una **Tarea Básica** que se ejecute diariamente o cada 1 hora.
3. Acción: Iniciar un programa -> `scripts/listenbrainz_notifier/deploy/run_task.bat`.
