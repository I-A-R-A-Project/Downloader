import os, random, re, requests, time
from PyQt5.QtCore import QObject, pyqtSignal, QRunnable, QThreadPool, pyqtSlot
from PyQt5.QtGui import QPixmap, QImage
from bs4 import BeautifulSoup
from settings_dialog import load_config, DEFAULT_CONFIG

FACTORIO_BASE = "https://mods.factorio.com"
MODINFO_URL = "https://re146.dev/factorio/mods/modinfo"

MAX_RETRIES = 100
RETRY_DELAY = 3
CHUNK_SIZE = 8192

class DownloadSignals(QObject):
    progress = pyqtSignal(int, int)  # index, percentage
    finished = pyqtSignal(int)       # index

class FileDownloader(QRunnable):
    def __init__(self, url, filename, index, signals, headers=None, cookies=None):
        super().__init__()
        self.url = url
        self.filename = filename
        self.index = index
        self.signals = signals
        self.headers = headers or {}
        self.cookies = cookies or {}

        QThreadPool.globalInstance().setMaxThreadCount(
            load_config().get("max_parallel_downloads", DEFAULT_CONFIG["max_parallel_downloads"])
        )

    def run(self):
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                downloaded = 0
                mode = 'wb'
                headers = dict(self.headers)

                if os.path.exists(self.filename):
                    downloaded = os.path.getsize(self.filename)
                    headers['Range'] = f'bytes={downloaded}-'
                    mode = 'ab'

                with requests.get(
                    self.url,
                    stream=True,
                    headers=headers,
                    cookies=self.cookies,
                    timeout=15
                ) as r:
                    total_length = r.headers.get('content-length')
                    if total_length is None:
                        total_length = 0
                    else:
                        total_length = int(total_length) + downloaded

                    dir_path = os.path.dirname(self.filename)
                    if dir_path:
                        os.makedirs(dir_path, exist_ok=True)

                    with open(self.filename, mode) as f:
                        for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                            if chunk:
                                f.write(chunk)
                                downloaded += len(chunk)
                                if total_length:
                                    percent = int((downloaded / total_length) * 100)
                                    self.signals.progress.emit(self.index, percent)

                self.signals.finished.emit(self.index)
                return

            except Exception as e:
                print(f"[{self.index}] ❌ Error en intento {attempt}: {e}")
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY * attempt)
                else:
                    self.signals.finished.emit(self.index)

class URLWorkerSignals(QObject):
    finished = pyqtSignal(int, str, str)  # index, title, link

class URLWorker(QRunnable):
    def __init__(self, index, title, url):
        super().__init__()
        self.index = index
        self.title = title
        self.url = url
        self.signals = URLWorkerSignals()

    @pyqtSlot()
    def run(self):
        try:
            link = self.url() if callable(self.url) else self.url
        except Exception as e:
            print(f"Error ejecutando función diferida para {self.title}: {e}")
            link = None
        self.signals.finished.emit(self.index, self.title, link)

class ImageLoadedSignal(QObject):
    finished = pyqtSignal(str, QPixmap)

class ImageLoaderWorker(QRunnable):
    def __init__(self, image_url):
        super().__init__()
        self.image_url = image_url
        self.signals = ImageLoadedSignal()

    def run(self):
        try:
            img_data = requests.get(self.image_url, timeout=10).content
            image = QImage()
            image.loadFromData(img_data)
            pixmap = QPixmap.fromImage(image)
            self.signals.finished.emit(self.image_url, pixmap)
        except Exception:
            self.signals.finished.emit(self.image_url, QPixmap())

class FullDetailsWorkerSignals(QObject):
    finished = pyqtSignal(str, str, str, str)

class FullDetailsWorker(QRunnable):
    def __init__(self, url):
        super().__init__()
        self.url = url
        self.signals = FullDetailsWorkerSignals()

    def run(self):
        second_title = None
        full_description = None
        trailer_url = None
        try:
            resp = requests.get(self.url, headers={"User-Agent": "Mozilla/5.0"})
            soup = BeautifulSoup(resp.text, "html.parser")
            second_title_tag = soup.select_one("p[class='title-english title-inherit']")
            if second_title_tag:
                second_title = second_title_tag.get_text(strip=True)
            desc_tag = soup.select_one("p[itemprop='description']")
            if desc_tag:
                full_description = desc_tag.get_text(strip=True)
            trailer_tag = soup.select_one("div.video-promotion a.iframe")
            if trailer_tag:
                trailer_url = trailer_tag['href']
        except Exception as e:
            print("[FullDetailsWorker] Error:", e)

        self.signals.finished.emit(self.url, second_title, full_description, trailer_url)

class SiteSearchWorkerSignals(QObject):
    result_ready = pyqtSignal(str, list)

class SiteSearchWorker(QRunnable):
    def __init__(self, site_name, search_func, query):
        super().__init__()
        self.site_name = site_name
        self.search_func = search_func
        self.query = query
        self.signals = SiteSearchWorkerSignals()

    def run(self):
        print(self.query)
        results = self.search_func(self.query)
        self.signals.result_ready.emit(self.site_name, results)

def search_jikan_mal(query, cat):
    if cat not in ["anime", "manga"]:
        print("Categoría no válida. Usa 'anime' o 'manga'.")
        return []
    url = f"https://api.jikan.moe/v4/{cat}?q={query}&limit=25"

    try:
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        print("[MyAnimeList/Jikan] Error:", e)
        return []

    results = []
    
    for item in data.get("data", []):
        print(item.get("title", ""))
        try:
            results.append({
                "loaded": True,
                "title": item.get("title", ""),
                "other_titles": [t["title"] for t in item.get("titles", []) if t["title"] != item.get("title")],
                "url": item.get("url", ""),
                "trailer": (item.get("trailer", {}).get("embed_url","") or "").split("?")[0],
                "image": item.get("images", {}).get("jpg", {}).get("image_url"),
                "description": item.get("synopsis", ""),
                "genres": [g["name"] for g in item.get("genres", [])],
                "type": item.get("type", ""),
                "episodes": item.get("episodes", ""),
                "score": item.get("score", ""),
                "rating": item.get("rating", ""),
                "source": "MyAnimeList"
            })
        except Exception as e:
            print("Error procesando un resultado:", e)
            continue

    return results

class AnimeSearchWorkerSignals(QObject):
    finished = pyqtSignal(list)

class AnimeSearchWorker(QRunnable):
    def __init__(self, term, cat):
        super().__init__()
        self.term = term
        self.cat = cat
        self.signals = AnimeSearchWorkerSignals()

    def run(self):
        if self.cat=='anime' or self.cat=='manga':
            results = search_jikan_mal(self.term, self.cat)
        self.signals.finished.emit(results)

class GameSearchWorkerSignals(QObject):
    finished = pyqtSignal(list)

class GameSearchWorker(QRunnable):
    def __init__(self, query, api_key):
        super().__init__()
        self.query = query
        self.api_key = api_key
        self.signals = GameSearchWorkerSignals()

    def run(self):
        url = "https://api.rawg.io/api/games"
        params = {
            "search": self.query,
            "key": self.api_key,
            "page_size": 20,
            "ordering": "-rating"
        }

        try:
            r = requests.get(url, params=params, timeout=10)
            r.raise_for_status()
            data = r.json().get("results", [])
        except Exception as e:
            print("[GameSearchWorker] Error:", e)
            self.signals.finished.emit([])
            return
        
        results = []
        for g in data:
            try:
                results.append({
                    "source": "RAWG",
                    "title": g.get("name", "Sin título"),
                    "url": f"https://rawg.io/games/{g.get('slug', '')}",
                    "image": g.get("background_image"),
                    "released": g.get("released", "Desconocido"),
                    "rating": g.get("rating", "N/A"),
                    "genres": [genre["name"] for genre in g.get("genres", [])],
                    "platforms": [p["platform"]["name"] for p in g.get("platforms", [])],
                    "description": g.get("short_description", "") or "Sin descripción.",
                    "trailer": (g.get("clip", {}) or {}).get("clip"),
                    "loaded": True
                })
            except Exception as e:
                print("[GameSearchWorker] Error procesando resultado:", e)
        print(results)
        self.signals.finished.emit(results)

class FactorioSearchSignals(QObject):
    finished = pyqtSignal(object, str)  # payload, error

class FactorioSearchWorker(QRunnable):
    def __init__(self, mode=None, query=None, page=1):
        super().__init__()
        self.mode = mode
        self.query = query
        self.params = {
            "page": page,
            "factorio_version": 2.0,
            "show_deprecated": False
        }
        self.signals = FactorioSearchSignals()

    def run(self):
        try:
            if self.query:
                url = f"{FACTORIO_BASE}/search"
                self.params["query"] = self.query
                params = self.params
            else:
                url = f"{FACTORIO_BASE}/browse/{self.mode}"
                params = self.params
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            parsed = parse_mod_list(resp.text)
            self.signals.finished.emit(parsed, "")
        except Exception as e:
            self.signals.finished.emit([], str(e))

class FactorioInfoSignals(QObject):
    finished = pyqtSignal(str, dict, str)  # mod_id, data, error

class FactorioInfoWorker(QRunnable):
    def __init__(self, mod_id):
        super().__init__()
        self.mod_id = mod_id
        self.signals = FactorioInfoSignals()

    def run(self):
        try:
            rand = random.random()
            params = {"rand": f"{rand:.18f}", "id": self.mod_id}
            resp = requests.get(MODINFO_URL, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            self.signals.finished.emit(self.mod_id, data, "")
        except Exception as e:
            self.signals.finished.emit(self.mod_id, {}, str(e))

def parse_page_bar(soup):
    total = None
    current_page = None
    last_page = None

    label = soup.select_one("div.grey")
    if label:
        match = re.search(r"Found\s+(\d+)\s+mods", label.get_text(strip=True))
        if match:
            total = int(match.group(1))

    for a in soup.select("a.button.square-sm"):
        href = a.get("href") or ""
        if "page=" not in href:
            continue
        num_match = re.search(r"[?&]page=(\d+)", href)
        if not num_match:
            continue
        page_num = int(num_match.group(1))
        last_page = max(last_page or page_num, page_num)
        if "active" in (a.get("class") or []):
            current_page = page_num

    return {
        "total": total,
        "current_page": current_page,
        "last_page": last_page,
    }

def parse_mod_list(html):
    soup = BeautifulSoup(html, "html.parser")
    items = []
    seen = set()
    mod_list = soup.select_one("div.mod-list")
    if not mod_list:
        mod_list = soup

    containers = mod_list.select("div.panel-inset-lighter.flex-column.p0")
    if not containers:
        containers = mod_list.select("div.panel-inset-lighter")

    for container in containers:
        name_tag = container.select_one("h2 a.result-field[href^='/mod/']")
        if not name_tag:
            continue
        href = (name_tag.get("href") or "").split("#")[0]
        if not href.startswith("/mod/"):
            continue
        clean_href = href.split("?")[0]
        mod_url = f"{FACTORIO_BASE}{clean_href}"
        mod_id = clean_href.split("/mod/")[-1].strip("/")
        if mod_url in seen:
            continue
        seen.add(mod_url)
        name = name_tag.get_text(strip=True) or "Sin título"
        author = ""
        author_url = ""
        description = ""
        category = ""
        updated_text = ""
        updated_title = ""
        versions = ""
        downloads_text = ""
        downloads_exact = ""
        thumbnail = ""

        if container:
            author_tag = container.select_one("a[href^='/user/']")
            if author_tag:
                author = author_tag.get_text(strip=True)
                author_url = f"{FACTORIO_BASE}{author_tag.get('href','')}"

            desc_tag = container.select_one("p.result-field")
            if desc_tag:
                description = desc_tag.get_text(" ", strip=True)

            category_tag = container.select_one(".category-label")
            if category_tag:
                category = category_tag.get_text(" ", strip=True)

            updated_tag = container.select_one("div[title='Last updated'] span")
            if updated_tag:
                updated_text = updated_tag.get_text(strip=True)
                updated_title = updated_tag.get("title", "")

            versions_tag = container.select_one("div[title='Available for these Factorio versions']")
            if versions_tag:
                versions = versions_tag.get_text(" ", strip=True).replace(" ", " ").strip()

            downloads_tag = container.select_one("div[title='Downloads, updated daily'] span")
            if downloads_tag:
                downloads_text = downloads_tag.get_text(strip=True)
                downloads_exact = downloads_tag.get("title", "")

            img_tag = container.select_one("img")
            if img_tag:
                thumbnail = img_tag.get("src", "")

        items.append({
            "name": name,
            "url": mod_url,
            "id": mod_id,
            "author": author,
            "author_url": author_url,
            "description": description,
            "category": category,
            "updated_text": updated_text,
            "updated_title": updated_title,
            "versions": versions,
            "downloads_text": downloads_text,
            "downloads_exact": downloads_exact,
            "thumbnail": thumbnail,
        })

    page_info = parse_page_bar(soup)
    return {
        "items": items,
        "page": page_info.get("current_page") or 1,
        "last_page": page_info.get("last_page"),
        "total": page_info.get("total"),
    }

class GDriveSignals(QObject):
    finished = pyqtSignal(int, str, bool, str)


class GDriveDownloader(QRunnable):
    def __init__(self, url, output_path, index, display_name, is_folder):
        super().__init__()
        self.url = url
        self.output_path = output_path
        self.index = index
        self.display_name = display_name
        self.is_folder = is_folder
        self.signals = GDriveSignals()

    def run(self):
        try:
            from gdrive_handler import gdown_download
        except Exception as e:
            self.signals.finished.emit(self.index, self.display_name, False, f"gdown no disponible: {e}")
            return

        try:
            gdown_download(self.url, self.output_path, is_folder=self.is_folder)
            self.signals.finished.emit(self.index, self.display_name, True, "")
        except Exception as e:
            self.signals.finished.emit(self.index, self.display_name, False, str(e))


