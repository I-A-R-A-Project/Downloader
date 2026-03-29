import hashlib
import html
import logging
import math
import os
import re
import shutil
import subprocess
import requests
from bs4 import BeautifulSoup
from PyQt5.QtCore import QObject, QRunnable, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QImage, QPixmap
from config import CONFIG_PATH


IMAGE_CACHE_DIR = os.path.join(os.path.dirname(CONFIG_PATH), "image_cache")
VNDB_API_URL = "https://api.vndb.org/kana/vn"
VNDB_RESULTS_PER_PAGE = 20
logger = logging.getLogger("media_search")


class URLWorkerSignals(QObject):
    finished = pyqtSignal(int, str, str)


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
        except Exception as exc:
            print(f"Error ejecutando función diferida para {self.title}: {exc}")
            link = None
        self.signals.finished.emit(self.index, self.title, link)


class ImageLoadedSignal(QObject):
    finished = pyqtSignal(str, QImage)


class ImageLoaderWorker(QRunnable):
    def __init__(self, image_url):
        super().__init__()
        self.image_url = image_url
        self.signals = ImageLoadedSignal()

    def run(self):
        try:
            if not self.image_url:
                logger.debug("ImageLoaderWorker: empty image URL")
                self.signals.finished.emit(self.image_url, QImage())
                return

            os.makedirs(IMAGE_CACHE_DIR, exist_ok=True)
            cache_path = os.path.join(
                IMAGE_CACHE_DIR,
                hashlib.sha1(self.image_url.encode("utf-8")).hexdigest() + ".jpg",
            )

            img_data = ""
            if os.path.exists(cache_path):
                logger.debug("ImageLoaderWorker: cache hit for %s", self.image_url)
                with open(cache_path, "rb") as f:
                    img_data = f.read()
            else:
                logger.debug("ImageLoaderWorker: downloading %s", self.image_url)
                response = requests.get(self.image_url, timeout=10)
                response.raise_for_status()
                img_data = response.content
                with open(cache_path, "wb") as f:
                    f.write(img_data)

            image = QImage()
            if not image.loadFromData(img_data):
                if os.path.exists(cache_path):
                    try:
                        os.remove(cache_path)
                    except OSError:
                        pass
                self.signals.finished.emit(self.image_url, QImage())
                return
            logger.debug("ImageLoaderWorker: loaded image for %s", self.image_url)
            self.signals.finished.emit(self.image_url, image)
        except Exception:
            logger.exception("ImageLoaderWorker: failed for %s", self.image_url)
            self.signals.finished.emit(self.image_url, QImage())


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
                trailer_url = trailer_tag["href"]
        except Exception as exc:
            print("[FullDetailsWorker] Error:", exc)

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
        logger.debug("SiteSearchWorker: %s query=%r", self.site_name, self.query)
        results = self.search_func(self.query)
        logger.debug("SiteSearchWorker: %s results=%s", self.site_name, len(results) if isinstance(results, list) else "n/a")
        self.signals.result_ready.emit(self.site_name, results)


def _format_vndb_rating(value):
    if value in (None, ""):
        return ""
    try:
        return f"{float(value) / 10:.1f}"
    except (TypeError, ValueError):
        return str(value)


def _format_vndb_length(minutes, fallback_length):
    if isinstance(minutes, int) and minutes > 0:
        hours = minutes // 60
        rem_minutes = minutes % 60
        if hours and rem_minutes:
            return f"{hours}h {rem_minutes}m"
        if hours:
            return f"{hours}h"
        return f"{rem_minutes}m"

    fallback_map = {
        1: "Muy corta",
        2: "Corta",
        3: "Media",
        4: "Larga",
        5: "Muy larga",
    }
    return fallback_map.get(fallback_length, "")


def _strip_vndb_markup(text):
    if not text:
        return "Sin descripción."

    cleaned = html.unescape(text)
    cleaned = re.sub(r"\[url=[^\]]+\](.*?)\[/url\]", r"\1", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"\[raw\](.*?)\[/raw\]", r"\1", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"\[quote\](.*?)\[/quote\]", r"\1", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"\[(/?)(b|i|u|s|code|spoiler|quote|raw|url|list|\*)[^\]]*\]", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = BeautifulSoup(cleaned, "html.parser").get_text("\n", strip=True)
    return cleaned.strip() or "Sin descripción."


def search_vndb_visual_novels(query, page=1):
    payload = {
        "filters": ["search", "=", query],
        "fields": (
            "title, alttitle, image.url, description, released, rating, "
            "length_minutes, length, platforms, languages, developers{name}"
        ),
        "sort": "searchrank",
        "results": VNDB_RESULTS_PER_PAGE,
        "page": page,
        "count": True,
    }

    try:
        logger.info("VNDB search start: query=%r page=%s", query, page)
        response = requests.post(
            VNDB_API_URL,
            json=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "MediaSearchPrototype/1.0",
            },
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        logger.exception("VNDB search failed: query=%r page=%s", query, page)
        print("[VNDB] Error:", exc)
        return {"items": [], "page": page, "last_page": page, "total": 0}

    total = data.get("count", 0) or 0
    last_page = max(1, math.ceil(total / VNDB_RESULTS_PER_PAGE)) if total else page + (1 if data.get("more") else 0)
    results = []

    for item in data.get("results", []):
        developers = [
            developer.get("name", "").strip()
            for developer in item.get("developers", [])
            if developer.get("name")
        ]
        alttitle = (item.get("alttitle") or "").strip()
        results.append({
            "id": item.get("id"),
            "source": "VNDB",
            "title": item.get("title") or "Sin título",
            "other_titles": [alttitle] if alttitle and alttitle != item.get("title") else [],
            "url": f"https://vndb.org/{item.get('id', '')}",
            "image": ((item.get("image") or {}).get("url")),
            "description": _strip_vndb_markup(item.get("description")),
            "released": item.get("released") or "Desconocido",
            "rating": _format_vndb_rating(item.get("rating")),
            "languages": item.get("languages", []),
            "platforms": item.get("platforms", []),
            "developers": developers,
            "length_label": _format_vndb_length(item.get("length_minutes"), item.get("length")),
            "trailer": None,
            "loaded": True,
        })

    logger.info("VNDB search end: query=%r page=%s items=%s total=%s", query, page, len(results), total)
    return {
        "items": results,
        "page": page,
        "last_page": last_page,
        "total": total,
    }


def search_jikan_mal(query, cat, page=1):
    if cat not in ["anime", "manga"]:
        print("Categoría no válida. Usa 'anime' o 'manga'.")
        return {"items": [], "page": page, "last_page": 1, "total": 0}
    url = f"https://api.jikan.moe/v4/{cat}?q={query}&limit=25&page={page}"

    try:
        logger.info("Jikan search start: category=%s query=%r page=%s", cat, query, page)
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        logger.exception("Jikan search failed: category=%s query=%r page=%s", cat, query, page)
        print("[MyAnimeList/Jikan] Error:", exc)
        return {"items": [], "page": page, "last_page": 1, "total": 0}

    results = []
    for item in data.get("data", []):
        try:
            results.append({
                "loaded": True,
                "title": item.get("title", ""),
                "other_titles": [t["title"] for t in item.get("titles", []) if t["title"] != item.get("title")],
                "url": item.get("url", ""),
                "trailer": (item.get("trailer", {}).get("embed_url", "") or "").split("?")[0],
                "image": item.get("images", {}).get("jpg", {}).get("image_url"),
                "description": item.get("synopsis", ""),
                "genres": [g["name"] for g in item.get("genres", [])],
                "type": item.get("type", ""),
                "episodes": item.get("episodes", ""),
                "score": item.get("score", ""),
                "rating": item.get("rating", ""),
                "source": "MyAnimeList",
            })
        except Exception as exc:
            print("Error procesando un resultado:", exc)
    pagination = data.get("pagination") or {}
    logger.info("Jikan search end: category=%s query=%r page=%s items=%s total=%s", cat, query, page, len(results), ((pagination.get("items") or {}).get("total")))
    return {
        "items": results,
        "page": pagination.get("current_page", page),
        "last_page": pagination.get("last_visible_page", page),
        "total": ((pagination.get("items") or {}).get("total")),
    }


class AnimeSearchWorkerSignals(QObject):
    finished = pyqtSignal(dict)


class AnimeSearchWorker(QRunnable):
    def __init__(self, term, cat, page=1):
        super().__init__()
        self.term = term
        self.cat = cat
        self.page = page
        self.signals = AnimeSearchWorkerSignals()

    def run(self):
        results = search_jikan_mal(self.term, self.cat, self.page) if self.cat in ("anime", "manga") else {
            "items": [],
            "page": self.page,
            "last_page": self.page,
            "total": 0,
        }
        self.signals.finished.emit(results)


class VisualNovelSearchWorkerSignals(QObject):
    finished = pyqtSignal(dict)


class VisualNovelSearchWorker(QRunnable):
    def __init__(self, query, page=1):
        super().__init__()
        self.query = query
        self.page = page
        self.signals = VisualNovelSearchWorkerSignals()

    def run(self):
        self.signals.finished.emit(search_vndb_visual_novels(self.query, self.page))


class GameSearchWorkerSignals(QObject):
    finished = pyqtSignal(dict)


class GameSearchWorker(QRunnable):
    def __init__(self, query, api_key, page=1):
        super().__init__()
        self.query = query
        self.api_key = api_key
        self.page = page
        self.signals = GameSearchWorkerSignals()

    def run(self):
        page_size = 20
        url = "https://api.rawg.io/api/games"
        params = {
            "search": self.query,
            "key": self.api_key,
            "page_size": page_size,
            "page": self.page,
            "ordering": "-rating",
        }

        try:
            logger.info("RAWG search start: query=%r page=%s", self.query, self.page)
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            payload = response.json()
            data = payload.get("results", [])
        except Exception as exc:
            logger.exception("RAWG search failed: query=%r page=%s", self.query, self.page)
            print("[GameSearchWorker] Error:", exc)
            self.signals.finished.emit({
                "items": [],
                "page": self.page,
                "last_page": self.page,
                "total": 0,
            })
            return

        results = []
        for game in data:
            try:
                results.append({
                    "id": game.get("id"),
                    "source": "RAWG",
                    "title": game.get("name", "Sin título"),
                    "url": f"https://rawg.io/games/{game.get('slug', '')}",
                    "image": game.get("background_image"),
                    "released": game.get("released", "Desconocido"),
                    "rating": game.get("rating", "N/A"),
                    "genres": [genre["name"] for genre in game.get("genres", [])],
                    "platforms": [p["platform"]["name"] for p in game.get("platforms", [])],
                    "description": "Cargando descripción...",
                    "movies_count": game.get("movies_count", 0),
                    "trailer": None,
                    "loaded": True,
                })
            except Exception as exc:
                print("[GameSearchWorker] Error procesando resultado:", exc)
        total = payload.get("count", 0)
        last_page = max(1, math.ceil(total / page_size)) if total else self.page
        logger.info("RAWG search end: query=%r page=%s items=%s total=%s", self.query, self.page, len(results), total)
        self.signals.finished.emit({
            "items": results,
            "page": self.page,
            "last_page": last_page,
            "total": total,
        })


class GameDetailsWorkerSignals(QObject):
    finished = pyqtSignal(int, str, str)


class GameDetailsWorker(QRunnable):
    def __init__(self, game_id, api_key):
        super().__init__()
        self.game_id = game_id
        self.api_key = api_key
        self.signals = GameDetailsWorkerSignals()

    def run(self):
        description = "Sin descripción."
        trailer_url = None

        try:
            logger.debug("RAWG details start: game_id=%s", self.game_id)
            details_url = f"https://api.rawg.io/api/games/{self.game_id}"
            details_response = requests.get(details_url, params={"key": self.api_key}, timeout=10)
            details_response.raise_for_status()
            details = details_response.json()

            raw_description = details.get("description") or ""
            if raw_description:
                description = BeautifulSoup(raw_description, "html.parser").get_text("\n", strip=True)
            if details.get("movies_count", 0) > 0:
                movies_url = f"https://api.rawg.io/api/games/{self.game_id}/movies"
                movies_response = requests.get(movies_url, params={"key": self.api_key}, timeout=10)
                movies_response.raise_for_status()
                movies = movies_response.json().get("results", [])
                for movie in movies:
                    movie_data = movie.get("data") or {}
                    trailer_url = movie_data.get("480")
                    if trailer_url:
                        break
        except Exception as exc:
            logger.exception("RAWG details failed: game_id=%s", self.game_id)
            print("[GameDetailsWorker] Error:", exc)

        logger.debug("RAWG details end: game_id=%s trailer=%s", self.game_id, bool(trailer_url))
        self.signals.finished.emit(self.game_id, description, trailer_url or "")


class TrailerLaunchWorkerSignals(QObject):
    browser_fallback = pyqtSignal(str, str)
    finished = pyqtSignal(str)


class TrailerLaunchWorker(QRunnable):
    def __init__(self, request_id, trailer_url, window_title):
        super().__init__()
        self.request_id = request_id
        self.trailer_url = trailer_url
        self.window_title = window_title
        self.signals = TrailerLaunchWorkerSignals()

    def run(self):
        try:
            if any(host in self.trailer_url for host in ("youtube.com", "youtu.be", "youtube-nocookie.com")):
                yt_dlp_path = shutil.which("yt-dlp")
                ffplay_path = shutil.which("ffplay")
                if not yt_dlp_path or not ffplay_path:
                    self.signals.browser_fallback.emit(self.request_id, self.trailer_url)
                    return

                result = subprocess.run(
                    [yt_dlp_path, "-g", self.trailer_url],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=20,
                )
                stream_url = next((line.strip() for line in result.stdout.splitlines() if line.strip()), "")
                if not stream_url:
                    self.signals.browser_fallback.emit(self.request_id, self.trailer_url)
                    return

                command = [ffplay_path, "-autoexit"]
                if self.window_title:
                    command.extend(["-window_title", self.window_title])
                command.append(stream_url)
                process = subprocess.Popen(command)
                process.wait()
                return

            if self.trailer_url.lower().endswith(".mp4"):
                ffplay_path = shutil.which("ffplay")
                if not ffplay_path:
                    self.signals.browser_fallback.emit(self.request_id, self.trailer_url)
                    return

                command = [ffplay_path, "-autoexit"]
                if self.window_title:
                    command.extend(["-window_title", self.window_title])
                command.append(self.trailer_url)
                process = subprocess.Popen(command)
                process.wait()
                return

            self.signals.browser_fallback.emit(self.request_id, self.trailer_url)
        except Exception:
            self.signals.browser_fallback.emit(self.request_id, self.trailer_url)
        finally:
            self.signals.finished.emit(self.request_id)
