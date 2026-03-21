import argparse
import os
import random
import re
import sys
import requests
from bs4 import BeautifulSoup
from PyQt5.QtCore import Qt, QThreadPool, QRunnable, pyqtSignal, QObject, QSize
from PyQt5.QtGui import QPixmap, QImage, QMovie
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QPushButton, QComboBox,
    QTextEdit, QDialog, QMessageBox, QStackedLayout
)
from settings_dialog import load_config, DEFAULT_CONFIG


FACTORIO_BASE = "https://mods.factorio.com"
RE146_BASE = "https://re146.dev/factorio/mods/en#"
SPINNER_PATH = "spinner.gif"
MODINFO_URL = "https://re146.dev/factorio/mods/modinfo"
DOWNLOAD_BASE = "https://mods-storage.re146.dev"


class ModSearchSignals(QObject):
    finished = pyqtSignal(object, str)  # payload, error


class ModSearchWorker(QRunnable):
    def __init__(self, mode=None, query=None, page=1):
        super().__init__()
        self.mode = mode
        self.query = query
        self.page = page
        self.signals = ModSearchSignals()

    def run(self):
        try:
            if self.query:
                url = f"{FACTORIO_BASE}/search"
                params = {"query": self.query, "page": self.page}
            else:
                url = f"{FACTORIO_BASE}/browse/{self.mode}"
                params = {"page": self.page}

            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            parsed = parse_mod_list(resp.text)
            self.signals.finished.emit(parsed, "")
        except Exception as e:
            self.signals.finished.emit([], str(e))


class ImageLoaderSignals(QObject):
    finished = pyqtSignal(str, QPixmap)


class ImageLoaderWorker(QRunnable):
    def __init__(self, url):
        super().__init__()
        self.url = url
        self.signals = ImageLoaderSignals()

    def run(self):
        try:
            img_data = requests.get(self.url, timeout=10).content
            image = QImage()
            image.loadFromData(img_data)
            pixmap = QPixmap.fromImage(image)
            self.signals.finished.emit(self.url, pixmap)
        except Exception:
            self.signals.finished.emit(self.url, QPixmap())


class ModInfoSignals(QObject):
    finished = pyqtSignal(str, dict, str)  # mod_id, data, error


class ModInfoWorker(QRunnable):
    def __init__(self, mod_id):
        super().__init__()
        self.mod_id = mod_id
        self.signals = ModInfoSignals()

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


class DownloadSignals(QObject):
    finished = pyqtSignal(bool, str)  # ok, message


class DownloadWorker(QRunnable):
    def __init__(self, url, output_path):
        super().__init__()
        self.url = url
        self.output_path = output_path
        self.signals = DownloadSignals()

    def run(self):
        try:
            resp = requests.get(self.url, stream=True, timeout=20)
            resp.raise_for_status()
            os.makedirs(os.path.dirname(self.output_path), exist_ok=True)
            with open(self.output_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            self.signals.finished.emit(True, self.output_path)
        except Exception as e:
            self.signals.finished.emit(False, str(e))


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


class ModSearchWindow(QWidget):
    def __init__(self, game="factorio"):
        super().__init__()
        self.game = game
        self.setWindowTitle("Buscar mods")
        self.resize(900, 600)
        self.thread_pool = QThreadPool()
        self.results = []
        self.current_mode = "updated"
        self.current_query = ""
        self.current_page = 1
        self.last_page = None
        self.total_found = None
        self.is_loading = False
        self.pending_load = False
        self.image_cache = {}
        self.current_thumb_url = ""
        self.auto_prefetch_done = False
        self.preload_queue = []
        self.preload_inflight = False
        self.modinfo_cache = {}
        self.current_mod_id = ""

        layout = QVBoxLayout()

        top_row = QHBoxLayout()
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Updated", "updated")
        self.mode_combo.addItem("Downloaded", "downloaded")
        self.mode_combo.addItem("Trending", "trending")
        top_row.addWidget(QLabel("Listado:"))
        top_row.addWidget(self.mode_combo)
        top_row.addStretch(1)
        layout.addLayout(top_row)

        search_row = QHBoxLayout()
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Buscar mods...")
        self.search_bar.returnPressed.connect(self.search_mods)
        self.search_button = QPushButton("Buscar")
        self.search_button.clicked.connect(self.search_mods)
        search_row.addWidget(self.search_bar)
        search_row.addWidget(self.search_button)
        layout.addLayout(search_row)

        content_row = QHBoxLayout()
        self.results_list = QListWidget()
        self.results_list.itemClicked.connect(self.show_details)
        self.results_list.currentItemChanged.connect(self.on_current_item_changed)
        self.results_list.verticalScrollBar().valueChanged.connect(self.on_scroll)
        content_row.addWidget(self.results_list, 2)

        details_panel = QVBoxLayout()
        details_row = QHBoxLayout()
        self.spinner_movie = QMovie(SPINNER_PATH)
        self.spinner_movie.setScaledSize(QSize(32, 32))
        self.thumb_frame = QWidget()
        self.thumb_frame.setFixedSize(160, 160)
        self.thumb_frame.setStyleSheet("border: 1px solid #ccc;")
        self.thumb_stack = QStackedLayout(self.thumb_frame)
        self.thumb_label = QLabel()
        self.thumb_label.setAlignment(Qt.AlignCenter)
        self.spinner_label = QLabel()
        self.spinner_label.setMovie(self.spinner_movie)
        self.spinner_label.setAlignment(Qt.AlignCenter)
        self.thumb_stack.addWidget(self.thumb_label)
        self.thumb_stack.addWidget(self.spinner_label)
        self.thumb_stack.setCurrentWidget(self.thumb_label)

        thumb_col = QVBoxLayout()
        thumb_col.addWidget(self.thumb_frame, alignment=Qt.AlignTop)

        fields_col = QVBoxLayout()
        self.name_label = QLabel("<b>Nombre:</b>")
        self.author_label = QLabel("<b>Autor:</b>")
        self.category_label = QLabel("<b>Categoría:</b>")
        self.updated_label = QLabel("<b>Actualizado:</b>")
        self.versions_label = QLabel("<b>Versiones:</b>")
        self.downloads_label = QLabel("<b>Descargas:</b>")
        for lbl in [
            self.name_label, self.author_label, self.category_label,
            self.updated_label, self.versions_label, self.downloads_label
        ]:
            lbl.setTextFormat(Qt.RichText)
            fields_col.addWidget(lbl)

        details_row.addLayout(thumb_col)
        details_row.addLayout(fields_col, 1)
        details_panel.addLayout(details_row)

        self.details = QTextEdit()
        self.details.setReadOnly(True)
        details_panel.addWidget(self.details, 1)

        details_container = QWidget()
        details_container.setLayout(details_panel)
        content_row.addWidget(details_container, 3)
        layout.addLayout(content_row)

        bottom_row = QHBoxLayout()
        self.status_label = QLabel("")
        self.open_button = QPushButton("Descargar (re146)")
        self.open_button.clicked.connect(self.open_re146)
        self.open_button.setEnabled(False)
        bottom_row.addWidget(self.status_label)
        bottom_row.addStretch(1)
        bottom_row.addWidget(self.open_button)
        layout.addLayout(bottom_row)

        self.setLayout(layout)
        self.mode_combo.currentIndexChanged.connect(self.load_browse)
        self.load_browse()

    def set_loading(self, loading, message=""):
        self.is_loading = loading
        self.search_button.setEnabled(not loading)
        self.search_bar.setEnabled(not loading)
        self.status_label.setText(message)

    def load_browse(self):
        mode = self.mode_combo.currentData()
        self.current_mode = mode
        self.current_query = ""
        self.current_page = 1
        self.last_page = None
        self.total_found = None
        self.results = []
        self.pending_load = False
        self.auto_prefetch_done = False
        self.preload_queue = []
        self.preload_inflight = False
        self.results_list.clear()
        self.clear_details()
        self.set_loading(True, f"Cargando listado: {mode}...")
        worker = ModSearchWorker(mode=mode, page=1)
        worker.signals.finished.connect(self.on_results_reset)
        self.thread_pool.start(worker)

    def search_mods(self):
        query = self.search_bar.text().strip()
        if not query:
            return
        self.current_query = query
        self.current_page = 1
        self.last_page = None
        self.total_found = None
        self.results = []
        self.pending_load = False
        self.auto_prefetch_done = False
        self.preload_queue = []
        self.preload_inflight = False
        self.results_list.clear()
        self.clear_details()
        self.set_loading(True, f"Buscando: {query}...")
        worker = ModSearchWorker(query=query, page=1)
        worker.signals.finished.connect(self.on_results_reset)
        self.thread_pool.start(worker)

    def on_results_reset(self, payload, error):
        self.set_loading(False, "")
        self.open_button.setEnabled(False)
        self.details.clear()

        if error:
            QMessageBox.warning(self, "Error", f"No se pudo cargar la lista.\n{error}")
            return

        items = payload.get("items", []) if isinstance(payload, dict) else []
        self.current_page = payload.get("page", 1) if isinstance(payload, dict) else 1
        self.last_page = payload.get("last_page") if isinstance(payload, dict) else None
        self.total_found = payload.get("total") if isinstance(payload, dict) else None

        if not items:
            self.status_label.setText("Sin resultados.")
            return

        self.results = items[:]
        self.update_status_label()
        for item in items:
            self.add_result_item(item)
        if self.results_list.count() > 0:
            self.results_list.setCurrentRow(0)
        self.start_preload_thumbnails()
        self.prefetch_second_page()

    def show_details(self, item):
        data = item.data(Qt.UserRole) or {}
        self.current_mod_id = data.get("id", "")
        self.name_label.setText(f"<b>Nombre:</b> {data.get('name','')}")
        author = data.get("author", "")
        self.author_label.setText(f"<b>Autor:</b> {author}")
        self.category_label.setText(f"<b>Categoría:</b> {data.get('category','')}")
        updated = data.get("updated_text", "")
        updated_title = data.get("updated_title", "")
        if updated_title:
            updated = f"{updated} ({updated_title})"
        self.updated_label.setText(f"<b>Actualizado:</b> {updated}")
        self.versions_label.setText(f"<b>Versiones:</b> {data.get('versions','')}")
        downloads = data.get("downloads_text", "")
        downloads_exact = data.get("downloads_exact", "")
        if downloads_exact:
            downloads = f"{downloads} ({downloads_exact})"
        self.downloads_label.setText(f"<b>Descargas:</b> {downloads}")
        self.details.setPlainText(data.get("description", ""))
        self.set_thumbnail(data.get("thumbnail", ""))
        self.open_button.setEnabled(False)
        self.load_modinfo(data.get("id"))

    def open_re146(self):
        item = self.results_list.currentItem()
        if not item:
            return
        data = item.data(Qt.UserRole) or {}
        mod_id = data.get("id")
        if not mod_id:
            return
        info = self.modinfo_cache.get(mod_id)
        if not info:
            return
        latest = self.get_latest_release(info)
        if not latest:
            return
        version = latest.get("version")
        if not version:
            return
        anticache = random.random()
        download_url = f"{DOWNLOAD_BASE}/{mod_id}/{version}.zip?anticache={anticache:.18f}"
        filename = f"{mod_id}_{version}.zip"
        folder = load_config().get("folder_path", DEFAULT_CONFIG["folder_path"])
        output_path = os.path.join(folder, filename)
        self.open_button.setEnabled(False)
        worker = DownloadWorker(download_url, output_path)
        worker.signals.finished.connect(self.on_download_finished)
        self.thread_pool.start(worker)

    def update_status_label(self):
        total = self.total_found
        shown = len(self.results)
        if total is not None:
            self.status_label.setText(f"{shown}/{total} mods cargados.")
        else:
            self.status_label.setText(f"{shown} mods cargados.")

    def add_result_item(self, item):
        lw_item = QListWidgetItem(item["name"])
        lw_item.setData(Qt.UserRole, item)
        self.results_list.addItem(lw_item)
        thumb = item.get("thumbnail")
        if thumb and thumb not in self.image_cache:
            self.preload_queue.append(thumb)

    def clear_details(self):
        self.thumb_label.clear()
        self.spinner_movie.stop()
        self.thumb_stack.setCurrentWidget(self.thumb_label)
        self.name_label.setText("<b>Nombre:</b>")
        self.author_label.setText("<b>Autor:</b>")
        self.category_label.setText("<b>Categoría:</b>")
        self.updated_label.setText("<b>Actualizado:</b>")
        self.versions_label.setText("<b>Versiones:</b>")
        self.downloads_label.setText("<b>Descargas:</b>")
        self.details.clear()

    def set_thumbnail(self, url):
        self.current_thumb_url = url or ""
        if not url:
            self.thumb_label.clear()
            self.spinner_movie.stop()
            self.thumb_stack.setCurrentWidget(self.thumb_label)
            return
        if url in self.image_cache:
            self.thumb_label.setPixmap(self.image_cache[url])
            self.spinner_movie.stop()
            self.thumb_stack.setCurrentWidget(self.thumb_label)
            return
        self.thumb_stack.setCurrentWidget(self.spinner_label)
        self.spinner_movie.start()
        worker = ImageLoaderWorker(url)
        worker.signals.finished.connect(self.on_image_ready)
        self.thread_pool.start(worker)

    def on_image_ready(self, url, pixmap):
        if url != self.current_thumb_url:
            return
        if pixmap.isNull():
            self.thumb_label.clear()
            self.spinner_movie.stop()
            self.thumb_stack.setCurrentWidget(self.thumb_label)
            return
        scaled = pixmap.scaled(
            self.thumb_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.image_cache[url] = scaled
        self.thumb_label.setPixmap(scaled)
        self.spinner_movie.stop()
        self.thumb_stack.setCurrentWidget(self.thumb_label)

    def on_scroll(self, value):
        if self.is_loading or self.pending_load:
            return
        bar = self.results_list.verticalScrollBar()
        maximum = bar.maximum()
        if maximum <= 0:
            return
        if value / maximum < 0.8:
            return
        if self.last_page is not None and self.current_page >= self.last_page:
            return
        self.pending_load = True
        self.load_next_page()

    def load_next_page(self):
        next_page = self.current_page + 1
        self.set_loading(True, "Cargando más...")
        if self.current_query:
            worker = ModSearchWorker(query=self.current_query, page=next_page)
        else:
            worker = ModSearchWorker(mode=self.current_mode, page=next_page)
        worker.signals.finished.connect(self.on_results_append)
        self.thread_pool.start(worker)

    def on_results_append(self, payload, error):
        self.set_loading(False, "")
        self.pending_load = False
        if error:
            self.status_label.setText("Error cargando más resultados.")
            return
        items = payload.get("items", []) if isinstance(payload, dict) else []
        self.current_page = payload.get("page", self.current_page) if isinstance(payload, dict) else self.current_page
        self.last_page = payload.get("last_page", self.last_page) if isinstance(payload, dict) else self.last_page
        self.total_found = payload.get("total", self.total_found) if isinstance(payload, dict) else self.total_found
        if not items:
            return
        existing_urls = {item.get("url") for item in self.results}
        for item in items:
            if item.get("url") in existing_urls:
                continue
            self.results.append(item)
            self.add_result_item(item)
        self.update_status_label()
        self.start_preload_thumbnails()

    def on_current_item_changed(self, current, _previous):
        if current:
            self.show_details(current)

    def prefetch_second_page(self):
        if self.auto_prefetch_done:
            return
        if self.last_page is None or self.current_page >= self.last_page:
            return
        self.auto_prefetch_done = True
        self.load_next_page()

    def start_preload_thumbnails(self):
        if self.preload_inflight:
            return
        while self.preload_queue:
            url = self.preload_queue.pop(0)
            if url in self.image_cache:
                continue
            self.preload_inflight = True
            worker = ImageLoaderWorker(url)
            worker.signals.finished.connect(self.on_preload_image_ready)
            self.thread_pool.start(worker)
            break

    def on_preload_image_ready(self, url, pixmap):
        self.preload_inflight = False
        if not pixmap.isNull():
            scaled = pixmap.scaled(
                self.thumb_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self.image_cache[url] = scaled
        self.start_preload_thumbnails()

    def load_modinfo(self, mod_id):
        if not mod_id:
            return
        if mod_id in self.modinfo_cache:
            self.apply_modinfo(mod_id, self.modinfo_cache[mod_id])
            return
        worker = ModInfoWorker(mod_id)
        worker.signals.finished.connect(self.on_modinfo_ready)
        self.thread_pool.start(worker)

    def on_modinfo_ready(self, mod_id, data, error):
        if error:
            if mod_id == self.current_mod_id:
                self.details.setPlainText("No se pudo cargar la descripción.")
            return
        if not data:
            return
        self.modinfo_cache[mod_id] = data
        if mod_id == self.current_mod_id:
            self.apply_modinfo(mod_id, data)

    def apply_modinfo(self, mod_id, data):
        description = data.get("description") or ""
        self.details.setPlainText(description.strip() or "Sin descripción.")
        latest = self.get_latest_release(data)
        self.open_button.setEnabled(bool(latest))

    def get_latest_release(self, data):
        releases = data.get("releases") or []
        if not releases:
            return None
        def key_fn(r):
            return r.get("released_at") or ""
        return max(releases, key=key_fn)

    def on_download_finished(self, ok, message):
        if ok:
            QMessageBox.information(self, "Descarga completa", f"Guardado en:\n{message}")
        else:
            QMessageBox.warning(self, "Error", f"No se pudo descargar.\n{message}")
        self.open_button.setEnabled(True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", default="factorio")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    window = ModSearchWindow(game=args.game)
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
