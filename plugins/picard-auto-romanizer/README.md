# Auto Romanizer Plugin for MusicBrainz Picard

A native plugin for [MusicBrainz Picard](https://picard.musicbrainz.org/) that automatically translates Japanese titles, artists, and albums (Kanji/Kana) to Romaji when metadata is loaded, while preserving native original metadata (`ORIGINALTITLE`, `ORIGINALARTIST`, `ORIGINALALBUM`) for synced lyrics (.lrc) matching.

## Features
- **100% Windowless & Silent**: Runs natively in memory without popping up console/CMD windows.
- **Metadata Preservation**: Writes native original titles into standard audio tags (`ORIGINALTITLE`, `ORIGINALARTIST`, `ORIGINALALBUM`).
- **LRCLIB Compatible**: Ensures lyrics plugins (like `Lrclib Lyrics`) fetch exact synced lyrics (.lrc) without 404 errors.

## Installation
1. Download `auto_romanizer.zip` (or compress the `auto_romanizer` folder into a `.zip`).
2. Copy `auto_romanizer.zip` to Picard's plugins directory:
   - **Windows**: `%LOCALAPPDATA%\MusicBrainz\Picard\plugins\`
   - **Linux**: `~/.config/MusicBrainz/Picard/plugins/`
   - **macOS**: `~/Library/Preferences/MusicBrainz/Picard/plugins/`
3. Enable the plugin under **Options > Plugins > Auto Romanizer**.

## License
GNU General Public License v2.0 (GPL-2.0).
