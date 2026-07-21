# navidrome-telegram — New Album Notifications Plugin

A [Navidrome](https://www.navidrome.org/) plugin that watches your music library for newly added albums and pushes a notification directly to one or more [Telegram](https://telegram.org/) chats/channels (with optional Subsonic cover-art image attachments).

## How it works

1. On startup (`nd_on_init`) the plugin schedules a recurring cron job (default: every minute).
2. On each tick it calls the internal Subsonic API (`getAlbumList2?type=newest`) to fetch the 20 most recently added albums.
3. It compares the results against the last known album ID stored in KVStore. Any albums that appear before (i.e. newer than) that watermark are considered new.
4. For each new album, it sends a notification directly to the configured Telegram chat(s) via the Telegram Bot API.
5. After all notifications are sent successfully, it updates the KVStore watermark. If a POST fails, the watermark is **not** updated, so the album will be retried on the next tick.

> **First run:** On the very first poll, the plugin establishes the watermark without sending any notifications, preventing a flood of alerts for your entire existing library.

## Requirements

- Navidrome with the plugin system enabled (see [plugin docs](https://www.navidrome.org/docs/usage/plugins/))
- [TinyGo](https://tinygo.org/getting-started/install/) to build the `.wasm` binary
- A Telegram Bot token and target Chat ID (or multiple IDs separated by commas).

## Build & install

```bash
# 1. Build the WebAssembly module
make build

# 2. Package as .ndp
make package          # produces navidrome-telegram.ndp

# 3. Copy to your Navidrome plugins folder
cp navidrome-telegram.ndp /path/to/navidrome/plugins/

# 4. Enable plugins in navidrome.toml (if not already)
# [Plugins]
# Enabled = true
# Folder  = "/path/to/plugins"
```

## Configuration

Set these values in the Navidrome web UI under **Settings → Plugins → Telegram New Album Notifications**.

| Key | Required | Description |
|---|---|---|
| `telegram_bot_token` | Yes | Bot token used to call the Telegram Bot API. |
| `telegram_chat_id` | Yes | Target chat ID or channel ID. Multiple IDs can be separated by commas (e.g. `-100123456,-100234567`). |
| `telegram_thread_id` | No | Optional Message Thread ID (Topic ID) for forum supergroups (e.g. `104674`). |
| `telegram_parse_mode` | No | Parse mode for the caption/message. Recommended: HTML, Markdown, or MarkdownV2. Default: HTML. |
| `subsonic_user` | Yes | Navidrome username the plugin uses for internal Subsonic API calls. |
| `subsonic_password` | No | Navidrome password used to generate token-authenticated getCoverArt URLs. |
| `subsonic_base_url` | No | Base URL used to build cover art URLs (e.g. `https://navidrome.example.com`). |
| `use_subsonic_cover_art` | No | If enabled and `image_url_template` is blank, generates cover art URLs automatically. |
| `poll_interval` | No | Cron expression override. Default: `*/1 * * * *` (every minute). |
| `message_title` | No | Template for the notification title. Placeholders: `{album}`, `{artist}`, `{id}`, `{url}`. |
| `message_body` | No | Template for the notification body. Placeholders: `{album}`, `{artist}`, `{id}`, `{year}`, `{genre}`, `{songCount}`, `{duration}`, `{songs}`, `{url}`. |
| `image_url_template` | No | Optional static or parameterized image URL template to attach. |


### Message Templates & Placeholders

You can customize `message_title` and `message_body` templates using placeholders. These placeholders will be replaced with real album data before the notification is sent:

| Placeholder | Description | Example |
|---|---|---|
| `{album}` | The name/title of the album. | `Abbey Road` |
| `{artist}` | The artist of the album. | `The Beatles` |
| `{id}` | The internal Navidrome ID of the album. | `3UUAzR27PHLpGBUgwiQuZK` |
| `{artistId}` | The internal Navidrome ID of the artist. | `5x91Az...` |
| `{year}` | The release year of the album (if available). | `1969` |
| `{genre}` | The genre of the album. | `Rock` |
| `{discCount}` | Total number of discs in the album. | `2` |
| `{songCount}` | Total number of tracks/songs in the album. | `17` |
| `{duration}` | Total duration of the album formatted as `MM:SS` (or `H:MM:SS`). | `47:23` |
| `{songs}` | Formatted list of all songs in the album. Auto-formats as an HTML list if HTML parse mode is active. | *See format below* |
| `{url}` | The direct link to the album page in Navidrome (requires `subsonic_base_url` to be set). | `https://navidrome.example.com/#/album/3UUAz...` |

#### Song List Formatting (`{songs}`)
Depending on your selected `telegram_parse_mode`:
- **HTML**: Rendered as a structured HTML list `<ul>` / `<li>`:
  ```html
  <ul>
    <li><b>01.</b> Come Together (4:20)</li>
    <li><b>02.</b> Something (3:03)</li>
  </ul>
  ```
- **Markdown / MarkdownV2**: Rendered as plain text lines, with special characters escaped automatically for `MarkdownV2`:
  ```text
  1. Come Together (4:20)
  2. Something (3:03)
  ```


If the album contains tracks from multiple discs, the `{songs}` list will automatically group them by disc:
- **HTML**: Rendered using nested `<details open>` elements for each disc:
  ```html
  <details open>
    <summary>💿 Disco 1</summary>
    <ul>
      <li><b>01.</b> Song A (3:00)</li>
    </ul>
  </details>
  ```
- **Markdown / MarkdownV2**: Rendered with text disc headers:
  ```text
  *Disco 1*
  1. Song A (3:00)

  *Disco 2*
  1. Song B (4:00)
  ```

### User access

Because this plugin uses the `subsonicapi` permission, it requires at least one Navidrome user to be assigned to it. In the plugin settings page:

- Enable **Allow all users**, or
- Select a specific user from the list (a dedicated read-only account is recommended).

## KVStore key

| Key | Description |
|---|---|
| `last_seen_album_id` | Album ID of the most recently notified album. Delete this key (via the Navidrome debug tools) to reset the watermark. |

## Development

```bash
# Clean build artifacts
make clean

# Rebuild and repackage in one step
make
```
