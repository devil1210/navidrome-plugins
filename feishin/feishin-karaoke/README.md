# Feishin Karaoke & Better Lyrics Shaders

Integración de **Karaoke Palabra por Palabra** y **Fondos Animados de Gradientes Fluidos** para el reproductor de música de escritorio **Feishin Desktop (Electron)**.

---

## 🌟 Características

1. **Karaoke Palabra por Palabra (Word-by-Word Sync)**:
   - Sincronización milimétrica basada en etiquetas de tiempo `<mm:ss.xxx>` (Enhanced LRC / TTML / SYLT).
   - **Manejo de Notas Sostenidas**: Mantiene el resplandor y brillo en la palabra cantada durante **toda la duración de la nota** (incluso en notas largas de varios segundos) y salta a la siguiente palabra únicamente cuando arranca la nueva estampa de tiempo.
2. **Shaders de Fondo Fluidos (Better Lyrics Shaders)**:
   - Renderizado en tiempo real a 60 FPS de gradientes de malla fluidos animando los colores del álbum mediante `<canvas>` WebGL.
3. **Desenfoque de Profundidad (Depth Blur)**:
   - Estética cristalina estilo **Apple Music / YouTube Music** con desenfoque dinámico (`backdrop-filter: blur()`) en frases inactivas.
4. **Búsqueda Automática Fallback**:
   - Integración nativa con la API pública de **LRCLIB** si la canción local no incluye letras sincronizadas por palabras.

---

## 📚 Créditos y Reconocimientos

Este proyecto integra y se inspira en el ecosistema de las siguientes herramientas de código abierto:

* **[Feishin Desktop Music Player](https://github.com/jeffvli/feishin)**: Reproductor de música moderno de código abierto creado por **jeffvli** (Electron, React, TypeScript).
* **[Better Lyrics](https://github.com/better-lyrics/better-lyrics)**: Extensión y motor de letras sincronizadas palabra por palabra para YouTube Music creado por la organización **Better Lyrics**.
* **[Better Lyrics Shaders](https://github.com/better-lyrics/shaders)**: Motor gráfico WebGL de gradientes animados fluidos y distorsión de dominio impulsado por `@kawarp/core`.
* **[LRCLIB](https://lrclib.net/)**: Base de datos abierta y API pública de letras sincronizadas creada por **Tran Duc Luan**.
* **[MusicBrainz Picard](https://picard.musicbrainz.org/)**: Etiquetador oficial de música de la Fundación MetaBrainz.

---

## 🛠️ Guía de Instalación en Feishin Desktop

### Método 1: Inyección Automática (Recomendado para Windows)
1. Cerrar Feishin Desktop.
2. Copiar el script `feishin-karaoke.js` a la carpeta de recursos de Feishin:
   - **Windows:** `C:\Users\<TuUsuario>\AppData\Local\Programs\feishin\resources\`
   - **Linux:** `/opt/Feishin/resources/` o `~/.local/share/Feishin/resources/`
3. Ejecutar el script parcheador en Python para inyectar la referencia en `app.asar`:
   ```bash
   python patch_feishin.py
   ```
4. Abrir Feishin Desktop.

---

### Método 2: Instalación Manual mediante `asar` (Cross-platform)
1. Localizar la carpeta `resources` de Feishin Desktop en tu sistema operativo.
2. Copiar `feishin-karaoke.js` a `resources/feishin-karaoke.js`.
3. Desempaquetar el archivo `app.asar`:
   ```bash
   npx asar extract app.asar app
   ```
4. Abrir el archivo `app/out/renderer/index.html` en un editor de texto e inyectar la siguiente etiqueta dentro de `<head>`:
   ```html
   <script src="../../feishin-karaoke.js"></script>
   ```
5. Volver a empaquetar la aplicación:
   ```bash
   npx asar pack app app.asar
   ```
6. Iniciar Feishin Desktop.

---

## 📄 Licencia

Licencia pública **GPL-2.0-or-later** en cumplimiento con el ecosistema de código abierto de Feishin y Picard.
