import random, re, requests
from bs4 import BeautifulSoup
from PyQt5.QtCore import QObject, QRunnable, pyqtSignal


FACTORIO_BASE = "https://mods.factorio.com"
MODINFO_URL = "https://re146.dev/factorio/mods/modinfo"


class FactorioSearchSignals(QObject):
    finished = pyqtSignal(object, str)


class FactorioSearchWorker(QRunnable):
    def __init__(self, mode=None, query=None, page=1):
        super().__init__()
        self.mode = mode
        self.query = query
        self.params = {
            "page": page,
            "factorio_version": 2.0,
            "show_deprecated": False,
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
            response = requests.get(url, params=params, timeout=15)
            response.raise_for_status()
            parsed = parse_mod_list(response.text)
            self.signals.finished.emit(parsed, "")
        except Exception as exc:
            self.signals.finished.emit([], str(exc))


class FactorioInfoSignals(QObject):
    finished = pyqtSignal(str, dict, str)


class FactorioInfoWorker(QRunnable):
    def __init__(self, mod_id):
        super().__init__()
        self.mod_id = mod_id
        self.signals = FactorioInfoSignals()

    def run(self):
        try:
            params = {"rand": f"{random.random():.18f}", "id": self.mod_id}
            response = requests.get(MODINFO_URL, params=params, timeout=15)
            response.raise_for_status()
            self.signals.finished.emit(self.mod_id, response.json(), "")
        except Exception as exc:
            self.signals.finished.emit(self.mod_id, {}, str(exc))


class DependencyResolveSignals(QObject):
    progress = pyqtSignal(str)
    finished = pyqtSignal(list)


class DependencyResolveWorker(QRunnable):
    def __init__(self, window, dependencies, visited):
        super().__init__()
        self.window = window
        self.dependencies = dependencies
        self.visited = visited
        self.signals = DependencyResolveSignals()

    def run(self):
        items = self.window.resolve_dependencies(
            self.dependencies,
            visited=self.visited,
            progress_cb=self.signals.progress.emit,
        )
        self.signals.finished.emit(items)


def parse_page_bar(soup):
    total = None
    current_page = None
    last_page = None

    label = soup.select_one("div.grey")
    if label:
        match = re.search(r"Found\s+(\d+)\s+mods", label.get_text(strip=True))
        if match:
            total = int(match.group(1))

    for anchor in soup.select("a.button.square-sm"):
        href = anchor.get("href") or ""
        if "page=" not in href:
            continue
        num_match = re.search(r"[?&]page=(\d+)", href)
        if not num_match:
            continue
        page_num = int(num_match.group(1))
        last_page = max(last_page or page_num, page_num)
        if "active" in (anchor.get("class") or []):
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

        author_tag = container.select_one("a[href^='/user/']")
        if author_tag:
            author = author_tag.get_text(strip=True)
            author_url = f"{FACTORIO_BASE}{author_tag.get('href', '')}"

        desc_tag = container.select_one("p.result-field")
        if desc_tag:
            description = desc_tag.get_text(" ", strip=True)

        category_tag = container.select_one(".category-label")
        if category_tag:
            category = category_tag.get_text(" ", strip=True)

        updated_tag = container.select_one("div[title='Last updated'] span")
        if updated_tag:
            updated_text = updated_tag.get_text(" ", strip=True)
            updated_title = updated_tag.parent.get("title", "")

        versions_tag = container.select_one("div[title='Factorio version'] span")
        if versions_tag:
            versions = versions_tag.get_text(" ", strip=True)

        downloads_tag = container.select_one("div[title='Downloads']")
        if downloads_tag:
            downloads_exact = downloads_tag.get("title", "")
            downloads_text = downloads_tag.get_text(" ", strip=True)

        image_tag = container.select_one("img")
        if image_tag:
            thumbnail = image_tag.get("src", "")

        items.append({
            "id": mod_id,
            "url": mod_url,
            "name": name_tag.get_text(strip=True) or "Sin título",
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

    page_data = parse_page_bar(soup)
    return {
        "items": items,
        "total": page_data["total"],
        "page": page_data["current_page"] or 1,
        "last_page": page_data["last_page"],
    }
