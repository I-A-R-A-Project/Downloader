import json, os, random, re, subprocess, tempfile
from pathlib import Path
import requests
from PyQt5.QtCore import Qt, QThreadPool, QSize, QUrl, QUrlQuery, pyqtSignal
from PyQt5.QtGui import QColor, QCursor, QDesktopServices, QMovie, QPainter, QPixmap
from PyQt5.QtWebEngineWidgets import QWebEnginePage, QWebEngineProfile, QWebEngineView
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QPushButton, QComboBox,
    QMessageBox, QDialog, QDialogButtonBox, QFrame, QTabBar, QTabWidget,
    QCheckBox, QScrollArea, QAbstractItemView,
)
from config import load_config
from mod_search.path_dialog import ModPathsDialog, DEFAULT_MOD_PATHS
from media_search.workers import ImageLoaderWorker
from mod_search.workers import (
    DependencyResolveWorker,
    FactorioInfoWorker,
    FactorioPageWorker,
    FactorioSearchWorker,
    MODINFO_URL,
    MODRINTH_API_BASE,
    ModrinthProjectWorker,
    ModrinthSearchWorker,
    ModrinthVersionsWorker,
    fetch_modrinth_project_versions,
    fetch_modrinth_version,
    filter_required_modrinth_dependencies,
    build_factorio_request_url,
    modrinth_headers,
    normalize_modrinth_version_option,
)


DOWNLOAD_BASE = "https://mods-storage.re146.dev"
LIST_THUMBNAIL_SIZE = QSize(92, 92)
FACTORIO_MOD_HOST = "mods.factorio.com"
MODRINTH_MOD_HOST = "modrinth.com"
FACTORIO_BODY_BG = "https://webcdn.factorio.com/assets/img/web/bg_v4-85.jpg"
SPINNER_PATH = "spinner.gif"
DEFAULT_FACTORIO_FILTERS = {
    "expansion": [
        {"label": "Space Age", "value": "space-age"},
    ],
    "category": [
        {"label": "Content", "value": "content"},
        {"label": "Overhaul", "value": "overhaul"},
        {"label": "Tweaks", "value": "tweaks"},
        {"label": "Utilities", "value": "utilities"},
        {"label": "Scenarios", "value": "scenarios"},
        {"label": "Mod packs", "value": "mod-packs"},
        {"label": "Localizations", "value": "localizations"},
        {"label": "Internal", "value": "internal"},
        {"label": "No category", "value": "no-category"},
    ],
    "tag": [
        {"label": "Planets", "value": "planets"},
        {"label": "Transportation", "value": "transportation"},
        {"label": "Logistics", "value": "logistics"},
        {"label": "Trains", "value": "trains"},
        {"label": "Combat", "value": "combat"},
        {"label": "Armor", "value": "armor"},
        {"label": "Character", "value": "character"},
        {"label": "Enemies", "value": "enemies"},
        {"label": "Environment", "value": "environment"},
        {"label": "Mining", "value": "mining"},
        {"label": "Fluids", "value": "fluids"},
        {"label": "Logistic network", "value": "logistic-network"},
        {"label": "Circuit network", "value": "circuit-network"},
        {"label": "Manufacturing", "value": "manufacturing"},
        {"label": "Power", "value": "power"},
        {"label": "Storage", "value": "storage"},
        {"label": "Blueprints", "value": "blueprints"},
        {"label": "Cheats", "value": "cheats"},
    ],
}


def load_factorio_filter_definitions():
    return DEFAULT_FACTORIO_FILTERS


class SilentPage(QWebEnginePage):
    def javaScriptConsoleMessage(self, level, message, lineNumber, sourceID):
        pass


class ModWebPage(SilentPage):
    def __init__(self, window, page_key, current_mod_id, parent=None):
        super().__init__(parent)
        self.window = window
        self.page_key = page_key or ""
        self.current_mod_id = current_mod_id or ""

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if not is_main_frame:
            return True
        target = url.toString()
        if not target:
            return True
        if nav_type == QWebEnginePage.NavigationTypeLinkClicked:
            if self.window.handle_internal_navigation(target):
                return False
        if nav_type == QWebEnginePage.NavigationTypeLinkClicked:
            QDesktopServices.openUrl(url)
            return False
        return True


class ClickableLabel(QLabel):
    clicked = pyqtSignal()

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setCursor(QCursor(Qt.PointingHandCursor))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


class FactorioBackgroundWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.background_pixmap = QPixmap()

    def set_background_path(self, path):
        self.background_pixmap = QPixmap(path) if path else QPixmap()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#201810"))
        if not self.background_pixmap.isNull():
            x = (self.width() - self.background_pixmap.width()) // 2
            y = 0
            while y < self.height():
                painter.drawPixmap(x, y, self.background_pixmap)
                y += self.background_pixmap.height()
        super().paintEvent(event)


class ModResultItemWidget(QFrame):
    clicked = pyqtSignal()
    open_requested = pyqtSignal(str, str, str)

    def __init__(self, data, parent=None):
        super().__init__(parent)
        self.data = data
        self.setFrameShape(QFrame.StyledPanel)
        self.setObjectName("modResultCard")
        self.setStyleSheet(self._build_stylesheet(False))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)

        self.thumb_label = ClickableLabel()
        self.thumb_label.setFixedSize(LIST_THUMBNAIL_SIZE)
        self.thumb_label.setAlignment(Qt.AlignCenter)
        self.thumb_label.setStyleSheet(
            "background: rgba(0, 0, 0, 0.28);"
            "border: 1px solid #2e2623;"
            "padding: 2px;"
        )
        self.thumb_label.clicked.connect(self._emit_open_requested)
        layout.addWidget(self.thumb_label)

        text_col = QVBoxLayout()
        text_col.setSpacing(4)

        self.name_label = ClickableLabel(data.get("name") or "Sin título")
        self.name_label.setWordWrap(True)
        self.name_label.setStyleSheet("font-weight: 700; font-size: 15px; color: #ffe6c0;")
        self.name_label.clicked.connect(self._emit_open_requested)
        text_col.addWidget(self.name_label)

        description = (data.get("description") or "").strip()
        if description:
            self.description_label = QLabel(description)
            self.description_label.setWordWrap(True)
            self.description_label.setStyleSheet("color: #ddd4cc; line-height: 1.2;")
            text_col.addWidget(self.description_label)
        else:
            self.description_label = None

        text_col.addStretch(1)
        layout.addLayout(text_col, 1)

        meta_col = QVBoxLayout()
        meta_col.setSpacing(4)
        meta_col.setContentsMargins(0, 0, 0, 0)

        self.author_label = QLabel(f"Autor: {data.get('author') or 'Desconocido'}")
        self.author_label.setWordWrap(True)
        self.author_label.setAlignment(Qt.AlignRight | Qt.AlignTop)
        self.author_label.setStyleSheet("color: #7dcaed;")
        meta_col.addWidget(self.author_label)

        self.category_label = QLabel(f"Categoría: {data.get('category') or 'Sin categoría'}")
        self.category_label.setWordWrap(True)
        self.category_label.setAlignment(Qt.AlignRight | Qt.AlignTop)
        self.category_label.setStyleSheet("color: #ffe6c0;")
        meta_col.addWidget(self.category_label)

        updated = data.get("updated_text") or "Desconocido"
        updated_title = data.get("updated_title") or ""
        if updated_title:
            updated = f"{updated} ({updated_title})"
        self.updated_label = QLabel(f"Actualizado: {updated}")
        self.updated_label.setWordWrap(True)
        self.updated_label.setAlignment(Qt.AlignRight | Qt.AlignTop)
        self.updated_label.setStyleSheet("color: #a6a6a6;")
        meta_col.addWidget(self.updated_label)
        meta_col.addStretch(1)
        layout.addLayout(meta_col)

        for label in (
            self.author_label,
            self.category_label,
            self.updated_label,
            self.description_label,
        ):
            if label is not None:
                label.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def set_selected(self, selected):
        self.setStyleSheet(self._build_stylesheet(selected))

    def set_thumbnail(self, pixmap):
        if pixmap is None or pixmap.isNull():
            self.thumb_label.clear()
            return
        self.thumb_label.setPixmap(pixmap)

    def _emit_open_requested(self):
        self.open_requested.emit(
            self.data.get("id", ""),
            self.data.get("url", ""),
            self.data.get("name", "") or "Mod",
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    @staticmethod
    def _build_stylesheet(selected):
        background = "#414040" if selected else "#313031"
        border_color = "#ffa200" if selected else "#2e2623"
        return (
            "QFrame#modResultCard {"
            f"background: {background};"
            f"border: 4px solid {border_color};"
            "color: #ffffff;"
            "}"
            "QFrame#modResultCard:hover {"
            "background: #3b3a3b;"
            "}"
        )


class VersionSelectDialog(QDialog):
    def __init__(self, project_name, options, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Elegir version - {project_name}")
        self.resize(760, 420)
        self.selected_option = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Selecciona la version/archivo a descargar:"))

        self.list_widget = QListWidget()
        for option in options:
            item = QListWidgetItem(self.build_label(option))
            item.setData(Qt.UserRole, option)
            self.list_widget.addItem(item)
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)
        self.list_widget.itemDoubleClicked.connect(lambda _item: self.accept_selection())
        layout.addWidget(self.list_widget, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept_selection)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def build_label(option):
        parts = [
            option.get("version_number") or option.get("name") or "Version",
            option.get("filename") or "archivo",
        ]
        loaders = option.get("loaders") or []
        if loaders:
            parts.append("loaders: " + ", ".join(loaders[:3]))
        game_versions = option.get("game_versions") or []
        if game_versions:
            parts.append("mc: " + ", ".join(game_versions[:3]))
        published = option.get("published") or ""
        if published:
            parts.append("publicado: " + published)
        return " | ".join(parts)

    def accept_selection(self):
        item = self.list_widget.currentItem()
        if not item:
            return
        self.selected_option = item.data(Qt.UserRole) or None
        self.accept()


class CartDialog(QDialog):
    def __init__(self, items, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Carrito de descargas")
        self.resize(520, 360)
        self.items = items

        layout = QVBoxLayout(self)
        self.list_widget = QListWidget()
        for item in self.items:
            title = item.get("title") or "Sin título"
            self.list_widget.addItem(QListWidgetItem(title))
        layout.addWidget(self.list_widget)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        row = QHBoxLayout()
        self.remove_button = QPushButton("Quitar seleccionado")
        self.remove_button.clicked.connect(self.remove_selected)
        row.addWidget(self.remove_button)
        row.addStretch(1)
        row.addWidget(buttons)
        layout.addLayout(row)

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
        self.game = self.normalize_game(game)
        self.setWindowTitle(self.window_title_for_game())
        self.resize(980, 640)
        self.thread_pool = QThreadPool()
        self.results = []
        self.current_mode = self.default_mode()
        self.current_query = ""
        self.current_page = 1
        self.last_page = None
        self.total_found = None
        self.is_loading = False
        self.pending_load = False
        self.image_cache = {}
        self.preload_queue = []
        self.preload_inflight = False
        self.result_widgets = {}
        self.modinfo_cache = {}
        self.version_cache = {}
        self.current_mod_id = ""
        self.pending_add_mod_id = ""
        self.web_tabs = {}
        self.internal_page_cache = {}
        self.cart_items = []
        self.browse_tab_index = -1
        self.factorio_filter_definitions = load_factorio_filter_definitions()
        self.factorio_filter_controls = {"expansion": None, "category": {}, "tag": {}}
        self.factorio_filter_state = {
            "expansion": "",
            "category": {"include": set(), "exclude": set()},
            "tag": {"include": set(), "exclude": set()},
        }
        self.current_request_url = ""
        self.web_cache_dir = os.path.join(tempfile.gettempdir(), "MediaSearchPrototype", "mod_search_web_cache")
        self.factorio_theme_dir = os.path.join(tempfile.gettempdir(), "MediaSearchPrototype", "mod_search_theme")
        self.factorio_background_path = ""

        root = QVBoxLayout(self)
        self.configure_web_profile()
        self.prepare_factorio_theme_assets()

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self.on_tab_changed)
        root.addWidget(self.tabs, 1)

        browse_tab = FactorioBackgroundWidget() if self.game == "factorio" else QWidget()
        if self.game == "factorio":
            browse_tab.setObjectName("factorioBrowseTab")
            browse_tab.set_background_path(self.factorio_background_path)
        browse_layout = QVBoxLayout(browse_tab)

        top_row = QHBoxLayout()
        self.mode_combo = QComboBox()
        self.populate_mode_combo()
        top_row.addWidget(QLabel("Listado:"))
        top_row.addWidget(self.mode_combo)
        top_row.addStretch(1)
        self.settings_button = QPushButton("Carpetas de Mods ⚙")
        self.settings_button.clicked.connect(self.open_settings_dialog)
        top_row.addWidget(self.settings_button)
        browse_layout.addLayout(top_row)

        search_row = QHBoxLayout()
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText(self.search_placeholder())
        self.search_bar.returnPressed.connect(self.search_mods)
        self.search_button = QPushButton("Buscar")
        self.search_button.clicked.connect(self.search_mods)
        search_row.addWidget(self.search_bar)
        search_row.addWidget(self.search_button)
        browse_layout.addLayout(search_row)

        self.filter_url_label = QLabel("")
        self.filter_url_label.setObjectName("factorioUrlPreview")
        self.filter_url_label.setWordWrap(True)
        self.filter_url_label.setStyleSheet("color: #6b7280; font-size: 11px;")
        self.filter_url_label.setVisible(self.game == "factorio")
        browse_layout.addWidget(self.filter_url_label)

        content_row = QHBoxLayout()

        self.results_list = QListWidget()
        self.results_list.setObjectName("factorioResultsList")
        self.results_list.setSpacing(8)
        self.results_list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.results_list.verticalScrollBar().setSingleStep(24)
        self.results_list.itemClicked.connect(self.show_details)
        self.results_list.currentItemChanged.connect(self.on_current_item_changed)
        self.results_list.verticalScrollBar().valueChanged.connect(self.on_scroll)
        content_row.addWidget(self.results_list, 1)

        self.factorio_filters_panel = self.build_factorio_filters_panel()
        if self.factorio_filters_panel is not None:
            content_row.addWidget(self.factorio_filters_panel)

        browse_layout.addLayout(content_row, 1)

        self.browse_tab_index = self.tabs.addTab(browse_tab, "Explorar")
        self._hide_close_button(self.browse_tab_index)

        bottom_row = QHBoxLayout()
        self.status_label = QLabel("")
        self.status_label.setObjectName("factorioStatusLabel")
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
        root.addLayout(bottom_row)

        self.mode_combo.currentIndexChanged.connect(self.load_browse)
        self.apply_factorio_theme()
        self.load_browse()

    @staticmethod
    def normalize_game(game):
        text = (game or "").strip().lower()
        if text in {"minecraft", "modrinth"}:
            return "minecraft"
        return "factorio"

    def window_title_for_game(self):
        if self.game == "minecraft":
            return "Buscar mods - Minecraft (Modrinth)"
        return "Buscar mods - Factorio"

    def configure_web_profile(self):
        try:
            os.makedirs(self.web_cache_dir, exist_ok=True)
        except OSError:
            return
        profile = QWebEngineProfile.defaultProfile()
        profile.setHttpCacheType(QWebEngineProfile.DiskHttpCache)
        profile.setCachePath(self.web_cache_dir)
        profile.setPersistentStoragePath(self.web_cache_dir)

    def prepare_factorio_theme_assets(self):
        if self.game != "factorio":
            return
        try:
            os.makedirs(self.factorio_theme_dir, exist_ok=True)
        except OSError:
            return
        background_path = os.path.join(self.factorio_theme_dir, "bg_v4-85.jpg")
        if not os.path.exists(background_path):
            try:
                response = requests.get(FACTORIO_BODY_BG, timeout=20)
                response.raise_for_status()
                Path(background_path).write_bytes(response.content)
            except Exception:
                self.factorio_background_path = ""
                return
        self.factorio_background_path = background_path

    def apply_factorio_theme(self):
        if self.game != "factorio":
            return
        self.setStyleSheet(
            f"""
            QWidget {{
                color: #ffffff;
            }}
            QTabBar::tab {{
                color: #000000;
            }}
            QTabBar::tab:selected {{
                color: #000000;
            }}
            QLabel#factorioUrlPreview {{
                color: #a6a6a6;
                background: rgba(0, 0, 0, 0.18);
                padding: 4px 8px;
            }}
            QLabel#factorioStatusLabel {{
                color: #000000;
            }}
            QListWidget#factorioResultsList {{
                background: transparent;
                border: none;
                outline: none;
            }}
            QListWidget#factorioResultsList::item {{
                background: transparent;
                border: none;
            }}
            QScrollArea#factorioFilterScroll, QScrollArea#factorioFilterScroll > QWidget > QWidget {{
                background: transparent;
                border: none;
            }}
            QFrame#factorioFilterPanel {{
                background: rgba(32, 24, 16, 0.68);
                border-left: 1px solid rgba(255, 230, 192, 0.18);
            }}
            QWidget#factorioFilterBody {{
                background: transparent;
            }}
            QLabel#factorioFilterTitle, QLabel#factorioLoadingTitle {{
                color: #ffe6c0;
                font-weight: 700;
                font-size: 15px;
            }}
            QLabel#factorioFilterHint, QLabel#factorioLoadingSubtitle {{
                color: #d6cfc8;
            }}
            QFrame#factorioLoadingPanel {{
                background-color: #313031;
                border: 4px solid #2e2623;
                min-width: 360px;
                max-width: 420px;
            }}
            QLineEdit, QComboBox, QListWidget, QPushButton {{
                background-color: rgba(49, 48, 49, 0.92);
                border: 2px solid #2e2623;
                color: #ffffff;
                padding: 4px 6px;
            }}
            QPushButton:hover {{
                border-color: #ffa200;
            }}
            QLabel#factorioSectionLabel {{
                color: #ffe6c0;
                font-weight: 700;
            }}
            """
        )

    def build_factorio_loading_placeholder(self, message="Cargando página...", subtitle=""):
        holder = FactorioBackgroundWidget()
        holder.setObjectName("factorioPagePlaceholder")
        holder.set_background_path(self.factorio_background_path)
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(24, 24, 24, 24)

        layout.addStretch(1)
        panel = QFrame()
        panel.setObjectName("factorioLoadingPanel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(24, 24, 24, 24)
        panel_layout.setSpacing(12)

        spinner_label = QLabel()
        spinner_label.setAlignment(Qt.AlignCenter)
        spinner_movie = QMovie(SPINNER_PATH)
        spinner_movie.setScaledSize(QSize(40, 40))
        spinner_label.setMovie(spinner_movie)
        spinner_movie.start()
        holder.spinner_movie = spinner_movie
        holder.spinner_label = spinner_label
        panel_layout.addWidget(spinner_label)

        title_label = QLabel(message)
        title_label.setObjectName("factorioLoadingTitle")
        title_label.setAlignment(Qt.AlignCenter)
        panel_layout.addWidget(title_label)
        holder.title_label = title_label

        subtitle_label = QLabel(subtitle or "Preparando la página del portal con el estilo original.")
        subtitle_label.setObjectName("factorioLoadingSubtitle")
        subtitle_label.setWordWrap(True)
        subtitle_label.setAlignment(Qt.AlignCenter)
        panel_layout.addWidget(subtitle_label)
        holder.subtitle_label = subtitle_label

        layout.addWidget(panel, alignment=Qt.AlignCenter)
        layout.addStretch(1)
        return holder

    def build_page_loading_placeholder(self, message="Cargando página..."):
        if self.game == "factorio":
            return self.build_factorio_loading_placeholder(message=message)
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(24, 24, 24, 24)
        label = QLabel(message)
        label.setAlignment(Qt.AlignCenter)
        layout.addStretch(1)
        layout.addWidget(label)
        layout.addStretch(1)
        holder.title_label = label
        holder.subtitle_label = None
        return holder

    def default_mode(self):
        return "popular" if self.game == "minecraft" else "updated"

    def search_placeholder(self):
        if self.game == "minecraft":
            return "Buscar mods de Minecraft..."
        return "Buscar mods..."

    def populate_mode_combo(self):
        self.mode_combo.clear()
        if self.game == "minecraft":
            self.mode_combo.addItem("Popular", "popular")
            self.mode_combo.addItem("Updated", "updated")
            self.mode_combo.addItem("Newest", "newest")
        else:
            self.mode_combo.addItem("Updated", "updated")
            self.mode_combo.addItem("Downloaded", "downloaded")
            self.mode_combo.addItem("Trending", "trending")

    def build_factorio_filters_panel(self):
        if self.game != "factorio":
            return None

        panel = QFrame()
        panel.setObjectName("factorioFilterPanel")
        panel.setFixedWidth(300)

        outer = QVBoxLayout(panel)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setObjectName("factorioFilterScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll)

        body = QWidget()
        body.setObjectName("factorioFilterBody")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        title = QLabel("Filtros del portal")
        title.setObjectName("factorioFilterTitle")
        title.setStyleSheet("font-weight: 600; font-size: 14px;")
        layout.addWidget(title)

        hint = QLabel("Marca Incluir o Excluir para modificar la URL de Factorio.")
        hint.setObjectName("factorioFilterHint")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #4b5563;")
        layout.addWidget(hint)

        expansion_combo = QComboBox()
        expansion_combo.addItem("Todas", "")
        for option in self.factorio_filter_definitions.get("expansion", []):
            expansion_combo.addItem(option["label"], option["value"])
        expansion_combo.currentIndexChanged.connect(self.on_factorio_filters_changed)
        self.factorio_filter_controls["expansion"] = expansion_combo
        layout.addWidget(QLabel("Expansión"))
        layout.addWidget(expansion_combo)

        for group_key, title_text in (("category", "Categorías"), ("tag", "Tags")):
            group_title = QLabel(title_text)
            group_title.setObjectName("factorioSectionLabel")
            group_title.setStyleSheet("font-weight: 600; margin-top: 8px;")
            layout.addWidget(group_title)
            for option in self.factorio_filter_definitions.get(group_key, []):
                row = QWidget()
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(0, 0, 0, 0)
                row_layout.setSpacing(6)
                label = QLabel(option["label"])
                label.setWordWrap(True)
                row_layout.addWidget(label, 1)

                include_box = QCheckBox("Incluir")
                exclude_box = QCheckBox("Excluir")
                include_box.toggled.connect(
                    lambda checked, group=group_key, value=option["value"]: self.on_factorio_filter_toggle(group, value, "include", checked)
                )
                exclude_box.toggled.connect(
                    lambda checked, group=group_key, value=option["value"]: self.on_factorio_filter_toggle(group, value, "exclude", checked)
                )
                row_layout.addWidget(include_box)
                row_layout.addWidget(exclude_box)
                layout.addWidget(row)
                self.factorio_filter_controls[group_key][option["value"]] = {
                    "include": include_box,
                    "exclude": exclude_box,
                }

        reset_button = QPushButton("Limpiar filtros")
        reset_button.clicked.connect(self.reset_factorio_filters)
        layout.addWidget(reset_button)
        layout.addStretch(1)

        scroll.setWidget(body)
        return panel

    def on_factorio_filter_toggle(self, group_key, value, mode, checked):
        controls = self.factorio_filter_controls.get(group_key, {}).get(value, {})
        if checked:
            other_mode = "exclude" if mode == "include" else "include"
            other_box = controls.get(other_mode)
            if other_box is not None and other_box.isChecked():
                other_box.blockSignals(True)
                other_box.setChecked(False)
                other_box.blockSignals(False)
                self.factorio_filter_state[group_key][other_mode].discard(value)
            self.factorio_filter_state[group_key][mode].add(value)
        else:
            self.factorio_filter_state[group_key][mode].discard(value)
        self.on_factorio_filters_changed()

    def on_factorio_filters_changed(self):
        if self.game != "factorio":
            return
        expansion_combo = self.factorio_filter_controls.get("expansion")
        if expansion_combo is not None:
            self.factorio_filter_state["expansion"] = expansion_combo.currentData() or ""
        self.refresh_factorio_filter_url_preview()
        self.reload_current_factorio_listing()

    def build_factorio_filter_params(self):
        if self.game != "factorio":
            return {}
        params = {}
        expansion = self.factorio_filter_state.get("expansion") or ""
        if expansion:
            params["expansion"] = expansion
        for group_key in ("category", "tag"):
            include_values = sorted(self.factorio_filter_state[group_key]["include"])
            exclude_values = sorted(self.factorio_filter_state[group_key]["exclude"])
            if include_values:
                params[group_key] = include_values
            exclude_key = f"exclude_{group_key}"
            if exclude_values:
                params[exclude_key] = exclude_values
        return params

    def refresh_factorio_filter_url_preview(self, request_url=None):
        if self.game != "factorio" or not hasattr(self, "filter_url_label"):
            return
        text = request_url or self.build_factorio_preview_url()
        self.current_request_url = text
        self.filter_url_label.setText(f"URL: {text}")
        self.filter_url_label.setToolTip(text)

    def build_factorio_preview_url(self):
        params = {
            "factorio_version": 2.0,
            "show_deprecated": False,
            "page": self.current_page or 1,
        }
        params.update(self.build_factorio_filter_params())
        if self.current_query:
            params["query"] = self.current_query
            base_url = f"https://{FACTORIO_MOD_HOST}/search"
        else:
            base_url = f"https://{FACTORIO_MOD_HOST}/browse/{self.current_mode or 'updated'}"
        return build_factorio_request_url(base_url, params)

    def reload_current_factorio_listing(self):
        if self.game != "factorio":
            return
        if self.current_query:
            self.search_mods()
            return
        self.load_browse()

    def reset_factorio_filters(self):
        if self.game != "factorio":
            return
        expansion_combo = self.factorio_filter_controls.get("expansion")
        if expansion_combo is not None:
            expansion_combo.blockSignals(True)
            expansion_combo.setCurrentIndex(0)
            expansion_combo.blockSignals(False)
        for group_key in ("category", "tag"):
            for controls in self.factorio_filter_controls.get(group_key, {}).values():
                for checkbox in controls.values():
                    checkbox.blockSignals(True)
                    checkbox.setChecked(False)
                    checkbox.blockSignals(False)
            self.factorio_filter_state[group_key]["include"].clear()
            self.factorio_filter_state[group_key]["exclude"].clear()
        self.factorio_filter_state["expansion"] = ""
        self.on_factorio_filters_changed()

    def set_factorio_filters_from_values(self, group_key, include_values=None, exclude_values=None):
        include_values = set(include_values or [])
        exclude_values = set(exclude_values or [])
        controls_map = self.factorio_filter_controls.get(group_key, {})
        self.factorio_filter_state[group_key]["include"].clear()
        self.factorio_filter_state[group_key]["exclude"].clear()
        for value, controls in controls_map.items():
            include_box = controls.get("include")
            exclude_box = controls.get("exclude")
            if include_box is not None:
                include_box.blockSignals(True)
                include_box.setChecked(value in include_values)
                include_box.blockSignals(False)
            if exclude_box is not None:
                exclude_box.blockSignals(True)
                exclude_box.setChecked(value in exclude_values)
                exclude_box.blockSignals(False)
            if value in include_values:
                self.factorio_filter_state[group_key]["include"].add(value)
            if value in exclude_values:
                self.factorio_filter_state[group_key]["exclude"].add(value)

    def query_values_from_url(self, url, key):
        query = QUrlQuery(QUrl(url))
        values = []
        for item_key, item_value in query.queryItems():
            if item_key == key and item_value:
                values.append(item_value)
        return values

    def apply_factorio_browse_url(self, url):
        if self.game != "factorio":
            return False
        parsed = QUrl(url)
        path = parsed.path().rstrip("/")
        query = QUrlQuery(parsed)

        mode = ""
        if path.startswith("/browse/"):
            mode = path.split("/browse/", 1)[1].split("/", 1)[0].strip()
        available_modes = {
            self.mode_combo.itemData(index)
            for index in range(self.mode_combo.count())
        }
        if mode and mode in available_modes:
            index = self.mode_combo.findData(mode)
            if index >= 0 and self.mode_combo.currentIndex() != index:
                self.mode_combo.blockSignals(True)
                self.mode_combo.setCurrentIndex(index)
                self.mode_combo.blockSignals(False)
            self.current_mode = mode

        expansion = query.queryItemValue("expansion") or ""
        expansion_combo = self.factorio_filter_controls.get("expansion")
        if expansion_combo is not None:
            target_index = expansion_combo.findData(expansion)
            if target_index < 0:
                target_index = 0
            expansion_combo.blockSignals(True)
            expansion_combo.setCurrentIndex(target_index)
            expansion_combo.blockSignals(False)
        self.factorio_filter_state["expansion"] = expansion if expansion else ""
        self.set_factorio_filters_from_values(
            "category",
            include_values=self.query_values_from_url(url, "category"),
            exclude_values=self.query_values_from_url(url, "exclude_category"),
        )
        self.set_factorio_filters_from_values(
            "tag",
            include_values=self.query_values_from_url(url, "tag"),
            exclude_values=self.query_values_from_url(url, "exclude_tag"),
        )

        query_text = query.queryItemValue("query") or ""
        self.search_bar.setText(query_text)
        self.tabs.setCurrentIndex(self.browse_tab_index)
        self.refresh_factorio_filter_url_preview(url)
        if query_text:
            self.search_mods()
        else:
            self.load_browse()
        return True

    def add_factorio_mod_to_cart(self, mod_id):
        if not mod_id:
            return False
        self.current_mod_id = mod_id
        self.update_add_button_state()
        info = self.modinfo_cache.get(mod_id)
        if info:
            self.finish_add_factorio_to_cart(mod_id, info)
            return True
        self.pending_add_mod_id = mod_id
        self.status_label.setText(f"Cargando mod: {mod_id}...")
        self.load_modinfo(mod_id)
        return True

    def extract_factorio_download_mod_id(self, url):
        parsed = QUrl(url)
        path = parsed.path().rstrip("/")
        if path == "/login":
            next_target = QUrlQuery(parsed).queryItemValue("next") or ""
            if next_target:
                match = re.match(r"^/mod/([^/]+)", next_target)
                if match:
                    return match.group(1)
        if "/download" in path:
            mod_id = self.extract_mod_id_from_url(url)
            if mod_id:
                return mod_id
        return ""

    def handle_internal_navigation(self, target):
        parsed = QUrl(target)
        host = parsed.host().lower()
        if not host:
            return False
        if host == FACTORIO_MOD_HOST:
            mod_id = self.extract_mod_id_from_url(target)
            if mod_id:
                self.open_mod_tab(mod_id, target, self.project_name_for_id(mod_id))
                return True
            download_mod_id = self.extract_factorio_download_mod_id(target)
            if download_mod_id:
                return self.add_factorio_mod_to_cart(download_mod_id)
            path = parsed.path().rstrip("/")
            if path in {"", "/"} or path.startswith("/browse") or path == "/search":
                return self.apply_factorio_browse_url(target)
            page_key = self.build_internal_page_key(target)
            self.open_internal_page(page_key, target, title=self.build_internal_page_title(target))
            return True
        if host == MODRINTH_MOD_HOST:
            mod_id = self.extract_mod_id_from_url(target)
            if mod_id:
                self.open_mod_tab(mod_id, target, self.project_name_for_id(mod_id))
                return True
            page_key = self.build_internal_page_key(target)
            self.open_internal_page(page_key, target, title=self.build_internal_page_title(target))
            return True
        return False

    def _hide_close_button(self, index):
        tab_bar = self.tabs.tabBar()
        tab_bar.setTabButton(index, QTabBar.LeftSide, None)
        tab_bar.setTabButton(index, QTabBar.RightSide, None)

    def set_loading(self, loading, message=""):
        self.is_loading = loading
        self.search_button.setEnabled(not loading)
        self.search_bar.setEnabled(not loading)
        self.status_label.setText(message)

    def create_search_worker(self, mode=None, query=None, page=1):
        if self.game == "minecraft":
            return ModrinthSearchWorker(mode=mode, query=query, page=page)
        return FactorioSearchWorker(
            mode=mode,
            query=query,
            page=page,
            extra_params=self.build_factorio_filter_params(),
        )

    def create_info_worker(self, mod_id):
        if self.game == "minecraft":
            return ModrinthProjectWorker(mod_id)
        return FactorioInfoWorker(mod_id)

    def load_browse(self):
        mode = self.mode_combo.currentData()
        self.current_mode = mode
        self.current_query = ""
        self.current_page = 1
        self.last_page = None
        self.total_found = None
        self.results = []
        self.pending_load = False
        self.preload_queue = []
        self.preload_inflight = False
        self.current_mod_id = ""
        self.pending_add_mod_id = ""
        self.results_list.clear()
        self.result_widgets.clear()
        self.update_add_button_state()
        self.refresh_factorio_filter_url_preview()
        self.set_loading(True, f"Cargando listado: {mode}...")
        worker = self.create_search_worker(mode=mode, page=1)
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
        self.preload_queue = []
        self.preload_inflight = False
        self.current_mod_id = ""
        self.pending_add_mod_id = ""
        self.results_list.clear()
        self.result_widgets.clear()
        self.update_add_button_state()
        self.refresh_factorio_filter_url_preview()
        self.set_loading(True, f"Buscando: {query}...")
        worker = self.create_search_worker(query=query, page=1)
        worker.signals.finished.connect(self.on_results_reset)
        self.thread_pool.start(worker)

    def on_results_reset(self, payload, error):
        self.set_loading(False, "")
        self.current_mod_id = ""
        self.update_add_button_state()

        if error:
            QMessageBox.warning(self, "Error", f"No se pudo cargar la lista.\n{error}")
            return

        items = payload.get("items", []) if isinstance(payload, dict) else []
        self.current_page = payload.get("page", 1) if isinstance(payload, dict) else 1
        self.last_page = payload.get("last_page") if isinstance(payload, dict) else None
        self.total_found = payload.get("total") if isinstance(payload, dict) else None
        self.refresh_factorio_filter_url_preview(payload.get("request_url", "") if isinstance(payload, dict) else "")

        if not items:
            self.status_label.setText("Sin resultados.")
            return

        self.results = items[:]
        for item in items:
            self.add_result_item(item)
        self.update_status_label()
        if self.results_list.count() > 0:
            self.results_list.setCurrentRow(0)
        self.start_preload_thumbnails()
        self.prefetch_second_page()

    def add_result_item(self, item):
        lw_item = QListWidgetItem()
        lw_item.setData(Qt.UserRole, item)
        widget = ModResultItemWidget(item)
        widget.clicked.connect(lambda item_ref=lw_item: self.select_result_item(item_ref))
        widget.open_requested.connect(
            lambda mod_id, url, title, item_ref=lw_item: self.open_result_from_item(item_ref, mod_id, url, title)
        )
        lw_item.setSizeHint(widget.sizeHint())
        self.results_list.addItem(lw_item)
        self.results_list.setItemWidget(lw_item, widget)

        thumb = item.get("thumbnail") or ""
        if thumb:
            self.result_widgets.setdefault(thumb, []).append(widget)
            if thumb in self.image_cache:
                widget.set_thumbnail(self.image_cache[thumb])
            elif thumb not in self.preload_queue:
                self.preload_queue.append(thumb)

    def select_result_item(self, item):
        self.results_list.setCurrentItem(item)
        self.tabs.setCurrentIndex(self.browse_tab_index)

    def open_result_from_item(self, item, mod_id, url, title):
        self.select_result_item(item)
        self.open_mod_tab(mod_id, url, title)

    def show_details(self, item):
        data = item.data(Qt.UserRole) or {}
        self.current_mod_id = data.get("id", "")
        self.update_item_selection()
        self.update_add_button_state()
        self.load_modinfo(self.current_mod_id)

    def on_current_item_changed(self, current, previous):
        if previous:
            old_widget = self.results_list.itemWidget(previous)
            if old_widget:
                old_widget.set_selected(False)
        if current:
            self.show_details(current)
        else:
            self.current_mod_id = ""
            self.update_add_button_state()

    def update_item_selection(self):
        current = self.results_list.currentItem()
        for index in range(self.results_list.count()):
            item = self.results_list.item(index)
            widget = self.results_list.itemWidget(item)
            if widget:
                widget.set_selected(item is current)

    def add_to_cart(self):
        mod_id = self.get_active_mod_id()
        if not mod_id:
            return

        if self.game == "minecraft":
            versions = self.version_cache.get(mod_id)
            if versions is not None:
                self.finish_add_modrinth_to_cart(mod_id, versions)
                return
            self.pending_add_mod_id = mod_id
            self.status_label.setText(f"Cargando versiones: {mod_id}...")
            self.load_mod_versions(mod_id)
            return

        info = self.modinfo_cache.get(mod_id)
        if info:
            self.finish_add_factorio_to_cart(mod_id, info)
            return
        self.pending_add_mod_id = mod_id
        self.status_label.setText(f"Cargando mod: {mod_id}...")
        self.load_modinfo(mod_id)

    def get_active_mod_id(self):
        index = self.tabs.currentIndex()
        if index == self.browse_tab_index:
            item = self.results_list.currentItem()
            if not item:
                return ""
            data = item.data(Qt.UserRole) or {}
            return data.get("id", "")
        tab = self.tabs.widget(index)
        return getattr(tab, "mod_id", "")

    def update_add_button_state(self):
        if not hasattr(self, "open_button"):
            return
        self.open_button.setEnabled(bool(self.get_active_mod_id()))

    def update_status_label(self):
        total = self.total_found
        shown = len(self.results)
        if total is not None:
            self.status_label.setText(f"{shown}/{total} mods cargados.")
        else:
            self.status_label.setText(f"{shown} mods cargados.")

    def build_internal_page_key(self, url, mod_id=""):
        if mod_id:
            return f"mod:{mod_id}"
        return f"url:{QUrl(url).toString()}"

    def build_internal_page_title(self, url, fallback=""):
        if fallback:
            return fallback
        parsed = QUrl(url)
        path = parsed.path().rstrip("/")
        if path.startswith("/user/"):
            return path.split("/")[-1] or "Usuario"
        if path.startswith("/mod/"):
            return path.split("/")[-1] or "Mod"
        if path in {"", "/"}:
            tag_values = self.query_values_from_url(url, "tag")
            if tag_values:
                return f"Tag: {tag_values[0]}"
            return "Explorar"
        return path.split("/")[-1] or parsed.host() or "Página"

    def open_internal_page(self, page_key, url, title="", mod_id=""):
        if not page_key or not url:
            return
        existing = self.web_tabs.get(page_key)
        if existing:
            index = self.tabs.indexOf(existing)
            if index >= 0:
                self.tabs.setCurrentIndex(index)
                if getattr(existing, "current_url", "") != url:
                    existing.current_url = url
                    existing.title = title or getattr(existing, "title", "") or self.build_internal_page_title(url)
                    self.tabs.setTabText(index, self._tab_label_for_title(existing.title))
                    self.load_mod_page_content(existing)
                return
            self.web_tabs.pop(page_key, None)

        tab = QWidget()
        tab.page_key = page_key
        tab.mod_id = mod_id
        tab.title = title or self.build_internal_page_title(url)
        tab.current_url = url

        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        loading_placeholder = self.build_page_loading_placeholder()
        layout.addWidget(loading_placeholder)
        tab.loading_placeholder = loading_placeholder

        web_view = QWebEngineView()
        web_view.setPage(ModWebPage(self, page_key, mod_id, web_view))
        web_view.page().setBackgroundColor(QColor("#201810"))
        web_view.loadFinished.connect(
            lambda ok, current_page_key=page_key: self.on_internal_page_loaded(current_page_key, ok)
        )
        web_view.setVisible(False)
        layout.addWidget(web_view)
        tab.web_view = web_view

        self.web_tabs[page_key] = tab
        index = self.tabs.addTab(tab, self._tab_label_for_title(tab.title))
        self.tabs.setCurrentIndex(index)
        self.update_add_button_state()
        if mod_id:
            self.load_modinfo(mod_id)
        self.load_mod_page_content(tab)

    def open_mod_tab(self, mod_id, url, title):
        if not mod_id or not url:
            return
        page_key = self.build_internal_page_key(url, mod_id=mod_id)
        self.open_internal_page(page_key, url, title=title or mod_id or "Mod", mod_id=mod_id)

    @staticmethod
    def _tab_label_for_title(title):
        text = (title or "Mod").strip()
        return text if len(text) <= 22 else f"{text[:19]}..."

    def close_tab(self, index):
        if index == self.browse_tab_index:
            return
        tab = self.tabs.widget(index)
        page_key = getattr(tab, "page_key", "")
        if page_key and self.web_tabs.get(page_key) is tab:
            self.web_tabs.pop(page_key, None)
        self.tabs.removeTab(index)
        tab.deleteLater()
        self.update_add_button_state()

    def on_tab_changed(self, index):
        if index == self.browse_tab_index:
            current = self.results_list.currentItem()
            self.current_mod_id = (current.data(Qt.UserRole) or {}).get("id", "") if current else ""
        self.update_add_button_state()

    def load_mod_page_content(self, tab):
        if self.game != "factorio":
            tab.web_view.load(QUrl(tab.current_url))
            tab.web_view.setVisible(True)
            if getattr(tab, "loading_placeholder", None) is not None:
                tab.loading_placeholder.hide()
            return
        cached_html = self.internal_page_cache.get(tab.current_url)
        if cached_html:
            tab.web_view.setHtml(cached_html, QUrl(tab.current_url))
            tab.web_view.setVisible(True)
            if getattr(tab, "loading_placeholder", None) is not None:
                tab.loading_placeholder.hide()
            return
        self.update_loading_placeholder(
            tab,
            message="Cargando página...",
            subtitle="Descargando y limpiando la página del portal antes de mostrarla.",
        )
        tab.web_view.setVisible(False)
        worker = FactorioPageWorker(getattr(tab, "page_key", tab.mod_id), tab.current_url, tab.title)
        worker.signals.finished.connect(self.on_factorio_page_ready)
        self.thread_pool.start(worker)

    def update_loading_placeholder(self, tab, message, subtitle):
        placeholder = getattr(tab, "loading_placeholder", None)
        if placeholder is None:
            return
        if hasattr(placeholder, "title_label"):
            placeholder.title_label.setText(message)
        if getattr(placeholder, "subtitle_label", None) is not None:
            placeholder.subtitle_label.setText(subtitle)
        placeholder.show()

    def on_factorio_page_ready(self, page_key, url, title, html, error):
        tab = self.web_tabs.get(page_key)
        if tab is None or getattr(tab, "current_url", "") != url:
            return
        if error or not html:
            self.update_loading_placeholder(
                tab,
                message="Cargando vista directa...",
                subtitle="No se pudo preparar la plantilla local, así que se abrirá la página original.",
            )
            tab.web_view.load(QUrl(url))
            return

        self.internal_page_cache[url] = html
        tab.web_view.setHtml(html, QUrl(url))

    def on_internal_page_loaded(self, page_key, ok):
        tab = self.web_tabs.get(page_key)
        if tab is None:
            return
        if not ok:
            self.update_loading_placeholder(
                tab,
                message="No se pudo cargar la página",
                subtitle="La vista embebida falló al renderizar este contenido.",
            )
            tab.web_view.setVisible(False)
            return
        tab.web_view.setVisible(True)
        if getattr(tab, "loading_placeholder", None) is not None:
            tab.loading_placeholder.hide()

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
            worker = self.create_search_worker(query=self.current_query, page=next_page)
        else:
            worker = self.create_search_worker(mode=self.current_mode, page=next_page)
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
        self.refresh_factorio_filter_url_preview(payload.get("request_url", "") if isinstance(payload, dict) else "")
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

    def prefetch_second_page(self):
        if self.last_page is None or self.current_page >= self.last_page:
            return
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

    def on_preload_image_ready(self, url, image):
        self.preload_inflight = False
        if not image.isNull():
            pixmap = QPixmap.fromImage(image).scaled(
                LIST_THUMBNAIL_SIZE,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            self.image_cache[url] = pixmap
            for widget in self.result_widgets.get(url, []):
                widget.set_thumbnail(pixmap)
        self.start_preload_thumbnails()

    def load_modinfo(self, mod_id):
        if not mod_id:
            return
        if mod_id in self.modinfo_cache:
            if self.game == "factorio" and self.pending_add_mod_id == mod_id:
                self.pending_add_mod_id = ""
                self.finish_add_factorio_to_cart(mod_id, self.modinfo_cache.get(mod_id) or {})
            return
        worker = self.create_info_worker(mod_id)
        worker.signals.finished.connect(self.on_modinfo_ready)
        self.thread_pool.start(worker)

    def on_modinfo_ready(self, mod_id, data, error):
        if error:
            if self.pending_add_mod_id == mod_id and self.game == "factorio":
                self.pending_add_mod_id = ""
                self.status_label.setText(f"No se pudo cargar el mod: {mod_id}")
            return
        if not data:
            return
        self.modinfo_cache[mod_id] = data
        if self.pending_add_mod_id == mod_id and self.game == "factorio":
            self.pending_add_mod_id = ""
            self.finish_add_factorio_to_cart(mod_id, data)

    def load_mod_versions(self, mod_id):
        if mod_id in self.version_cache:
            if self.pending_add_mod_id == mod_id:
                self.pending_add_mod_id = ""
                self.finish_add_modrinth_to_cart(mod_id, self.version_cache.get(mod_id) or [])
            return
        worker = ModrinthVersionsWorker(mod_id)
        worker.signals.finished.connect(self.on_mod_versions_ready)
        self.thread_pool.start(worker)

    def on_mod_versions_ready(self, mod_id, versions, error):
        if error:
            if self.pending_add_mod_id == mod_id:
                self.pending_add_mod_id = ""
                self.status_label.setText(f"No se pudieron cargar las versiones: {mod_id}")
            return
        self.version_cache[mod_id] = versions
        if self.pending_add_mod_id == mod_id:
            self.pending_add_mod_id = ""
            self.finish_add_modrinth_to_cart(mod_id, versions)

    def finish_add_factorio_to_cart(self, mod_id, info):
        latest = self.get_latest_release(info)
        if not latest:
            self.status_label.setText(f"Sin releases disponibles: {mod_id}")
            return
        version = latest.get("version")
        if not version:
            self.status_label.setText(f"Release invalida: {mod_id}")
            return

        added = self.add_cart_items([self.build_factorio_cart_item_data(mod_id, version)])
        if added:
            self.update_cart_button()
            self.status_label.setText(f"Agregado: {added[0]['title']}")
        else:
            self.status_label.setText("Este mod ya esta en el carrito.")

        deps = self.get_release_dependencies(latest)
        if not deps:
            return

        self.status_label.setText("Resolviendo dependencias...")
        worker = DependencyResolveWorker(self, deps, visited={mod_id})
        worker.signals.progress.connect(self.status_label.setText)
        worker.signals.finished.connect(self.on_dependencies_resolved)
        self.thread_pool.start(worker)

    def finish_add_modrinth_to_cart(self, mod_id, versions):
        options = self.build_modrinth_download_options(versions)
        if not options:
            self.status_label.setText(f"Sin archivos descargables: {mod_id}")
            return

        selected = self.select_modrinth_download_option(mod_id, options)
        if not selected:
            self.status_label.setText("Seleccion cancelada.")
            return

        added = self.add_cart_items([self.build_modrinth_cart_item_data(mod_id, selected)])
        if added:
            self.update_cart_button()
            self.status_label.setText(f"Agregado: {added[0]['title']}")
        else:
            self.status_label.setText("Este mod ya esta en el carrito.")

        dependencies = filter_required_modrinth_dependencies(selected.get("dependencies") or [])
        if not dependencies:
            return

        self.status_label.setText("Resolviendo dependencias...")
        worker = DependencyResolveWorker(self, dependencies, visited={mod_id})
        worker.signals.progress.connect(self.status_label.setText)
        worker.signals.finished.connect(self.on_dependencies_resolved)
        self.thread_pool.start(worker)

    def get_latest_release(self, data):
        releases = data.get("releases") or []
        if not releases:
            return None
        return max(releases, key=lambda release: release.get("released_at") or "")

    def get_release_dependencies(self, release):
        info_json = release.get("info_json") or {}
        deps = info_json.get("dependencies") or []
        return [dep for dep in deps if isinstance(dep, str)]

    def build_factorio_cart_item_data(self, mod_id, version):
        anticache = random.random()
        download_url = f"{DOWNLOAD_BASE}/{mod_id}/{version}.zip?anticache={anticache:.18f}"
        title = f"{mod_id}_{version}.zip"
        return {
            "title": title,
            "url": download_url,
            "mod_id": mod_id,
            "version": version,
            "source": "factorio",
        }

    def build_modrinth_download_options(self, versions):
        options = []
        for version in versions:
            normalized = normalize_modrinth_version_option(version)
            if not normalized:
                continue
            files = normalized.get("files") or []
            for index, file_info in enumerate(files):
                option = dict(normalized)
                option["filename"] = file_info.get("filename") or ""
                option["download_url"] = file_info.get("url") or ""
                option["is_primary_file"] = bool(file_info.get("primary")) or index == 0
                option["file"] = file_info
                option["project_id"] = version.get("project_id") or ""
                options.append(option)
        options.sort(
            key=lambda option: (
                option.get("published") or "",
                option.get("is_primary_file", False),
            ),
            reverse=True,
        )
        return options

    def select_modrinth_download_option(self, mod_id, options):
        if len(options) == 1:
            return options[0]
        dialog = VersionSelectDialog(self.project_name_for_id(mod_id), options, self)
        if dialog.exec_() != QDialog.Accepted:
            return None
        return dialog.selected_option

    def build_modrinth_cart_item_data(self, mod_id, option):
        version_id = option.get("id") or ""
        filename = option.get("filename") or f"{mod_id}.jar"
        return {
            "title": filename,
            "url": option.get("download_url") or "",
            "mod_id": mod_id,
            "version": version_id,
            "source": "modrinth",
            "filename": filename,
        }

    def add_cart_items(self, items):
        added = []
        for item in items:
            if not item or not item.get("url"):
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
        if self.game == "minecraft":
            return self.resolve_modrinth_dependencies(dependencies, visited=visited, progress_cb=progress_cb)
        return self.resolve_factorio_dependencies(dependencies, visited=visited, progress_cb=progress_cb)

    def resolve_factorio_dependencies(self, dependencies, visited=None, progress_cb=None):
        if visited is None:
            visited = set()
        resolved = []
        for dep in dependencies:
            dep_name, constraint = self.parse_dependency(dep)
            if not dep_name or dep_name in visited:
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
            resolved.append(self.build_factorio_cart_item_data(dep_name, version))
            nested = self.get_release_dependencies(release)
            if nested:
                resolved.extend(self.resolve_factorio_dependencies(nested, visited, progress_cb))
        return resolved

    def resolve_modrinth_dependencies(self, dependencies, visited=None, progress_cb=None):
        if visited is None:
            visited = set()
        resolved = []
        for dependency in dependencies:
            if not isinstance(dependency, dict):
                continue
            if (dependency.get("dependency_type") or "").lower() != "required":
                continue

            dep_project_id = dependency.get("project_id") or ""
            dep_version_id = dependency.get("version_id") or ""
            visit_key = dep_project_id or dep_version_id
            if not visit_key or visit_key in visited:
                continue
            visited.add(visit_key)

            if progress_cb:
                progress_cb(f"Resolviendo dependencia: {visit_key}")

            option = None
            if dep_version_id:
                option = self.fetch_modrinth_option_for_version(dep_version_id)
            if not option and dep_project_id:
                option = self.fetch_modrinth_best_option(dep_project_id)
            if not option:
                continue

            project_id = option.get("project_id") or dep_project_id or dep_version_id
            if project_id and project_id not in visited:
                visited.add(project_id)
            resolved.append(self.build_modrinth_cart_item_data(project_id or visit_key, option))

            nested = filter_required_modrinth_dependencies(option.get("dependencies") or [])
            if nested:
                resolved.extend(self.resolve_modrinth_dependencies(nested, visited, progress_cb))
        return resolved

    def parse_dependency(self, dep):
        dep = dep.strip()
        if not dep:
            return None, None
        if dep.startswith(("?", "!", "~")):
            if dep.startswith(("?", "!")):
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
        if self.game == "minecraft":
            return self.fetch_modrinth_project(mod_id)
        if mod_id in self.modinfo_cache:
            return self.modinfo_cache.get(mod_id)
        try:
            params = {"rand": f"{random.random():.18f}", "id": mod_id}
            response = requests.get(MODINFO_URL, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            if data:
                self.modinfo_cache[mod_id] = data
            return data
        except Exception:
            return None

    def fetch_modrinth_project(self, mod_id):
        if mod_id in self.modinfo_cache:
            return self.modinfo_cache.get(mod_id)
        try:
            response = requests.get(
                f"{MODRINTH_API_BASE}/project/{mod_id}",
                headers=modrinth_headers(),
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()
            if data:
                self.modinfo_cache[mod_id] = data
            return data
        except Exception:
            return None

    def fetch_modrinth_versions(self, mod_id):
        if mod_id in self.version_cache:
            return self.version_cache.get(mod_id) or []
        try:
            versions = fetch_modrinth_project_versions(mod_id)
            self.version_cache[mod_id] = versions
            return versions
        except Exception:
            return []

    def fetch_modrinth_best_option(self, mod_id):
        versions = self.fetch_modrinth_versions(mod_id)
        options = self.build_modrinth_download_options(versions)
        return options[0] if options else None

    def fetch_modrinth_option_for_version(self, version_id):
        try:
            version = fetch_modrinth_version(version_id)
        except Exception:
            return None
        option = normalize_modrinth_version_option(version)
        if not option:
            return None
        primary_file = option.get("primary_file") or {}
        option["filename"] = primary_file.get("filename") or ""
        option["download_url"] = primary_file.get("url") or ""
        option["file"] = primary_file
        option["project_id"] = version.get("project_id") or ""
        return option

    def select_release_for_constraint(self, info, constraint):
        releases = info.get("releases") or []
        if not releases:
            return None
        if not constraint:
            return self.get_latest_release(info)

        op, version = constraint
        target = self.parse_version(version)
        filtered = []
        for release in releases:
            release_version = release.get("version") or ""
            if release_version and self.compare_versions(self.parse_version(release_version), target, op):
                filtered.append(release)
        if not filtered:
            return None
        return max(filtered, key=lambda release: self.parse_version(release.get("version") or "0"))

    def parse_version(self, value):
        parts = re.split(r"[.\-+]", value.strip())
        numbers = []
        for part in parts:
            if part.isdigit():
                numbers.append(int(part))
            else:
                break
        return tuple(numbers) if numbers else (0,)

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
        dialog = CartDialog(self.cart_items[:], self)
        if dialog.exec_() != QDialog.Accepted:
            return
        self.cart_items = dialog.items
        self.update_cart_button()

        urls = [item["url"] for item in self.cart_items if item.get("url")]
        if not urls:
            return

        config = load_config()
        if self.game == "minecraft":
            mods_path = (
                config.get("minecraft_mods_path")
                or config.get("folder_path")
                or DEFAULT_MOD_PATHS["minecraft_mods_path"]
            )
        else:
            mods_path = (
                config.get("factorio_mods_path")
                or config.get("mods_folder_path")
                or DEFAULT_MOD_PATHS["factorio_mods_path"]
            )
        entries = [
            {
                "url": item["url"],
                "path": mods_path,
                "title": item.get("title") or item.get("filename") or item.get("mod_id") or "Mod",
            }
            for item in self.cart_items
            if item.get("url")
        ]
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json", encoding="utf-8") as handle:
            json.dump(entries, handle, indent=2)
            json_path = handle.name
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
            self.status_label.setText(f"Dependencias agregadas: {len(added)}")
        else:
            self.status_label.setText("Dependencias ya estaban en el carrito.")

    def project_name_for_id(self, mod_id):
        for item in self.results:
            if item.get("id") == mod_id:
                return item.get("name") or mod_id
        return mod_id

    def extract_mod_id_from_url(self, url):
        parsed = QUrl(url)
        host = parsed.host().lower()
        path = parsed.path().rstrip("/")
        if self.game == "minecraft":
            if host and host != MODRINTH_MOD_HOST:
                return ""
        else:
            if host and host != FACTORIO_MOD_HOST:
                return ""
        match = re.match(r"^/mod/([^/]+)$", path)
        return match.group(1) if match else ""
