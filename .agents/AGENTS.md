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

## Jerarquía Estricta de Prioridad de Letras (Better Lyrics 15-Level Pipeline)
- **TODAS las búsquedas y procesamiento de letras, TANTO en MusicBrainz Picard COMO en los Plugins de Navidrome (WASM / Go / Feishin), DEBEN seguir estrictamente el orden numérico de prioridad de 15 niveles de Better Lyrics:**
  1. `#1` **Better Lyrics / QQMusic / Kugou (Syllable)**: Sincronización por sílaba fina (`<mm:ss.xxx>` por sílaba)
  2. `#2` **Unison (Syllable)**: Algoritmo Unison para sílabas
  3. `#3` **BiniLyrics (Syllable)**: Algoritmo BiniLyrics para sílabas
  4. `#4` **Better Lyrics Portato (Word)**: Convertidor Portato (suaviza y extiende duraciones por palabra sin pausas muertas)
  5. `#5` **Musixmatch RichSync (Word)**: Estampas de tiempo por palabra nativas (`<mm:ss.xxx>` por palabra)
  6. `#6` **Better Lyrics (Line)**: Algoritmo de línea estándar
  7. `#7` **Unison (Line)**: Sincronización de línea Unison
  8. `#8` **YouTube Captions (Line)**: Subtítulos oficiales TimedText de YouTube (`[mm:ss.xx]`)
  9. `#9` **BiniLyrics (Line)**: Sincronización de línea BiniLyrics
  10. `#10` **LRCLib (Line)**: Base de datos LRCLib por línea (`[mm:ss.xx]`)
  11. `#11` **Better Lyrics Legato (Line)**: Convertidor Legato (conecta el final de cada línea con el inicio de la siguiente)
  12. `#12` **Musixmatch Subtitle (Line)**: Subtítulos por línea de Musixmatch
  13. `#13` **YouTube (Unsynced)**: Texto sin sincronizar de YouTube
  14. `#14` **Unison (Unsynced)**: Texto plano de Unison
  15. `#15` **LRCLib / NetEase (Unsynced)**: Texto plano final de respaldo
- Si un proveedor o nivel de mayor prioridad está disponible, DEBE reemplazar de inmediato a cualquier proveedor de menor prioridad previamente asignado.
