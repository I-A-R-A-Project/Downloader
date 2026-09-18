# Downloader

Herramientas para buscar media y gestionar descargas HTTP, torrents y
contenido de YouTube. La interfaz principal usa PyQt6 y el gestor también
ofrece un modo TUI para ejecución desde terminal.

## Aplicaciones

- `media_search.py`: busca anime, manga, visual novels y juegos; recopila
  enlaces y los envía al gestor de descargas.
- `download_manager.py`: gestor de descargas de instancia única con interfaz
  GUI o modo TUI.

El antiguo navegador de mods (`mod_search.py` y `mod_search/`) fue retirado de
este repositorio.

## Capacidades

### Búsqueda de media

- Anime y manga mediante Jikan/MyAnimeList.
- Visual novels mediante VNDB Kana.
- Juegos mediante RAWG.
- Enlaces para anime y manga desde Aniteca, Nyaa y 1337x.
- Enlaces para juegos y visual novels desde ElAmigos, FitGirl, SteamRIP,
  Nyaa y 1337x.
- Carpetas de descarga configurables por categoría.

### Gestor de descargas

- URLs directas, enlaces magnet, URLs `.torrent` y listas JSON.
- Descargas regulares con resolución mediante navegador embebido.
- Torrents mediante Aria2 RPC.
- Videos y playlists de YouTube mediante `yt-dlp`.
- Límite configurable de descargas simultáneas.
- Cancelación, reanudación y persistencia de sesión.
- Extracción opcional de archivos `.zip`, `.rar` y `.7z` con 7-Zip o WinRAR.
- Handoff desde procesos secundarios mediante IPC local; solo queda una
  ventana principal abierta.

Hosts con automatización integrada incluyen MediaFire, Google Drive, 4shared,
FileCrypt, Rapidgator, DDownload, DDL.to, FuckingFast, DataNodes, MegaDB y
GoFile. La disponibilidad depende del sitio y de sus cambios.

## Requisitos

- Python 3.10 o superior.
- PyQt6 y PyQt6-WebEngine.
- `requests` y `beautifulsoup4`.
- `aria2c` para torrents; el ejecutable puede estar en PATH o en el
  repositorio.
- `7z.exe` o WinRAR para extracción opcional.
- `yt-dlp.exe` o `yt-dlp` para YouTube. `ffmpeg` es opcional y permite unir
  video y audio con mejor calidad.

Instalar dependencias:

```bash
pip install -r requirements.txt
```

## Uso

Buscar media:

```bash
python media_search.py
```

Abrir gestor GUI:

```bash
python download_manager.py
python download_manager.py --gui
```

Ejecutar gestor en terminal:

```bash
python download_manager.py --tui
python download_manager.py --set-default-tui
python download_manager.py --set-default-gui
```

Pasar enlaces directamente:

```bash
python download_manager.py "https://example.com/file.zip" "magnet:?xt=urn:btih:..."
```

Pasar una lista JSON:

```bash
python download_manager.py downloads.json
```

Formato mínimo de entrada:

```json
[
  {
    "url": "https://example.com/file.zip",
    "path": "C:\\Users\\User\\Downloads\\Game",
    "password": "",
    "title": "Game"
  }
]
```

Una segunda ejecución del gestor entrega sus enlaces a la instancia principal
mediante IPC local. Si se usa una lista JSON, las entradas se incorporan a la
sesión existente.

## Configuración y datos

El archivo de configuración se guarda en:

```text
Windows: %APPDATA%\IARA\Downloader\config.json
Linux:   $XDG_DATA_HOME/IARA/Downloader/config.json
```

También se puede cambiar la raíz de datos con `IARA_DATA_DIR`. Configuración
incluye carpetas destino, modo por defecto (`gui` o `tui`), concurrencia,
extracción automática, borrado de archivos extraídos y rutas de Factorio o
Minecraft conservadas para compatibilidad.

La sesión se guarda en
`%APPDATA%\IARA\Downloader\download_state.json` en Windows, o en el directorio
de datos equivalente en otros sistemas. Conserva URLs originales y resueltas,
rutas, contraseñas, progreso, estado de torrents y estado de extracción.

## Tests

```bash
python -m pytest -q
```

La cobertura automatizada se concentra en parsing, transformación de datos y
configuración. Todavía no hay cobertura completa para GUI, IPC, scheduler,
restore de sesión, resolución browser ni reconciliación de torrents.

## Limitaciones conocidas

- Algunos mirrors encontrados por `media_search` todavía no llegan a una URL
  final confiable en `download_manager`.
- La categoría `General` aparece en la interfaz, pero no tiene worker de
  búsqueda.
- La extracción posterior todavía no se aplica a torrents completados.
- Algunas API keys y constantes específicas de sitios permanecen embebidas en
  el código fuente.
