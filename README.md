# Downloader

GUI tools for searching media and managing downloads (HTTP + torrents) built with PyQt5.

## Apps
- `media_search.py`: search anime, manga, visual novels, and games; collect links; hand selected entries to `download_manager.py`.
- `download_manager.py`: single-instance download manager for direct links and torrents.
- `mod_search.py`: Factorio mod browser with dependency-aware cart and handoff to `download_manager.py`.

## Current capabilities

### `media_search`
- Search sources:
  - Anime and manga via Jikan/MyAnimeList
  - Visual novels via VNDB Kana
  - Games via RAWG
- Download link collection:
  - Anime and manga: Aniteca, Nyaa, 1337x
  - Games: ElAmigos, FitGirl, SteamRIP
  - Visual novels: Nyaa, 1337x, ElAmigos, FitGirl, SteamRIP
- Per-category download folders from config.
- Link selector groups releases by subgroup metadata instead of repeating that text on every item.
- Selected entries are sent to `download_manager.py` with:
  - `url`
  - `path`
  - `password`
  - `title`
- Selected links for one result are grouped into a subfolder named after the result title.
- RAWG Factorio entries expose `Ver mods` and open `mod_search.py`.

### `download_manager`
- Single-instance window with local IPC handoff from secondary launches.
- Accepts:
  - direct CLI URLs
  - magnet links
  - `.torrent` URLs
  - JSON entry lists
- Persists download session to `%APPDATA%\\MediaSearchPrototype\\download_state.json`.
- Restores saved items on startup, including waiting, cancelled, downloading, finished, and torrent entries.
- Scheduler respects `max_parallel_downloads` for regular downloads and does not resolve more direct links once the parallel limit is full.
- UI states:
  - `En espera`
  - `Resolviendo`
  - `Descargando`
  - `Completado`
  - `Cancelado`
  - `Error`
- Cancelled items can be resumed or removed from the session.
- Password hints are also appended to `__passwords__.txt` inside the target folder.
- Optional post-download extraction for direct-download archives using 7-Zip or WinRAR.
- Optional deletion of the archive after successful extraction.

### `mod_search`
- Focused on Factorio today.
- Browse updated, downloaded, and trending pages.
- Search mods.
- Open mod pages in tabs inside the app.
- Add mods to a cart and resolve dependencies before sending downloads to `download_manager.py`.
- Render sanitized HTML/Markdown descriptions locally.

## Supported direct-link handling in `download_manager`
- Direct file URLs with common archive/installer/document extensions.
- MediaFire:
  - files via API first, HTML fallback
  - folders via API first, HTML fallback
- Google Drive:
  - files via API/session-based direct resolution
  - folders by clicking `Descargar todo` in the embedded browser and capturing the generated ZIP request
- 4shared
- FileCrypt containers and link pages
- Interactive host automation:
  - Rapidgator
  - DDownload
  - DDL.to
  - FuckingFast
  - DataNodes
  - MegaDB
  - GoFile
- Torrents and magnet links through Aria2 RPC

## Requirements
- Python 3.10+ recommended
- Windows, Linux, or macOS with GUI support
- Python packages:
  - PyQt5
  - PyQtWebEngine
  - requests
  - beautifulsoup4

Optional:
- `aria2c` for torrents. The repo already includes `aria2c.exe` for Windows.
- `7z.exe` or WinRAR for archive extraction.

## Install
```bash
pip install PyQt5 PyQtWebEngine requests beautifulsoup4
```

## Usage

### Media search
```bash
python media_search.py
```

Notes:
- Searchable categories currently implemented: `Anime`, `Manga`, `Visual Novel`, `Games`.
- `General` exists in the UI but does not dispatch a search worker yet.
- RAWG uses the API key embedded in [media_search/game_sources.py](/C:/Users/Nexxus/Desktop/Downloader/media_search/game_sources.py).
- VNDB search, image caching, and trailer launch are handled from [media_search/workers.py](/C:/Users/Nexxus/Desktop/Downloader/media_search/workers.py).

### Download manager
Open the manager:
```bash
python download_manager.py
```

Pass one or more URLs:
```bash
python download_manager.py "https://www.mediafire.com/file/..." "magnet:?xt=urn:btih:..."
```

Pass a JSON file containing entries:
```bash
python download_manager.py input.json
```

Expected JSON shape:
```json
[
  {
    "url": "https://example.com/file",
    "path": "C:\\Users\\User\\Downloads\\Game",
    "password": "",
    "title": "Game"
  }
]
```

### Mod browser
```bash
python mod_search.py --game factorio
```

## Configuration
Stored in `%APPDATA%\\MediaSearchPrototype\\config.json`.

Current config fields:
- `folder_path`
- `general_folder_path`
- `anime_folder_path`
- `manga_folder_path`
- `vn_folder_path`
- `games_folder_path`
- `open_on_finish`
- `auto_extract_archives`
- `delete_archive_after_extract`
- `max_parallel_downloads`
- `factorio_mods_path`
- `minecraft_mods_path`

## Session data
- Download session: `%APPDATA%\\MediaSearchPrototype\\download_state.json`
- Media caches also live under `%APPDATA%\\MediaSearchPrototype\\...`

The saved session currently preserves:
- target path
- original URL
- resolved direct links
- password
- state
- progress
- torrent identifiers
- extraction state

## Tests
Run with:
```bash
python -m pytest -q
```

Current automated coverage is focused on parsing and data transformation:
- Aniteca mapping
- anime torrent search parsing
- game source parsing
- mod description rendering

There is no automated GUI/integration coverage yet for `download_manager` scheduling, IPC, or browser-driven host flows.

## TODO / what still needs completion
- Implement more downstream host flows in [download_manager/browser.py](/C:/Users/Nexxus/Desktop/Downloader/download_manager/browser.py) for mirrors surfaced by ElAmigos, FitGirl, and SteamRIP when the manager still opens the page but fails to capture a final file URL.
- Add automated tests for `download_manager`:
  - session restore
  - scheduler slot usage
  - resume/cancel/delete flows
  - torrent reconciliation
  - extraction lifecycle
- Add integration tests or fixtures for `media_search` to `download_manager` handoff JSON shape.
- Decide whether the `General` category in `media_search` should be implemented or removed from the UI.
- Expand `mod_search` beyond Factorio, or document it as permanently Factorio-only.
- Review whether completed torrent downloads should also participate in the archive-extraction workflow; today extraction is only triggered for regular direct-download entries.
- Move embedded API keys and other site-specific constants to user configuration or environment-based overrides.

## Notes
- If a site search finds mirrors but `download_manager` cannot reach a final direct file URL, treat that as a capability gap in `download_manager`, not in `media_search`.
- VikingFile remains intentionally disabled because the embedded browser flow does not load it correctly yet.
- Use only sources you are authorized to access and comply with local laws and site terms.
