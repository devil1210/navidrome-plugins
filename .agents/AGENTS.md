# Reglas del Proyecto (Workspace Rules)

## Compilacion y Despliegue de Plugins (PROHIBIDO compilar en LXC)
- **NUNCA compilar binarios WASM (.wasm) ni paquetes (.ndp) en el servidor LXC ni en ningun otro entorno remoto.**
- **TODAS las compilaciones de WASM con TinyGo y el empaquetado de archivos .ndp / .zip DEBEN realizarse obligatoria y exclusivamente de manera LOCAL en este PC (Windows).**
- El servidor LXC solo debe recibir los paquetes .ndp ya listos y empaquetados mediante transferencia por scp (make deploy).

## Gestion del Servicio Navidrome (PROHIBIDO reiniciar el contenedor Navidrome)
- **NUNCA reiniciar el contenedor Docker `navidrome` (`docker restart navidrome`).**
- Reiniciar el contenedor borra la configuracion de credenciales activas del usuario.
- Para actualizar el estado o recargar los plugins, utilizar la interfaz Web/API de Navidrome o el watcher automatico de archivos en `/data/plugins`, sin reiniciar el contenedor.

## Interfaz Web de Navidrome (WebUI Constraints)
- **NUNCA asumir ni indicarle al usuario que presione botones de "Refrescar Metadatos" en la interfaz web de Navidrome**, ya que dicho botón NO existe en la WebUI estándar para artistas.
- Todas las actualizaciones de metadatos o recargas deben manejarse automáticamente mediante plugins WASM, tareas programadas (schedulers) o scripts en segundo plano.

## Jerarquía Estricta de Prioridad de Letras (Global Low-Latency Pipeline)
- **Omitir Proveedores Regionales/Lentos (QQMusic / Kugou / China)**: Quedan excluidos por bloqueos de red y tiempos de espera de 5s+.
- **TODAS las búsquedas y procesamiento de letras (Picard y Navidrome/Feishin) DEBEN seguir este orden de prioridad global de alta velocidad:**
  1. `#1` **Better Lyrics Portato (Word)**: Convertidor Portato (suaviza y extiende duraciones por palabra sin pausas muertas)
  2. `#2` **Musixmatch RichSync (Word)**: Estampas de tiempo por palabra nativas (`<mm:ss.xxx>` por palabra)
  3. `#3` **YouTube Captions (Line)**: Subtítulos oficiales TimedText de YouTube (`[mm:ss.xx]`)
  4. `#4` **LRCLib (Line)**: Base de datos LRCLib por línea (`[mm:ss.xx]`)
  5. `#5` **Musixmatch Subtitle (Line)**: Subtítulos por línea de Musixmatch
  6. `#6` **LRCLib / NetEase (Unsynced)**: Texto plano final de respaldo
