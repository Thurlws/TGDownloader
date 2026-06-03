# TGDownloader

A personal music library manager with a Telegram client, automatic album sorting, duplicate detection, a full library browser, and a dark GUI served from a local web server.

![Python](https://img.shields.io/badge/python-3.11+-blue?style=flat-square)
![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey?style=flat-square)

---

> **⚠️ Legal notice**
>
> TGDownloader is a **Telegram client and local music library manager**. It does not host, distribute, or provide access to any copyrighted content. It communicates only with Telegram bots that you configure yourself — bots that you already use independently of this software.
>
> **Intended use:** Managing audio files you are legally entitled to possess — for example, files received through your own Telegram bots for music you own or have licensed.
>
> The authors do not endorse, encourage, or facilitate copyright infringement or music piracy in any form. You are solely responsible for ensuring that your use of this software and any bots you connect it to complies with the laws in your jurisdiction and with the terms of service of any platform involved. This software is provided as-is with no warranties.

---

## What it does

TGDownloader is a Telegram client and local music library manager. It connects to your Telegram account, interacts with a bot of your choosing, receives audio files, and automatically organises them into `Artist / Album / track.flac` folders in your music library. It skips files you already have, detects duplicates by audio hash, and keeps a manifest so it never processes the same URL twice.

The interface is a local web app that opens in your browser (or a Chromium app window) and behaves like a native desktop app.

---

## Features

- **Queue manager** — paste URLs, drag to reorder, bulk import, save/load named sessions
- **Deezer search** — browse album metadata and art directly from inside the app (`Ctrl+K`)
- **Automatic sorting** — reads audio tags with Mutagen and sorts into `Artist/Album/` folders; fuzzy-matches existing folders to handle naming variants
- **Duplicate detection** — filename check + MD5 hash index so renamed duplicates are caught too
- **Library browser** — visual discography view with album art, tracks what you have vs what's missing
- **Mini audio player** — preview local files and Deezer 30s previews directly in the app
- **Download history** — searchable, filterable, with favorites, review-later tags, and notes
- **Live progress** — real-time speed graph, ETA, per-file progress bar
- **Telegram auth** — full in-browser login flow including 2FA, no terminal prompts ever
- **Audio quality selector** — configure the bot's quality setting without leaving the app
- **Single-instance** — launching a second instance just focuses the existing browser window

---

## Quick start

### Run from source

**1. Clone and install dependencies**

```bash
git clone https://github.com/thurlws/tgdownloader.git
cd tgdownloader
pip install telethon mutagen cryptg ffmpeg
```

**2. Get Telegram API credentials**

Go to [my.telegram.org/auth](https://my.telegram.org/auth), log in, click **API Development Tools**, and create an app. You'll get an `api_id` (number) and `api_hash` (32-char hex string).

**3. Set up the config file**

Copy the example config and fill in your details:

```bash
cp tg_audio_config_example.json tg_audio_config.json
```

Edit `tg_audio_config.json` and set at minimum:
- `api_id` and `api_hash` from step 2
- `bot_username` — the `@username` of the Telegram bot you use to receive audio files
- `home_music_folder` — the full path to your music library

**4. Launch**

```bash
python TGDownloader_bundled.py
```

This starts the local web server and opens the GUI in your browser. There is no separate GUI process — the browser interface is served directly by the Python script.

**5. Connect Telegram**

Click the **TG** button in the top-right corner and log in with your phone number. This creates a local session file so you only do it once.

**6. Point it at your library**

- Confirm your home music folder is set in the left panel
- Add URLs to the queue and hit **▶ Run**

---

### Run as an executable (Windows)

Pre-built releases are published on the [Releases](../../releases) page. Extract and run `TGDownloader.exe` — no Python required.

To build it yourself, `TGDownloader.spec` is included in the repository root:

```bash
pip install pyinstaller telethon mutagen cryptg ffmpeg
pyinstaller TGDownloader.spec
```

Output lands in `dist/TGDownloader/`.

---

## Requirements

| Requirement | Notes |
|---|---|
| Python 3.11+ | 3.12/3.13 recommended |
| [telethon](https://github.com/LonamiWebs/Telethon) | Telegram client |
| [mutagen](https://mutagen.readthedocs.io/) | Audio tag reading |
| [cryptg](https://github.com/cher-nov/cryptg) | Optional but strongly recommended — without it downloads are ~50x slower and may crash on Python 3.14 |
| [ffmpeg](https://ffmpeg.org/download.html) | Required for audio quality conversion (FLAC ↔ MP3). Must be on your system PATH. |
| A Telegram account | Free |
| Telegram API credentials | Free — [my.telegram.org](https://my.telegram.org) |

---

## File layout

```
TGDownloader_bundled.py        ← entry point (run this, or build to .exe)
TGDownloader_GUI.py            ← HTTP + WebSocket server, all API endpoints
TGDownloader.py                ← download engine, Telegram client, album sorter
gui.html                       ← the entire frontend (single file)
setup_wizard.html              ← first-run credential setup wizard
TGDownloader.spec              ← PyInstaller build spec
tg_audio_config_example.json  ← config template — copy to tg_audio_config.json to get started

# Generated at runtime (gitignored):
tg_audio_config.json      ← your settings + API credentials
tg_audio_session.session  ← Telegram session (keep this safe)
tg_sessions.json          ← saved queue sessions
tgdownloader_debug.log    ← debug log (view in the Debug tab)
hash_cache.json           ← library hash index cache
album_id_cache.json       ← Deezer album ID cache
```

---

## Configuration

All settings are accessible via **Settings** in the app. The config file is `tg_audio_config.json`:

```json
{
  "api_id": 12345678,
  "api_hash": "your_32_character_hex_hash_here",
  "home_music_folder": "/path/to/your/music",
  "bot_username": "@YourBotUsername",
  "reply_timeout": 30,
  "queue_wait_timeout": 120,
  "inter_file_idle_timeout": 8,
  "bot_busy_wait": 10,
  "bot_busy_retries": 12,
  "max_queue": 10,
  "ui_scale": 1.0,
  "target_quality": "FLAC"
}
```

`target_quality` accepts `"FLAC"`, `"MP3 320"`, or `"MP3 128"`. Files are converted locally by ffmpeg after each download — whatever format your bot sends gets normalised to your chosen target.

---

## Keyboard shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl+K` | Search |
| `Ctrl+Enter` | Run / Stop |
| `Ctrl+S` | Save queue as session |
| `Ctrl+L` | Clear log |
| `Ctrl+/` | Show all shortcuts |
| `Space` | Play / pause mini player |
| `1` – `5` | Switch tabs |
| `↑ ↓` | Navigate queue items |
| `Shift+↑↓` | Move queue item up/down |
| `Del` | Remove focused queue item |
| `Right-click` | Context menu on queue item |

---

## Privacy & security

- Your API credentials and Telegram session are stored **only on your machine**
- Nothing is sent to any server other than Telegram's own infrastructure and the Deezer public API (for album art / metadata)
- The local web server binds to `127.0.0.1` only — it is not accessible from other machines on your network

---

## Troubleshooting

**Downloads are very slow**
Install `cryptg`: `pip install cryptg`. Without it Telethon uses pure-Python AES which is ~50x slower.

**Bot says "please wait" / busy errors**
The bot is handling another user's request. TGDownloader retries automatically (configurable via `bot_busy_retries` and `bot_busy_wait`).

**"Not logged in to Telegram" error**
Click the **TG** button in the toolbar and complete the login flow.

**Files not sorting into the right album folder**
The sorter reads the `album` tag from the file. If the bot sends untagged files they land in `_Unknown Album/`. Re-tag the files and move them manually.

**Audio quality conversion not working**
Install ffmpeg from [ffmpeg.org](https://ffmpeg.org/download.html) and ensure the `ffmpeg` binary is on your system PATH. On Windows, adding the ffmpeg `bin/` folder to your PATH environment variable is usually sufficient.

**App won't open / port in use**
Port `7842` is the HTTP server. If something else is using it, kill that process or change `HTTP_PORT` in `TGDownloader_GUI.py`.

---

## License

MIT