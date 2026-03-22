# Downloader

GUI tools for searching media and managing downloads (HTTP + torrents) built with PyQt5.

## What it does
- Search anime/manga (Jikan/MyAnimeList), games (RAWG), and collect download links from:
  - Aniteca (direct links)
  - Nyaa / 1337x (magnet links)
- Browse Factorio mods and queue their downloads with dependency resolution
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
- Set your TMDb key in `media_search.py` at `TMDB_API_KEY`.
- RAWG uses the API key embedded in `media_search.py`. Replace it with your own if needed.
- Factorio entries expose a `Ver mods` button that opens the mod browser.

### 1.1) Mod browser
```bash
python mod_search.py --game factorio
```
Notes:
- Lets you browse, search, inspect, and queue Factorio mods.
- Resolves mod dependencies before sending downloads to `download_manager.py`.
- Uses the configured Factorio mods folder as destination.

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
- Direct file URLs with common archive/installer/document extensions (`.zip`, `.rar`, `.7z`, `.exe`, `.msi`, `.pdf`, etc.)

## Notes
- Torrents require Aria2 RPC at `http://localhost:6800/jsonrpc`.
  The app will attempt to start Aria2 automatically.
- Closing `download_manager.py` asks for confirmation only when there is active work in progress.
- Use only sources you are authorized to access and comply with local laws and site terms.

## Tests
```bash
pip install pytest
pytest
```
