import os, requests, time
from PyQt5.QtCore import QObject, pyqtSignal, QRunnable, QThreadPool, pyqtSlot
from PyQt5.QtGui import QPixmap, QImage
from bs4 import BeautifulSoup
from settings_dialog import load_config, DEFAULT_CONFIG

MAX_RETRIES = 100
RETRY_DELAY = 3
CHUNK_SIZE = 8192

class DownloadSignals(QObject):
    progress = pyqtSignal(int, int)  # index, percentage
    finished = pyqtSignal(int)       # index

class FileDownloader(QRunnable):
    def __init__(self, url, filename, index, signals):
        super().__init__()
        self.url = url
        self.filename = filename
        self.index = index
        self.signals = signals

        QThreadPool.globalInstance().setMaxThreadCount(
            load_config().get("max_parallel_downloads", DEFAULT_CONFIG["max_parallel_downloads"])
        )

    def run(self):
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                downloaded = 0
                mode = 'wb'
                headers = {}

                if os.path.exists(self.filename):
                    downloaded = os.path.getsize(self.filename)
                    headers['Range'] = f'bytes={downloaded}-'
                    mode = 'ab'

                with requests.get(self.url, stream=True, headers=headers, timeout=15) as r:
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
        except:
            self.signals.finished.emit(None)

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
        try:
            results.append({
                "loaded": True,
                "title": item.get("title", ""),
                "other_titles": [t["title"] for t in item.get("titles", []) if t["title"] != item.get("title")],
                "url": item.get("url", ""),
                "trailer": item.get("trailer", {}).get("embed_url","").split("?")[0],
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

def search_scrapper_mal(query,cat):
    url = f"https://myanimelist.net/{cat}.php?cat={cat}&q={query}&type=0&score=0&status=0&sm=0&sd=0&sy=0&em=0&ed=0&ey=0&c[]=a&c[]=b&c[]=c&c[]=g"
    try:
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
    except Exception as e:
        if "Failed to resolve 'myanimelist.net' ([Errno 11001] getaddrinfo failed)" in e:
            print("[MyAnimeList] Failed connection, check your internet")
        else:
            print("[MyAnimeList] Error:", e)
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    results = []

    for row in soup.select("tr:has(img)"):
        try:
            img_tag = row.select_one("img")
            title_tag = row.select_one("td:nth-of-type(2) a strong")
            description_tag = row.select_one(".pt4")
            info_cells = row.find_all("td", class_="ac")
            #print(info_cells)
            title = title_tag.text.strip()
            link = title_tag.find_parent("a")["href"]

            # Extraer y mejorar la imagen
            raw_img = img_tag.get("data-src", img_tag.get("src", ""))
            full_img = raw_img.replace("/r/50x70", "").split("?")[0]  # remueve resize y parámetros

            description = description_tag.get_text(strip=True) if description_tag else ""
            tipo = info_cells[0].text.strip() if len(info_cells) > 0 else ""
            episodios = info_cells[1].text.strip() if len(info_cells) > 1 else ""
            score = info_cells[2].text.strip() if len(info_cells) > 2 else ""
            rating = info_cells[3].text.strip() if len(info_cells) > 3 else ""

            results.append({
                "loaded": False,
                "title": title,
                "other_titles": [], 
                "url": link,
                "trailer": None,
                "image": full_img,
                "description": description,
                "genres": None,
                "type": tipo,
                "episodes": episodios,
                "score": score,
                "rating": rating,
                "source": "MyAnimeList"
            })

        except Exception as e:
            print("Error procesando un resultado:", e)
            continue

    return results

class SearchWorkerSignals(QObject):
    finished = pyqtSignal(list)

class SearchWorker(QRunnable):
    def __init__(self, term, cat):
        super().__init__()
        self.term = term
        self.cat = cat
        self.signals = SearchWorkerSignals()

    def run(self):
        if self.cat=='anime' or self.cat=='manga':
            results = search_jikan_mal(self.term, self.cat)
        self.signals.finished.emit(results)

