# Picard File Deduplicator Plugin

Plugin para MusicBrainz Picard que detecta, previene y remueve automáticamente archivos duplicados con sufijos de copia como ` (1)`, ` (2)` (archivos de audio o letras `.lrc`) al guardar etiquetas o mediante acción de menú con clic derecho.

## Características
1. **Prevención Automática al Guardar**: Al guardar cualquier pista en Picard, escanea si existe alguna versión duplicada con ` (1)` o ` (2)` en la misma carpeta y la elimina automáticamente.
2. **Acción de Menú**: Añade la opción **Mover duplicados (1) a _duplicados_backup** al hacer clic derecho sobre álbumes o pistas cargadas en Picard.
