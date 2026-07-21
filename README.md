# Navidrome & Picard Plugins Monorepo

Este monorepo en Go/TinyGo y Python está diseñado para desarrollar, compilar a WebAssembly (`wasip1`), empaquetar y desplegar automáticamente el ecosistema completo de plugins para **Navidrome** y **MusicBrainz Picard**.

---

## 📁 Estructura del Monorepo

```
navidrome-plugins/
├── plugins/
│   ├── telegram-plugin/        # Plugin Navidrome: Notificaciones de nuevos álbumes a Telegram
│   │   ├── main.go
│   │   ├── manifest.json
│   │   └── go.mod
│   ├── lyrics-plugin/          # Plugin Navidrome: Letras nativas (tags ID3/SYLT/USLT) y proveedores online
│   │   ├── main.go
│   │   ├── manifest.json
│   │   ├── tags/
│   │   │   └── tags.go
│   │   ├── providers/
│   │   │   └── providers.go
│   │   └── go.mod
│   ├── picard-auto-romanizer/  # Plugin MusicBrainz Picard: Romanizador automático (Kana/Kanji a Romaji)
│   │   ├── auto_romanizer/
│   │   │   └── __init__.py
│   │   └── README.md
│   └── picard-deduplicator/    # Plugin MusicBrainz Picard: Previene y limpia duplicados (1) al guardar
│       ├── picard_deduplicator/
│       │   └── __init__.py
│       └── README.md
├── Makefile                    # Targets globales de compilación, empaquetado y despliegue
├── scripts/
│   └── deploy.sh               # Script de despliegue vía SCP a contenedor LXC
└── README.md
```

---

## 🛠️ Requisitos Previos

- **Go**: 1.22+
- **TinyGo**: 0.30+ (para compilación rápida de WASM)
- **zip**: Utilidad para empaquetar `.ndp` y `.zip`
- **scp / ssh**: Para desplegar paquetes en el servidor LXC destino

---

## 🚀 Comandos del Makefile

### Compilación Local

- **Compilar Plugin de Telegram**:
  ```bash
  make build-telegram
  ```

- **Compilar Plugin de Lyrics**:
  ```bash
  make build-lyrics
  ```

- **Compilar Todos los Plugins de WASM**:
  ```bash
  make build-all
  ```

---

### Empaquetado (`.ndp` y `.zip`)

Genera los paquetes `.ndp` para Navidrome (`navidrome-telegram.ndp` y `navidrome-lyrics-plugin.ndp`) y el `.zip` para Picard (`auto_romanizer.zip`):

```bash
make package
```

También puedes empaquetar plugins de forma individual:
```bash
make package-telegram
make package-lyrics
make package-picard
```

---

### Despliegue Automático

Envía los paquetes `.ndp` generados al servidor destino vía `scp`:

```bash
make deploy DEST=usuario@servidor-lxc:/var/lib/navidrome/plugins
```

---

## 🧼 Limpieza

Elimina binarios `.wasm`, paquetes `.ndp` y zips generados:

```bash
make clean
```
