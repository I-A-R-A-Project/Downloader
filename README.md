# Downloader

Herramientas GUI para buscar media y gestionar descargas (HTTP + torrents)
construidas con PyQt6.

## Apps

- `media_search.py`: busca anime, manga, visual novels y juegos; recolecta
  links; entrega las entradas seleccionadas a `download_manager.py`.
- `download_manager.py`: gestor de instancia única para links directos y
  torrents.
- `mod_manager/`: un gestor web de mods en Vite + React con rutas `/minecraft`
  y `/factorio`, ahora mantenido como app separada a nivel raíz.
- `mod_search.py` y `mod_search/`: navegador legacy de mods en PyQt.

## Mod manager

La app web canónica es `mod_manager/` a nivel raíz. Comparte una plantilla de
React y un build de Vite para ambas rutas:

- `/minecraft` usa Modrinth para búsqueda, filtros, selección de archivo/
  versión, dependencias necesarias, carrito, descargas directas, exportación
  JSON y handoff opcional al Downloader.
- `/factorio` usa listados de Mod Portal a través de un proxy CORS configurable,
  `re146.dev` para metadatos/dependencias y `mods-storage.re146.dev` para
  archivos. Añade filtros de Factorio, resolución de dependencias y manejo
  opcional de `factorio-current.log`.

Desde `IARA/mod_manager`:

```bash
npm install
npm run dev
npm run build
npm run preview
```

Las URLs de desarrollo son `/minecraft` y `/factorio`. `npm run build` genera
archivos estáticos en `dist/`, incluyendo índices de ruta para navegación
directa. Publicá `dist/` en un host estático; `vercel.json` reescribe ambos
prefijos para Vercel.

### Proxy

La URL del proxy y su estado activo son configurables desde el diálogo de
Settings de cada ruta y se guardan en el `localStorage` del navegador.
Minecraft usa Modrinth directamente por defecto y considera el proxy opcional.
Factorio activa el proxy desplegado
`https://factoriomods.supermaty97.workers.dev` por defecto porque Mod Portal
normalmente necesita CORS. Los proxies deben exponer `/fetch?url=<encoded-url>`
y enviar headers CORS.

### Storage del navegador

Claves canónicas:

- Minecraft: `modrinthSearchSettings`, `modrinthSearchCart`
- Factorio: `modSearchSettings`, `modSearchCart`

La app React también lee aliases antiguos
(`minecraftModSettings`/`minecraftModCart` y
`factorioModSettings`/`factorioModCart`), normaliza nombres viejos del carrito
y escribe el resultado en las claves canónicas. No borra las claves viejas.
El storage está acotado al origen del navegador.

### Protocolo de handoff al Downloader

El handoff del carrito crea entradas con forma
`{url, path, password, title}`, codifica la lista JSON en UTF-8 base64url y
navega a:

```text
iara-downloads://add-mods?payload=<base64url-json>
```

Esto requiere el protocol handler instalado por `installer/`. El receptor
decodifica el payload y abre el ejecutable del Downloader. Para construir el
instalador Windows, ver `..\installer\README.md`. El fallback fiable sigue
siendo `Export JSON` y luego:

```bash
python download_manager.py modrinth_cart.json
python download_manager.py mod_search_cart.json
```

Las páginas estáticas viejas fueron removidas. Los cambios web nuevos van en
`mod_manager/src/`, y el workflow legacy de `mod_search.py` queda fuera del
flujo React.

## Capacidades actuales

### `media_search`

- Fuentes de búsqueda:
  - Anime y manga vía Jikan/MyAnimeList
  - Visual novels vía VNDB Kana
  - Juegos vía RAWG
- Recolección de links de descarga:
  - Anime y manga: Aniteca, Nyaa, 1337x
  - Juegos: ElAmigos, FitGirl, SteamRIP
  - Visual novels: Nyaa, 1337x, ElAmigos, FitGirl, SteamRIP
- Carpetas de descarga por categoría desde configuración.
- Las entradas seleccionadas se envían a `download_manager.py` con `url`,
  `path`, `password` y `title`.

### `download_manager`

- Ventana de instancia única con IPC local desde lanzamientos secundarios.
- Acepta URLs directas de CLI, magnet links, URLs `.torrent` y listas JSON.
- Persiste sesiones en `%APPDATA%\\IARA\\Downloader\\download_state.json`.
- El scheduler respeta `max_parallel_downloads` para descargas regulares.
- Soporta cancelación/reanudación, extracción opcional de archivos y borrado
  opcional del archivo comprimido.

## Requisitos

- Python 3.10+ recomendado
- Windows, Linux o macOS con soporte GUI
- `PyQt6`, `PyQt6-WebEngine`, `requests`, `beautifulsoup4`
- Opcional: `aria2c` para torrents; `7z.exe` o WinRAR para extracción

## Instalación y uso

```bash
pip install -r requirements.txt
python media_search.py
python download_manager.py
python mod_search.py --game factorio
```

## Configuración y datos de sesión

`%APPDATA%\\IARA\\Downloader\\config.json` guarda carpetas de descarga,
configuración de extracción, concurrencia HTTP y paths legacy de mods. Las
rutas de mods web quedan en `localStorage` del navegador.

La sesión de descarga conserva rutas objetivo, URLs originales/resueltas,
passwords, estado, progreso, identificadores torrent y estado de extracción.

## Tests

```bash
python -m pytest -q
```

La cobertura principal es parsing y transformación de datos. No hay cobertura
completa de integración para scheduler, IPC, restore de sesión, resolución de
hosts por browser ni reconciliación de torrents.

## Limitaciones conocidas

- Algunos mirrors que aparecen en `media_search` todavía no tienen resolución
  confiable del host final en `download_manager/browser.py`; eso se trata como
  gap de capacidad del Downloader.
- `media_search` sigue dejando una categoría `General` sin worker de búsqueda.
- Los torrents completados no entran al flujo regular de extracción.
- Hay API keys y constantes del sitio embebidas en el código fuente.
