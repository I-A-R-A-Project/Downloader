import argparse, random, sys, subprocess, requests, re, json, tempfile
from PyQt5.QtCore import Qt, QThreadPool, QSize
from PyQt5.QtGui import QMovie
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QPushButton, QComboBox,
    QTextEdit, QMessageBox, QStackedLayout, QDialog, QDialogButtonBox
)
from settings_dialog import load_config, DEFAULT_CONFIG
from mod_paths_dialog import ModPathsDialog, DEFAULT_MOD_PATHS
from workers import (
    ImageLoaderWorker,
    FactorioSearchWorker,
    FactorioInfoWorker,
    DependencyResolveWorker,
    MODINFO_URL,
)


SPINNER_PATH = "spinner.gif"
DOWNLOAD_BASE = "https://mods-storage.re146.dev"


class FactorioCartDialog(QDialog):
    def __init__(self, items, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Carrito de descargas")
        self.resize(520, 360)
        self.items = items
        layout = QVBoxLayout(self)
        label = QLabel("Elementos en el carrito:")
        layout.addWidget(label)
        self.list_widget = QListWidget()
        for item in self.items:
            title = item.get("title") or "Sin título"
            self.list_widget.addItem(QListWidgetItem(title))
        layout.addWidget(self.list_widget)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons_row = QHBoxLayout()
        self.remove_button = QPushButton("Quitar seleccionado")
        self.remove_button.clicked.connect(self.remove_selected)
        buttons_row.addWidget(self.remove_button)
        buttons_row.addStretch(1)
        buttons_row.addWidget(buttons)
        layout.addLayout(buttons_row)

    def remove_selected(self):
        row = self.list_widget.currentRow()
        if row < 0:
            return
        self.list_widget.takeItem(row)
        if row < len(self.items):
            self.items.pop(row)

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
        self.cart_items = []

        layout = QVBoxLayout()

        top_row = QHBoxLayout()
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Updated", "updated")
        self.mode_combo.addItem("Downloaded", "downloaded")
        self.mode_combo.addItem("Trending", "trending")
        top_row.addWidget(QLabel("Listado:"))
        top_row.addWidget(self.mode_combo)
        top_row.addStretch(1)
        self.settings_button = QPushButton("Carpetas de Mods ⚙")
        self.settings_button.clicked.connect(self.open_settings_dialog)
        top_row.addWidget(self.settings_button)
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
        self.cart_button = QPushButton("Carrito (0)")
        self.cart_button.clicked.connect(self.open_cart)
        self.cart_button.setEnabled(False)
        self.open_button = QPushButton("Agregar al carrito")
        self.open_button.clicked.connect(self.add_to_cart)
        self.open_button.setEnabled(False)
        bottom_row.addWidget(self.status_label)
        bottom_row.addStretch(1)
        bottom_row.addWidget(self.cart_button)
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
        worker = FactorioSearchWorker(mode=mode, page=1)
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
        worker = FactorioSearchWorker(query=query, page=1)
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

    def add_to_cart(self):
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
        added = self.add_cart_items([self.build_cart_item_data(mod_id, version)])
        if added:
            self.update_cart_button()
            self.status_label.setText(f"Agregado: {added[0]['title']}")
        else:
            self.status_label.setText("Este mod ya está en el carrito.")

        deps = self.get_release_dependencies(latest)
        if not deps:
            return

        self.status_label.setText("Resolviendo dependencias...")
        worker = DependencyResolveWorker(self, deps, visited={mod_id})
        worker.signals.progress.connect(self.status_label.setText)
        worker.signals.finished.connect(self.on_dependencies_resolved)
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
            worker = FactorioSearchWorker(query=self.current_query, page=next_page)
        else:
            worker = FactorioSearchWorker(mode=self.current_mode, page=next_page)
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
        worker = FactorioInfoWorker(mod_id)
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

    def get_release_dependencies(self, release):
        info_json = release.get("info_json") or {}
        deps = info_json.get("dependencies") or []
        return [d for d in deps if isinstance(d, str)]

    def build_cart_item_data(self, mod_id, version):
        anticache = random.random()
        download_url = f"{DOWNLOAD_BASE}/{mod_id}/{version}.zip?anticache={anticache:.18f}"
        title = f"{mod_id}_{version}.zip"
        return {
            "title": title,
            "url": download_url,
            "mod_id": mod_id,
            "version": version,
        }

    def add_cart_items(self, items):
        added = []
        for item in items:
            if not item:
                continue
            if any(
                existing.get("mod_id") == item.get("mod_id")
                and existing.get("version") == item.get("version")
                for existing in self.cart_items
            ):
                continue
            self.cart_items.append(item)
            added.append(item)
        return added

    def resolve_dependencies(self, dependencies, visited=None, progress_cb=None):
        if visited is None:
            visited = set()
        resolved = []
        for dep in dependencies:
            dep_name, constraint = self.parse_dependency(dep)
            if not dep_name:
                continue
            if dep_name in visited:
                continue
            visited.add(dep_name)
            if progress_cb:
                progress_cb(f"Resolviendo dependencia: {dep_name}")
            info = self.fetch_modinfo(dep_name)
            if not info:
                continue
            release = self.select_release_for_constraint(info, constraint)
            if not release:
                continue
            version = release.get("version")
            if not version:
                continue
            resolved.append(self.build_cart_item_data(dep_name, version))
            nested_deps = self.get_release_dependencies(release)
            if nested_deps:
                resolved.extend(self.resolve_dependencies(nested_deps, visited, progress_cb))
        return resolved

    def parse_dependency(self, dep):
        dep = dep.strip()
        if not dep:
            return None, None

        if dep.startswith(("?", "!", "~")):
            if dep.startswith("?") or dep.startswith("!"):
                return None, None
            dep = dep.lstrip("?~!").strip()

        if dep.startswith("(") and dep.endswith(")"):
            dep = dep[1:-1].strip()

        if not dep:
            return None, None

        parts = dep.split()
        name = parts[0].strip()
        if not name or name.lower() == "base":
            return None, None

        constraint = None
        if len(parts) >= 3 and parts[1] in (">=", "<=", ">", "<", "="):
            constraint = (parts[1], parts[2])
        elif len(parts) >= 2:
            match = re.match(r"^(>=|<=|=|>|<)\s*(\S+)$", parts[1])
            if match:
                constraint = (match.group(1), match.group(2))
        return name, constraint

    def fetch_modinfo(self, mod_id):
        if mod_id in self.modinfo_cache:
            return self.modinfo_cache.get(mod_id)
        try:
            rand = random.random()
            params = {"rand": f"{rand:.18f}", "id": mod_id}
            resp = requests.get(MODINFO_URL, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            if data:
                self.modinfo_cache[mod_id] = data
            return data
        except Exception:
            return None

    def select_release_for_constraint(self, info, constraint):
        releases = info.get("releases") or []
        if not releases:
            return None
        if not constraint:
            return self.get_latest_release(info)
        op, ver = constraint
        filtered = []
        target = self.parse_version(ver)
        for rel in releases:
            rel_ver = rel.get("version") or ""
            if not rel_ver:
                continue
            if self.compare_versions(self.parse_version(rel_ver), target, op):
                filtered.append(rel)
        if not filtered:
            return None
        return max(filtered, key=lambda r: self.parse_version(r.get("version") or "0"))

    def parse_version(self, value):
        parts = re.split(r"[.\-+]", value.strip())
        nums = []
        for p in parts:
            if p.isdigit():
                nums.append(int(p))
            else:
                break
        return tuple(nums) if nums else (0,)

    def compare_versions(self, left, right, op):
        max_len = max(len(left), len(right))
        left = left + (0,) * (max_len - len(left))
        right = right + (0,) * (max_len - len(right))
        if op == ">=":
            return left >= right
        if op == "<=":
            return left <= right
        if op == ">":
            return left > right
        if op == "<":
            return left < right
        if op == "=":
            return left == right
        return False

    def update_cart_button(self):
        count = len(self.cart_items)
        self.cart_button.setText(f"Carrito ({count})")
        self.cart_button.setEnabled(count > 0)

    def open_cart(self):
        if not self.cart_items:
            return
        dialog = FactorioCartDialog(self.cart_items[:], self)
        if dialog.exec_() != QDialog.Accepted:
            return
        self.cart_items = dialog.items
        self.update_cart_button()
        urls = [item["url"] for item in self.cart_items if item.get("url")]
        if not urls:
            return
        config = load_config()
        mods_path = (
            config.get("factorio_mods_path")
            or config.get("mods_folder_path")
            or DEFAULT_MOD_PATHS["factorio_mods_path"]
        )
        entries = [{"url": url, "path": mods_path} for url in urls]
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json", encoding="utf-8") as f:
            json.dump(entries, f, indent=2)
            json_path = f.name
        subprocess.Popen(["python", "download_manager.py", json_path])
        self.cart_items = []
        self.update_cart_button()

    def open_settings_dialog(self):
        dialog = ModPathsDialog(self)
        dialog.exec_()

    def on_dependencies_resolved(self, items):
        added = self.add_cart_items(items)
        if added:
            self.update_cart_button()
            count = len(added)
            self.status_label.setText(f"Dependencias agregadas: {count}")
        else:
            self.status_label.setText("Dependencias ya estaban en el carrito.")

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
