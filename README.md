# Downloader

GUI tools for searching media and managing downloads (HTTP + torrents) built with PyQt5.

## Repo layout
- `media_search.py`: entrypoint for the media search UI.
- `download_manager.py`: entrypoint for the download manager UI.
- `mod_search.py`: entrypoint for the game mods browser UI.
- `config.py`: shared config loading/saving.
- `media_search/`: media search package.
- `download_manager/`: download manager package, direct-link extraction, torrents, dialogs, workers.
- `mod_search/`: mod browser package.

## What it does
- Search anime/manga (Jikan/MyAnimeList), visual novels (VNDB Kana), and games (RAWG)
- Collect download links from:
  - Aniteca (direct links)
  - Nyaa / 1337x (magnet links)
  - ElAmigos
  - FitGirl
  - SteamRIP
- Browse game mods and queue their downloads with dependency resolution
- Launch a download manager UI for:
  - Direct downloads (MediaFire, Google Drive, 4shared, direct file URLs)
  - Torrents and magnet links via Aria2 RPC

## Requirements
- Python 3.10+ recommended
- Windows, Linux, or macOS with GUI support
- Python packages:
  - PyQt5
  - PyQtWebEngine
  - requests
  - beautifulsoup4

Optional (for torrents):
- aria2c available on PATH, or `aria2c.exe` placed next to the scripts.
  - This repo already includes `aria2c.exe` for Windows.

## Install
```bash
pip install PyQt5 PyQtWebEngine requests beautifulsoup4
```

## Usage

### 1) Media search UI
```bash
python media_search.py
```
Notes:
- Set your TMDb key in `media_search/sources.py` at `TMDB_API_KEY`.
- RAWG uses the API key embedded in `media_search/game_sources.py`. Replace it with your own if needed.
- Anime download sources live in `media_search/anime_sources.py` and the Aniteca client in `media_search/aniteca.py`.
- Game download sources live in `media_search/game_sources.py`.
- Visual novels use the public VNDB Kana API.
- Supported game entries can expose a `Ver mods` button that opens the mod browser.

### 1.1) Mod browser
```bash
python mod_search.py --game factorio
```
Notes:
- Lets you browse, search, inspect, and queue videogame mods.
- The current built-in implementation focuses on Factorio.
- Resolves mod dependencies before sending downloads to `download_manager.py`.
- Uses the configured game mod path as destination when applicable.

### 2) Download manager UI
Open the link input UI:
```bash
python download_manager.py
```

Pass one or more URLs directly:
```bash
python download_manager.py "https://www.mediafire.com/file/..." "magnet:?xt=urn:btih:..."
```

Pass a JSON file containing a list of entries:
```bash
python download_manager.py input.json
```

Expected JSON shape:
```json
[
  { "url": "https://example.com/file", "path": "Subfolder", "password": "" }
]
```

## Configuration
Settings are stored in:
`%APPDATA%\\MediaSearchPrototype\\config.json`

Config fields:
- `folder_path`: default download folder
- `open_on_finish`: open folder after download completes
- `max_parallel_downloads`: max concurrent HTTP downloads
- `factorio_mods_path`: default folder for Factorio mods
- `minecraft_mods_path`: extra mod folder path stored by the mod settings dialog

## Supported direct-link hosts
- MediaFire (files and folders)
- Google Drive
  - file links download directly
  - folder links open the embedded browser, click `Descargar todo`, capture the generated ZIP URL, and download it through the app
- 4shared
- FileCrypt containers and link pages
- Rapidgator / DDownload / DDL.to / FuckingFast / DataNodes through the embedded browser flow
- MegaDB pages can continue after the captcha, but the user currently needs to click `Download` manually once the site enables it
- Direct file URLs with common archive/installer/document extensions (`.zip`, `.rar`, `.7z`, `.exe`, `.msi`, `.pdf`, etc.)

## Download source routing
- Anime / manga downloads search: Aniteca, Nyaa, 1337x
- Game downloads search: ElAmigos, FitGirl, SteamRIP
- Visual novel downloads search: Nyaa, 1337x, ElAmigos, FitGirl, SteamRIP

## TODO / current limitations
- `media_search` can surface mirrors from ElAmigos, FitGirl and SteamRIP that are not yet fully implemented by `download_manager`.
- Known examples are host-specific pages that still need additional extraction or click automation beyond the currently supported flow.
- If a mirror opens but no final direct link is captured, that host still needs implementation in `download_manager/browser.py`.
- SteamRIP currently contributes outgoing mirror URLs; whether each one downloads successfully depends on the host behind that mirror and the support already present in `download_manager`.
- VikingFile mirrors are intentionally hidden for now because the page is not loading correctly inside `download_manager`.

## Notes
- Torrents require Aria2 RPC at `http://localhost:6800/jsonrpc`.
  The app will attempt to start Aria2 automatically.
- Closing `download_manager.py` asks for confirmation only when there is active work in progress.
- `download_manager.py` remains single-instance and forwards new entries to the already-open window.
- Use only sources you are authorized to access and comply with local laws and site terms.

## Tests
```bash
pip install pytest
pytest
```
