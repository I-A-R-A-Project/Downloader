import argparse
import re
import sys
import requests
from bs4 import BeautifulSoup
from PyQt5.QtCore import Qt, QThreadPool, QRunnable, pyqtSignal, QObject, QUrl, QSize
from PyQt5.QtGui import QPixmap, QImage, QMovie
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QPushButton, QComboBox,
    QTextEdit, QDialog, QMessageBox, QStackedLayout
)
from PyQt5.QtWebEngineWidgets import QWebEngineView


FACTORIO_BASE = "https://mods.factorio.com"
RE146_BASE = "https://re146.dev/factorio/mods/en#"
SPINNER_PATH = "spinner.gif"


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
        mod_url = f"{FACTORIO_BASE}{href}"
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


class ModWebView(QDialog):
    def __init__(self, url, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Descargar mod (re146)")
        self.resize(900, 650)
        layout = QVBoxLayout()
        self.web_view = QWebEngineView()
        self.web_view.setUrl(QUrl(url))
        layout.addWidget(self.web_view)
        self.setLayout(layout)


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
        self.prefetch_second_page()

    def show_details(self, item):
        data = item.data(Qt.UserRole) or {}
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
        self.open_button.setEnabled(bool(data.get("url")))

    def open_re146(self):
        item = self.results_list.currentItem()
        if not item:
            return
        data = item.data(Qt.UserRole) or {}
        mod_url = data.get("url")
        if not mod_url:
            return
        re146_url = f"{RE146_BASE}{mod_url}"
        self.web_dialog = ModWebView(re146_url, self)
        self.web_dialog.show()

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
