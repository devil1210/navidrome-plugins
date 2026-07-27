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
