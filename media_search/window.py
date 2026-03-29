import json, logging, os, re, subprocess, tempfile
import webbrowser
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QRadioButton , QButtonGroup, QLineEdit, QListWidget,
    QLabel, QListWidgetItem, QTextEdit, QPushButton,
    QMessageBox, QTreeWidget, QTreeWidgetItem, QFileDialog
)
from PyQt5.QtGui import QMovie, QPixmap
from PyQt5.QtCore import Qt, QTimer, QSize, QThreadPool, pyqtSignal
from media_search.anime_sources import search_aniteca, search_1337x, search_nyaa
from media_search.game_sources import (
    RAWG_API_KEY,
    search_elamigos,
    search_fitgirl,
    search_steamrip,
    warm_elamigos_cache,
    warm_steamrip_cache,
)
from config import DEFAULT_CONFIG, load_config, save_config
from media_search.dialogs import TrailerWindow
from media_search.sources import (
    normalize_trailer_url,
)
from media_search.workers import (
    GameSearchWorker, GameDetailsWorker, ImageLoaderWorker,
    AnimeSearchWorker, SiteSearchWorker, TrailerLaunchWorker, URLWorker,
    VisualNovelSearchWorker,
)

logger = logging.getLogger("media_search")

class MultiChoiceDownloader(QWidget):
    selection_ready = pyqtSignal(list)  
    def __init__(self, results_dict,title):
        super().__init__()
        self.setWindowTitle(title)
        self.setGeometry(300, 300, 600, 400)
        self.results_dict = results_dict
        self.selected_links = []
        self.thread_pool = QThreadPool()

        self.layout = QVBoxLayout()
        label = QLabel("Selecciona los enlaces para descargar")
        self.layout.addWidget(label)
        config = load_config()
        self.default_download_path = config.get("folder_path", DEFAULT_CONFIG["folder_path"])
        self.selected_download_path = self.default_download_path
        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("Guardar en:"))
        self.path_input = QLineEdit(self.default_download_path)
        self.path_browse_button = QPushButton("📁")
        self.path_browse_button.setFixedWidth(30)
        self.path_browse_button.clicked.connect(self.choose_download_path)
        path_row.addWidget(self.path_input)
        path_row.addWidget(self.path_browse_button)
        self.layout.addLayout(path_row)
        self.tree_widget = QTreeWidget()
        self.tree_widget.setHeaderHidden(True)
        self.tree_widget.itemChanged.connect(self.handle_item_changed)

        for source, results in results_dict.items():
            group_name=f"{source}"
            if source=="Nyaa" or source=="1337x":
                group_name+=f" - torrents"
            group_item = QTreeWidgetItem([group_name])
            group_item.setFlags(group_item.flags() & ~Qt.ItemIsSelectable)
            self.tree_widget.addTopLevelItem(group_item)

            aux={}
            for result in results:
                # Si los campos vienen vacíos, intento parsear el title plano
                if not any([result.get("fansub"), result.get("resolucion"), result.get("chapter")]):
                    parsed = self.parse_release_name(result["title"])
                    for k, v in parsed.items():
                        if v and not result.get(k):
                            result[k] = v

                item_text = ""
                subgroup_text = ""
                subgroup_item = None

                if result["url_type"] != "torrent":
                    item_text += f"[{result['url_type']}] "

                item_text += f"{result['title']}"

                fansub = result.get("fansub")
                resol = result.get("resolucion")
                chapter = result.get("chapter")
                chapters = result.get("chapters")

                if isinstance(chapter, (int, str)) and isinstance(chapters, (int)):
                    item_text += f" {chapter}/{chapters}"
                elif isinstance(chapter, (int, str)):
                    item_text += f" {chapter}"

                if isinstance(fansub, str):
                    subgroup_text += f" [{fansub}]"

                if isinstance(resol, int):
                    subgroup_text += f" ({resol}p)"

                explicit_group = result.get("group")
                if isinstance(explicit_group, str) and explicit_group.strip():
                    subgroup_text = explicit_group.strip()

                if subgroup_text and subgroup_text not in aux:
                    subgroup_item = QTreeWidgetItem([subgroup_text])
                    subgroup_item.setFlags(subgroup_item.flags() | Qt.ItemIsUserCheckable)
                    subgroup_item.setCheckState(0, Qt.Unchecked)
                    aux[subgroup_text] = subgroup_item
                    group_item.addChild(aux[subgroup_text])

                password = result.get("password")
                if isinstance(password, (int, str)):
                    item_text += f" - PASSWORD: {password}"

                child_item = QTreeWidgetItem([item_text])
                child_item.setFlags(child_item.flags() | Qt.ItemIsUserCheckable)
                child_item.setCheckState(0, Qt.Unchecked)
                child_item.setData(0, Qt.UserRole, (item_text, result["url"]))

                if subgroup_text in aux:
                    aux[subgroup_text].addChild(child_item)
                else:
                    group_item.addChild(child_item)

        self.layout.addWidget(self.tree_widget)
        self.btn_confirm = QPushButton("Descargar seleccionados")
        self.btn_confirm.clicked.connect(self.confirm_selection)
        self.layout.addWidget(self.btn_confirm)
        self.setLayout(self.layout)

    def choose_download_path(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "Seleccionar carpeta",
            self.path_input.text().strip() or self.default_download_path,
        )
        if folder:
            self.path_input.setText(folder)

    def ensure_download_path(self):
        folder_path = self.path_input.text().strip()
        if not folder_path:
            QMessageBox.warning(self, "Ruta faltante", "Elegí una carpeta de descarga.")
            return None
        if os.path.isdir(folder_path):
            return folder_path

        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Warning)
        msg.setWindowTitle("Carpeta no encontrada")
        msg.setText("La carpeta especificada no existe:\n\n" + folder_path)
        msg.setInformativeText("¿Deseas crearla?")
        create_btn = msg.addButton("Crear carpeta", QMessageBox.AcceptRole)
        msg.addButton("Cancelar", QMessageBox.RejectRole)
        msg.exec_()
        if msg.clickedButton() != create_btn:
            return None

        try:
            os.makedirs(folder_path, exist_ok=True)
        except Exception as e:
            QMessageBox.critical(self, "Error al crear carpeta", f"No se pudo crear la carpeta:\n{e}")
            return None
        return folder_path

    def parse_release_name(self,text):
        data = {
            "fansub": None,
            "title": None,
            "chapter": None,
            "resolucion": None,
            "extra": None
        }

        # Fansub: [Texto] al inicio
        fansub_match = re.match(r'^\[(.*?)\]', text)
        if fansub_match:
            data["fansub"] = fansub_match.group(1)
            text = text[fansub_match.end():].strip()

        # Capítulo: - 12 / - 09v2 / [12] / S01E12
        chapter_match = re.search(r'(?:-| )(S\d+E\d+|\d+v?\d*|\[\d+\])', text, re.IGNORECASE)
        if chapter_match:
            chap = chapter_match.group(1).strip(" -[]")
            data["chapter"] = chap
            # título sería lo que está antes
            data["title"] = text[:chapter_match.start()].strip(" -")

        # Resolución: 480p, 720p, 1080p
        resol_match = re.search(r'(\d{3,4}p)', text)
        if resol_match:
            try:
                data["resolucion"] = int(resol_match.group(1)[:-1])
            except ValueError:
                pass

        # Extra: WEB-DL, WEBRip, HEVC, AAC, MultiSub, etc.
        extras = re.findall(r'(WEB-?DL|WEBRip|HEVC|x264|AAC|MultiSub|VOSTFR|AMZN|CR)', text, re.IGNORECASE)
        if extras:
            data["extra"] = " ".join(extras)

        # Si no detectamos título aún, lo dejamos como todo
        if not data["title"]:
            data["title"] = text

        return data
    
    def handle_item_changed(self, item, column):
        if item.childCount() > 0:
            state = item.checkState(0)
            for i in range(item.childCount()):
                child = item.child(i)
                child.setCheckState(0, state)

    def confirm_selection(self):
        self.selected_download_path = self.ensure_download_path()
        if not self.selected_download_path:
            return

        self.selected_links = []
        self.pending = 0
        self.results_temp = []
        top_level_count = self.tree_widget.topLevelItemCount()
        index = 0
        for i in range(top_level_count):
            group_item = self.tree_widget.topLevelItem(i)
            for j in range(group_item.childCount()):
                child = group_item.child(j)

                if child.childCount() > 0:
                    for k in range(child.childCount()):
                        grandchild = child.child(k)
                        if grandchild.checkState(0) == Qt.Checked:
                            self.pending += self.procesar_item_si_valido(grandchild, index)
                            index += 1
                else:
                    if child.checkState(0) == Qt.Checked:
                        self.pending += self.procesar_item_si_valido(child, index)
                        index += 1
        if self.pending == 0:
            print(self.results_temp)
            print(self.selected_links)
            QMessageBox.warning(self, "Nada seleccionado", "Por favor selecciona al menos un enlace.")

    def procesar_item_si_valido(self, item, index):
                data = item.data(0, Qt.UserRole)
                if data is not None:
                    title, url = data
                    self.results_temp.append((index, title, None))
                    worker = URLWorker(index, title, url)
                    worker.signals.finished.connect(self.on_link_ready)
                    self.thread_pool.start(worker)
                    return 1
                return 0

    def on_link_ready(self, index, title, link):
        if link:
            self.results_temp[index] = (index, title, link)
        else:
            self.results_temp[index] = None

        self.pending -= 1
        if self.pending == 0:
            self.show_results()

    def show_results(self):
        selected_links = [
            (title, link) for _, title, link in sorted(filter(None, self.results_temp))
        ]
        if selected_links:
            self.selected_links = [
                {"url": link, "path": self.selected_download_path}
                for _, link in selected_links
            ]
            config = load_config()
            if config.get("folder_path") != self.selected_download_path:
                config["folder_path"] = self.selected_download_path
                save_config(config)
            links_str = "\n\n".join(
                f"{title}\n{link}"
                for title, link in selected_links
            )
            QMessageBox.information(self, "Links seleccionados", links_str)
            self.selection_ready.emit(self.selected_links)

        else:
            QMessageBox.warning(self, "Error", "No se pudieron obtener los enlaces.")

# --- UI principal ---
class MediaSearchUI(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Buscador de medios")
        self.resize(800, 500)
        layout = QVBoxLayout()
        self.spinner_movie = QMovie("spinner.gif")
        self.spinner_movie.setScaledSize(QSize(20, 20)) 

        types_layout = QHBoxLayout()
        category_group = QButtonGroup()
        self.gen_rbutton = QRadioButton("General")
        self.anime_rbutton = QRadioButton("Anime")
        self.manga_rbutton = QRadioButton("Manga")
        self.vn_rbutton = QRadioButton("Visual Novel")
        self.games_rbutton = QRadioButton("Games")
        self.radio_map = [
            (self.gen_rbutton, "general"),
            (self.anime_rbutton, "anime"),
            (self.manga_rbutton, "manga"),
            (self.vn_rbutton, "VN"),
            (self.games_rbutton, "games"),
        ]
        for rbutton, category_value in self.radio_map:
            category_group.addButton(rbutton)
            rbutton.setStyleSheet("font-weight: bold;")
            rbutton.toggled.connect(lambda checked,
                value=category_value: self.set_category(value) if checked else None
            )
            rbutton.setEnabled(True)
            types_layout.addWidget(rbutton)
        self.games_rbutton.toggle()
        self.category = "games"
        layout.addLayout(types_layout)

        search_layout = QHBoxLayout()
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Buscar...")
        self.search_bar.returnPressed.connect(self.perform_search)
        self.search_button = QPushButton("🔎")
        self.search_button.setFixedSize(24, 24)
        self.search_button.clicked.connect(self.perform_search)
        self.spinner_search_bar = QLabel()
        self.spinner_search_bar.setMovie(self.spinner_movie)
        self.spinner_search_bar.setFixedSize(24, 24)
        self.spinner_search_bar.setAlignment(Qt.AlignCenter)
        self.spinner_search_bar.setVisible(False)
        search_layout.addWidget(self.search_bar)
        search_layout.addWidget(self.search_button)
        search_layout.addWidget(self.spinner_search_bar)
        layout.addLayout(search_layout)

        self.results_list = QListWidget()
        self.results_list.setIconSize(QSize(80, 120))
        self.results_list.itemClicked.connect(self.show_details)
        self.results_list.currentItemChanged.connect(self.on_current_item_changed)
        self.results_list.verticalScrollBar().valueChanged.connect(self.on_scroll)
        layout.addWidget(self.results_list)

        details_layout = QHBoxLayout()
        self.image_label = QLabel()
        self.image_label.setFixedSize(150, 212)
        self.image_label.setScaledContents(True)
        details_layout.addWidget(self.image_label)

        self.details = QTextEdit()
        self.details.setReadOnly(True)

        buttons = QHBoxLayout()
        self.trailer_button = QPushButton("Ver Trailer")
        self.trailer_button.setFixedWidth(75)
        self.trailer_button.clicked.connect(self.show_trailer)
        self.trailer_button.setEnabled(False)
        self.download_button = QPushButton("Descargar")
        self.download_button.clicked.connect(self.download_item)
        self.download_button.setEnabled(False)
        self.mods_button = QPushButton("Ver mods")
        self.mods_button.clicked.connect(self.open_mods)
        self.mods_button.setEnabled(False)
        self.spinner_details = QLabel()
        self.spinner_details.setMovie(self.spinner_movie)
        self.spinner_details.setFixedSize(24, 24)
        self.spinner_details.setAlignment(Qt.AlignCenter)
        self.spinner_details.setVisible(False)
        buttons.addWidget(self.trailer_button)
        buttons.addWidget(self.download_button)
        buttons.addWidget(self.mods_button)
        buttons.addWidget(self.spinner_details)

        details_center = QVBoxLayout()
        # Descripción
        details_inner_layout = QHBoxLayout()
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        details_inner_layout.addWidget(self.details)
        # Campos adicionales
        details_right = QVBoxLayout()
        self.labels_info = []
        for _ in range(4):
            label = QLabel()
            label.setStyleSheet("font-weight: bold;")
            label.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
            details_right.addWidget(label)
            self.labels_info.append(label)
        details_inner_layout.addLayout(details_right)
        details_center.addLayout(details_inner_layout)
        details_center.addLayout(buttons)

        details_layout.addLayout(details_center)
        layout.addLayout(details_layout)
        self.download_label = QLabel()
        self.active_downloads = set()
        self.selector_windows = {}
        self.total_links_found = {}
        layout.addWidget(self.download_label)
        self.setLayout(layout)
        self.current_item = None
        self.trailer_request_id = None
        self.current_query = ""
        self.current_page = 1
        self.last_page = None
        self.total_found = None
        self.is_loading = False
        self.pending_load = False
        self.image_cache = {}
        self.current_image_url = ""
        self.preload_queue = []
        self.preload_inflight = False
        self.game_details_cache = {}
        self.game_details_inflight = set()
        self.game_details_queue = []
        self.search_placeholders = {
            "general": "Buscar...",
            "anime": "Buscar anime...",
            "manga": "Buscar manga...",
            "VN": "Buscar visual novels...",
            "games": "Buscar juegos...",
        }
        self.update_search_placeholder()
        QTimer.singleShot(0, self.preload_download_sources)

    def set_category(self, value):
        self.category = value
        if hasattr(self, "mods_button"):
            self.mods_button.setVisible(self.category == "games")
            if self.category != "games":
                self.mods_button.setEnabled(False)
        self.update_search_placeholder()
        print("Categoría seleccionada:", self.category)

    def update_search_placeholder(self):
        if hasattr(self, "search_bar") and not self.search_bar.isEnabled():
            self.search_bar.setPlaceholderText("Buscando...")
            return
        placeholder = getattr(self, "search_placeholders", {}).get(self.category, "Buscar...")
        if hasattr(self, "search_bar"):
            self.search_bar.setPlaceholderText(placeholder)

    def preload_download_sources(self):
        def warmup(_query):
            warm_elamigos_cache()
            warm_steamrip_cache()
            return []

        worker = SiteSearchWorker("_startup_elamigos_cache", warmup, "")
        QThreadPool.globalInstance().start(worker)

    def perform_search(self):
        term = self.search_bar.text().strip()
        if not term:
            return
        logger.info("UI perform_search: category=%s query=%r", self.category, term)

        self.current_query = term
        self.current_page = 1
        self.last_page = None
        self.total_found = None
        self.pending_load = False
        self.preload_queue = []
        self.preload_inflight = False
        self.game_details_queue = []
        self.results_list.clear()
        self.details.clear()
        self.image_label.clear()
        self.download_button.setEnabled(False)
        self.mods_button.setEnabled(False)
        self.current_item = None

        self.search_button.setHidden(True)
        self.spinner_search_bar.show()
        self.spinner_movie.start()
        self.search_bar.setDisabled(True)
        self.update_search_placeholder()
        self.is_loading = True

        self.start_search(page=1, append=False)

    def start_search(self, page, append):
        logger.info("UI start_search: category=%s query=%r page=%s append=%s", self.category, self.current_query, page, append)
        pool = QThreadPool.globalInstance()
        if self.category == "games":
            worker = GameSearchWorker(self.current_query, RAWG_API_KEY, page=page)
        elif self.category in ("anime", "manga"):
            worker = AnimeSearchWorker(self.current_query, self.category, page=page)
        elif self.category == "VN":
            worker = VisualNovelSearchWorker(self.current_query, page=page)
        else:
            self.finish_search({"items": [], "page": page, "last_page": page, "total": 0}, append)
            return

        worker.signals.finished.connect(lambda payload, append=append: self.finish_search(payload, append))
        pool.start(worker)

    def finish_search(self, payload, append):
        logger.info(
            "UI finish_search: category=%s query=%r page=%s append=%s items=%s total=%s",
            self.category,
            self.current_query,
            payload.get("page") if isinstance(payload, dict) else None,
            append,
            len(payload.get("items", [])) if isinstance(payload, dict) else 0,
            payload.get("total") if isinstance(payload, dict) else None,
        )
        self.search_bar.setDisabled(False)
        self.update_search_placeholder()
        self.spinner_movie.stop()
        self.spinner_search_bar.setHidden(True)
        self.search_button.show()
        self.is_loading = False
        self.pending_load = False

        items = payload.get("items", []) if isinstance(payload, dict) else []
        self.current_page = payload.get("page", self.current_page) if isinstance(payload, dict) else self.current_page
        self.last_page = payload.get("last_page", self.last_page) if isinstance(payload, dict) else self.last_page
        self.total_found = payload.get("total", self.total_found) if isinstance(payload, dict) else self.total_found

        if not append:
            self.results_list.clear()

        existing_keys = set()
        if append:
            for i in range(self.results_list.count()):
                data = self.results_list.item(i).data(Qt.UserRole) or {}
                existing_keys.add((data.get("source"), data.get("id"), data.get("url"), data.get("title")))

        for item in items:
            item_key = (item.get("source"), item.get("id"), item.get("url"), item.get("title"))
            if item_key in existing_keys:
                continue
            existing_keys.add(item_key)
            self.add_result_item(item)

        if self.results_list.count() > 0 and not self.results_list.currentItem():
            self.results_list.setCurrentRow(0)

        self.start_preload_images()
        self.start_preload_game_details()

    def add_result_item(self, item):
        logger.debug("UI add_result_item: source=%s id=%s title=%r", item.get("source"), item.get("id"), item.get("title"))
        source = item.get('source', '')
        if source == "RAWG":
            lw_item = QListWidgetItem(f"[{source}] {item['title']}")
        elif source == "MyAnimeList":
            lw_item = QListWidgetItem(f"[{source}] - {item['title']}")
        elif source == "VNDB":
            lw_item = QListWidgetItem(f"[{source}] {item['title']}")
        else:
            lw_item = QListWidgetItem(item['title'])

        lw_item.setData(Qt.UserRole, item)
        self.results_list.addItem(lw_item)
        image_url = item.get("image")
        if image_url and image_url not in self.image_cache:
            self.preload_queue.append(image_url)
        if item.get("source") == "RAWG":
            game_id = item.get("id")
            if game_id and game_id not in self.game_details_cache and game_id not in self.game_details_inflight:
                self.game_details_queue.append(game_id)

    def show_details(self, item):
        def score_to_color(score):
            try:
                s = float(score)
            except:
                return f"<span style='color:#000'>{score}</span>"
            s = max(0.0, min(s, 10.0))
            r = int(255 * (1 - s / 10))
            g = int(255 * (s / 10))
            return f"<span style='color:rgb({r},{g},0)'>{score}</span>"

        RATING_TEXT = {
            "G": " G<br>All Ages",
            "PG": " PG<br>Children",
            "PG-13": "<br>PG-13<br>Teens 13<br>or older",
            "R": " R<br>17+ (violence<br>and profanity)",
            "R+": " R+<br>Mild Nudity",
            "Rx": "<br>Rx<br>Hentai"
        }

        self.trailer_button.setEnabled(False)
        data = item.data(Qt.UserRole)
        self.current_item = item
        self.current_image_url = data.get("image") or ""
        logger.info(
            "UI show_details: source=%s id=%s title=%r image=%s",
            data.get("source"),
            data.get("id"),
            data.get("title"),
            bool(data.get("image")),
        )

        desc = data.get("description", "Sin descripción.")
        self.details.setPlainText(desc)
        source = data.get("source", "")

        if source=="MyAnimeList":
            self.labels_info[0].setText(f"<b>Tipo:<br>{data.get('type', '')}</b>")
            self.labels_info[1].setText(f"<b>Episodios:<br>{data.get('episodes', '')}</b>")
            self.labels_info[2].setText(f"<b>Score:<br>{score_to_color(data.get('score', ''))}</b>")
            rating_text = RATING_TEXT.get(data.get("rating", ""), "<br>No rating")
            self.labels_info[3].setText(f"<b>Rating:{rating_text}</b>")
            self.spinner_movie.start()
            self.image_label.setMovie(self.spinner_movie)

            if data.get("image"):
                self.load_detail_image(data["image"])
            else:
                self.spinner_movie.stop()
                self.image_label.clear()
            trailer = data.get("trailer")
            if trailer:
                self.trailer_button.setEnabled(True)
                data["trailer"] = trailer
                self.current_item.setData(Qt.UserRole, data)
            self.download_button.setEnabled(True)
            self.download_button.setText("Descargar")
            self.mods_button.setEnabled(False)

        if source == "RAWG":
            self.labels_info[0].setText(f"<b>Plataformas:<br>{', '.join(data.get('platforms', []))}</b>")
            self.labels_info[1].setText(f"<b>Géneros:<br>{', '.join(data.get('genres', []))}</b>")
            self.labels_info[2].setText(f"<b>Fecha lanzamiento:<br>{data.get('released', 'N/A')}</b>")
            self.labels_info[3].setText(f"<b>Rating:<br>{data.get('rating', 'N/A')}</b>")
            self.spinner_movie.start()
            self.image_label.setMovie(self.spinner_movie)

            if data.get("image"):
                self.load_detail_image(data["image"])
            else:
                self.spinner_movie.stop()
                self.image_label.clear()

            game_id = data.get("id")
            if game_id:
                cached_details = self.game_details_cache.get(game_id)
                if cached_details:
                    self.apply_game_details_to_current(
                        game_id,
                        cached_details.get("description"),
                        cached_details.get("trailer"),
                    )
                else:
                    self.details.setPlainText("Cargando descripción...")
                    self.queue_game_details(game_id, priority=True)

            self.download_button.setText("Descargar")
            self.download_button.setEnabled(True)
            if "Factorio" in data.get("title", ""):
                self.mods_button.setEnabled(True)
            else:
                self.mods_button.setEnabled(False)

        if source == "VNDB":
            languages = ", ".join(data.get("languages", [])) or "N/A"
            platforms = ", ".join(data.get("platforms", [])) or "N/A"
            released = data.get("released", "N/A")
            rating = data.get("rating", "N/A") or "N/A"
            self.labels_info[0].setText(f"<b>Idiomas:<br>{languages}</b>")
            self.labels_info[1].setText(f"<b>Plataformas:<br>{platforms}</b>")
            self.labels_info[2].setText(f"<b>Lanzamiento:<br>{released}</b>")
            self.labels_info[3].setText(f"<b>Rating:<br>{score_to_color(rating)}</b>")
            self.spinner_movie.start()
            self.image_label.setMovie(self.spinner_movie)

            if data.get("image"):
                self.load_detail_image(data["image"])
            else:
                self.spinner_movie.stop()
                self.image_label.clear()

            extras = []
            if data.get("developers"):
                extras.append("Desarrolladores: " + ", ".join(data.get("developers", [])))
            if data.get("length_label"):
                extras.append("Duración: " + data.get("length_label"))
            if data.get("other_titles"):
                extras.append("Título alternativo: " + ", ".join(data.get("other_titles", [])))
            if extras:
                self.details.setPlainText(desc + "\n\n" + "\n".join(extras))

            self.trailer_button.setEnabled(False)
            self.download_button.setText("Descargar")
            self.download_button.setEnabled(True)
            self.mods_button.setEnabled(False)

    def load_detail_image(self, image_url):
        self.current_image_url = image_url or ""
        if not image_url:
            logger.debug("UI load_detail_image: empty image URL")
            self.spinner_movie.stop()
            self.image_label.clear()
            return
        if image_url in self.image_cache:
            logger.debug("UI load_detail_image: cache hit for %s", image_url)
            self.spinner_movie.stop()
            self.image_label.setPixmap(self.image_cache[image_url])
            return
        logger.debug("UI load_detail_image: queue worker for %s", image_url)
        worker = ImageLoaderWorker(image_url)
        worker.signals.finished.connect(self.set_detail_image)
        QThreadPool.globalInstance().start(worker)

    def set_detail_image(self, url, image):
        logger.debug("UI set_detail_image: url=%s current=%s valid=%s", url, self.current_image_url, bool(image and not image.isNull()))
        if url != self.current_image_url:
            return
        self.spinner_movie.stop()
        if image and not image.isNull():
            pixmap = QPixmap.fromImage(image)
            scaled = pixmap.scaled(
                self.image_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self.image_cache[url] = scaled
            self.image_label.setPixmap(scaled)
        else:
            self.image_label.clear()

    def apply_game_details(self, game_id, description, trailer_url):
        logger.debug("UI apply_game_details: game_id=%s trailer=%s", game_id, bool(trailer_url))
        self.game_details_inflight.discard(game_id)
        self.game_details_cache[game_id] = {
            "description": description or "Sin descripción.",
            "trailer": trailer_url or None,
        }
        self.apply_game_details_to_list(game_id, description, trailer_url)
        self.apply_game_details_to_current(game_id, description, trailer_url)
        self.start_preload_game_details()

    def apply_game_details_to_list(self, game_id, description, trailer_url):
        for index in range(self.results_list.count()):
            item = self.results_list.item(index)
            data = item.data(Qt.UserRole) or {}
            if data.get("source") != "RAWG" or data.get("id") != game_id:
                continue
            data["description"] = description or "Sin descripción."
            data["trailer"] = trailer_url or None
            item.setData(Qt.UserRole, data)

    def apply_game_details_to_current(self, game_id, description, trailer_url):
        if not self.current_item:
            return

        data = self.current_item.data(Qt.UserRole) or {}
        if data.get("source") != "RAWG" or data.get("id") != game_id:
            return

        data["description"] = description or "Sin descripción."
        data["trailer"] = trailer_url or None
        self.current_item.setData(Qt.UserRole, data)
        self.details.setPlainText(data["description"])
        self.trailer_button.setEnabled(bool(data["trailer"]))

    def handle_trailer_browser_fallback(self, request_id, trailer_url):
        if request_id != self.trailer_request_id:
            return
        webbrowser.open(trailer_url)

    def handle_trailer_finished(self, request_id):
        if request_id != self.trailer_request_id:
            return
        self.trailer_request_id = None
        if self.current_item:
            trailer_url = normalize_trailer_url((self.current_item.data(Qt.UserRole) or {}).get("trailer"))
            self.trailer_button.setEnabled(bool(trailer_url))
        else:
            self.trailer_button.setEnabled(False)

    def show_trailer(self):
        if not self.current_item:
            return

        self.trailer_button.setEnabled(False)
        trailer_url = normalize_trailer_url((self.current_item.data(Qt.UserRole) or {}).get("trailer"))
        title = (self.current_item.data(Qt.UserRole) or {}).get("title", "").strip()
        window_title = f"Trailer de {title}" if title else "Trailer"
        if not trailer_url:
            self.trailer_button.setEnabled(True)
            return

        if trailer_url.lower().endswith(".mp4") or any(
            host in trailer_url for host in ("youtube.com", "youtu.be", "youtube-nocookie.com")
        ):
            request_id = f"{id(self.current_item)}:{trailer_url}"
            self.trailer_request_id = request_id
            worker = TrailerLaunchWorker(request_id, trailer_url, window_title)
            worker.signals.browser_fallback.connect(self.handle_trailer_browser_fallback)
            worker.signals.finished.connect(self.handle_trailer_finished)
            QThreadPool.globalInstance().start(worker)
            return

        self.trailer_window = TrailerWindow(trailer_url, self)
        self.trailer_window.show()
        self.trailer_button.setEnabled(True)

    def on_current_item_changed(self, current, _previous):
        if current:
            logger.debug("UI current item changed")
            self.show_details(current)

    def on_scroll(self, value):
        if self.is_loading or self.pending_load:
            return
        if self.last_page is not None and self.current_page >= self.last_page:
            return
        bar = self.results_list.verticalScrollBar()
        maximum = bar.maximum()
        if maximum <= 0:
            return
        if value / maximum < 0.8:
            return
        self.pending_load = True
        self.is_loading = True
        logger.info("UI on_scroll pagination trigger: query=%r next_page=%s", self.current_query, self.current_page + 1)
        self.spinner_search_bar.show()
        self.spinner_movie.start()
        self.search_button.setHidden(True)
        self.start_search(page=self.current_page + 1, append=True)

    def start_preload_images(self):
        if self.preload_inflight:
            return
        while self.preload_queue:
            image_url = self.preload_queue.pop(0)
            if image_url in self.image_cache:
                continue
            self.preload_inflight = True
            logger.debug("UI start_preload_images: image=%s remaining=%s", image_url, len(self.preload_queue))
            worker = ImageLoaderWorker(image_url)
            worker.signals.finished.connect(self.on_preload_image_ready)
            QThreadPool.globalInstance().start(worker)
            break

    def on_preload_image_ready(self, url, image):
        logger.debug("UI on_preload_image_ready: url=%s valid=%s", url, bool(image and not image.isNull()))
        self.preload_inflight = False
        if image and not image.isNull() and url not in self.image_cache:
            pixmap = QPixmap.fromImage(image)
            self.image_cache[url] = pixmap.scaled(
                self.image_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
        self.start_preload_images()

    def queue_game_details(self, game_id, priority=False):
        if not game_id or game_id in self.game_details_cache or game_id in self.game_details_inflight:
            return
        logger.debug("UI queue_game_details: game_id=%s priority=%s", game_id, priority)
        if priority:
            self.game_details_queue = [queued for queued in self.game_details_queue if queued != game_id]
            self.game_details_queue.insert(0, game_id)
        elif game_id not in self.game_details_queue:
            self.game_details_queue.append(game_id)
        self.start_preload_game_details()

    def start_preload_game_details(self):
        while self.game_details_queue:
            game_id = self.game_details_queue.pop(0)
            if game_id in self.game_details_cache or game_id in self.game_details_inflight:
                continue
            self.game_details_inflight.add(game_id)
            logger.debug("UI start_preload_game_details: game_id=%s remaining=%s", game_id, len(self.game_details_queue))
            worker = GameDetailsWorker(game_id, RAWG_API_KEY)
            worker.signals.finished.connect(self.apply_game_details)
            QThreadPool.globalInstance().start(worker)
            break

    def download_item(self):
        if not self.current_item:
            return
        title = re.sub(r"\s*\([^)]*\)\s*$", "", self.current_item.data(Qt.UserRole)['title']).strip()
        logger.info("UI download_item: source=%s title=%r", (self.current_item.data(Qt.UserRole) or {}).get("source"), title)
        if title in self.active_downloads:
            print(f"⏳ Ya se está buscando: {title}")
            return
        self.active_downloads.add(title)

        print(f"📥 Buscar para descarga: {title}")
        self.spinner_details.show()
        self.spinner_movie.start()
        self.download_button.setEnabled(False)
        self.download_button.setText(f"Buscando y cargando enlaces...")
        
        self.results_dict = {}
        self.total_links_found[title] = 0
        self.update_download_label()

        def update_results(site_name, results):
            if results:
                sorted_results = sorted(
                    results,
                    key=lambda x: (
                        x.get("title").lower(),
                        x.get("url_type").lower(),
                        (x.get("fansub") or "").lower(),
                        int(x.get("resolucion") or 0),
                        int(x.get("chapter") or 0)
                    )
                )
                self.results_dict[site_name] = sorted_results
                self.total_links_found[title] += len(sorted_results)

            self.pending_sites.discard(site_name)
            self.update_download_label()

            if not self.pending_sites:
                QTimer.singleShot(5000, lambda: self.remove_download_entry(title)) 
                self.spinner_movie.stop()
                self.spinner_details.setVisible(False)
                self.active_downloads.discard(title)

                if self.results_dict:
                    selector_window = MultiChoiceDownloader(self.results_dict, title)
                    self.selector_windows[title] = selector_window
                    selector_window.show()

                    def handle_selection(entries):
                        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json", encoding="utf-8") as f:
                            json.dump(entries, f, indent=2, ensure_ascii=False)
                            json_path = f.name
                        subprocess.Popen(["python", "download_manager.py", json_path])
                        selector_window.close()
                        del self.selector_windows[title]

                    selector_window.selection_ready.connect(handle_selection)
                else:
                    QMessageBox.information(self, "Sin resultados", f"No se encontraron descargas para: {title}")

        pool = QThreadPool.globalInstance()
        sources = [("Aniteca", search_aniteca), ("Nyaa", search_nyaa), ("1337x", search_1337x)]
        current_source = (self.current_item.data(Qt.UserRole) or {}).get("source", "")
        if current_source == "RAWG":
            sources = [("ElAmigos", search_elamigos), ("FitGirl", search_fitgirl), ("SteamRIP", search_steamrip)]
        elif current_source == "VNDB":
            sources = [
                ("Nyaa", search_nyaa),
                ("1337x", search_1337x),
                ("ElAmigos", search_elamigos),
                ("FitGirl", search_fitgirl),
                ("SteamRIP", search_steamrip),
            ]
        self.pending_sites = {name for name, _ in sources}

        for name, func in sources:
            worker = SiteSearchWorker(name, func, title)
            worker.signals.result_ready.connect(update_results)
            pool.start(worker)

    def open_mods(self):
        if not self.current_item:
            return
        data = self.current_item.data(Qt.UserRole) or {}
        if data.get("source") != "RAWG":
            return
        title = data.get("title", "")
        if "Factorio" not in title:
            return
        subprocess.Popen(["python", "mod_search.py", "--game", "factorio"])

    def update_download_label(self):
        summary = []
        for title, count in self.total_links_found.items():
            summary.append(f"Cargando: {count} enlaces encontrados - {title}")
        self.download_label.setText("\n".join(summary))

    def remove_download_entry(self, title):
        if title in self.total_links_found:
            del self.total_links_found[title]
            self.update_download_label()

