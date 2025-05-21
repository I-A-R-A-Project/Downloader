import re, subprocess, sys, requests
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLineEdit, QListWidget,
    QLabel, QListWidgetItem, QTextEdit, QPushButton, QHBoxLayout,
    QMessageBox, QDialog,QTreeWidget,QTreeWidgetItem
)
from PyQt5.QtGui import QMovie, QKeyEvent
from PyQt5.QtCore import Qt, QTimer, QSize, QThreadPool, pyqtSignal
from aniteca import search_aniteca
from bs4 import BeautifulSoup
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEnginePage
from workers import FullDetailsWorker, ImageLoaderWorker, SearchWorker, SiteSearchWorker, URLWorker

TMDB_API_KEY = 'TU_API_KEY_AQUI'
def search_tmdb(query):
    url = f'https://api.themoviedb.org/3/search/multi'
    params = {
        'api_key': TMDB_API_KEY,
        'query': query,
        'language': 'es-ES',
        'include_adult': False
    }
    res = requests.get(url, params=params)
    items = res.json().get("results", [])
    return [{
        "source": "TMDb",
        "title": i.get("title") or i.get("name"),
        "year": (i.get("release_date") or i.get("first_air_date") or "")[:4],
        "type": i.get("media_type"),
        "description": i.get("overview", ""),
        "image": f"https://image.tmdb.org/t/p/w185{i['poster_path']}" if i.get("poster_path") else None
    } for i in items]

def search_nyaa(query):
    results = []
    try:
        url = f"https://nyaa.si/?f=0&c=1_0&q={query.replace(' ', '+')}&s=seeders&o=desc"
        r = requests.get(url, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        rows = soup.select("tr.default")

        for row in rows[:100]:
            title_tag = row.select_one("td:nth-child(2) a[href*='view']")
            magnet_tag = row.select_one("td.text-center a[href^='magnet:?']")

            if title_tag and magnet_tag:
                title = title_tag.text.strip()
                magnet_url = magnet_tag["href"]

                results.append({
                    "title": title,
                    "chapter": None,
                    "chapters": None,
                    "url_type": "magnet",
                    "url": magnet_url,
                    "resolucion": None,
                    "idioma": None,
                    "subtitulo": None,
                    "fansub": None,
                    "format": None,
                    "password": None
                })

    except Exception as e:
        print(f"[Nyaa] Error: {e}")
    return results

def search_1337x(query):
    results = []
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        url = f"https://1337x.to/search/{query.replace(' ', '%20')}/1/"
        r = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        entries = soup.select("td.coll-1.name")

        for entry in entries[:100]:
            link = entry.select_one("a:nth-of-type(2)")
            if not link:
                continue

            title = link.text.strip()
            detail_url = "https://1337x.to" + link["href"]

            # Ir a la página de detalles y buscar el magnet
            try:
                detail_r = requests.get(detail_url, headers=headers, timeout=10)
                detail_soup = BeautifulSoup(detail_r.text, "html.parser")
                magnet_tag = detail_soup.select_one("a[href^='magnet:?']")

                if not magnet_tag:
                    continue

                magnet_url = magnet_tag["href"]

                results.append({
                    "title": title,
                    "chapter": None,
                    "chapters": None,
                    "url_type": "magnet",
                    "url": magnet_url,
                    "resolucion": None,
                    "idioma": None,
                    "subtitulo": None,
                    "fansub": None,
                    "format": None,
                    "password": None
                })
            except Exception as e:
                print(f"[1337x detail] Error: {e}")

    except Exception as e:
        print(f"[1337x] Error: {e}")
    return results

class MultiChoiceDownloader(QWidget):
    selection_ready = pyqtSignal(list)  
    def __init__(self, results_dict):
        super().__init__()
        self.setWindowTitle("Selecciona los enlaces para descargar")
        self.setGeometry(300, 300, 600, 400)
        self.results_dict = results_dict
        self.selected_links = []
        self.thread_pool = QThreadPool()

        self.layout = QVBoxLayout()
        self.tree_widget = QTreeWidget()
        self.tree_widget.setHeaderHidden(True)

        for source, results in results_dict.items():
            group_item = QTreeWidgetItem([f"{source}"])
            group_item.setFlags(group_item.flags() & ~Qt.ItemIsSelectable)  # No seleccionable
            self.tree_widget.addTopLevelItem(group_item)

            for result in results:
                item_text = f"{result['url_type']} - {result['title']}"
                fansub = result["fansub"]
                if isinstance(fansub, (str)):
                    item_text += f" [{fansub}]"
                chapter = result["chapter"]
                chapters = result["chapters"]
                if isinstance(chapter, (int)) and isinstance(chapters, (int)):
                    item_text += f" {chapter}/{chapters}"
                resol = result["resolucion"]
                if isinstance(resol, (int)):
                    item_text += f" {resol}p"
                password = result["password"]
                if isinstance(password, (int)):
                    item_text += f" - PASSWORD: {password}"
                child_item = QTreeWidgetItem([item_text])
                child_item.setFlags(child_item.flags() | Qt.ItemIsUserCheckable)
                child_item.setCheckState(0, Qt.Unchecked)
                child_item.setData(0, Qt.UserRole, (item_text, result["url"], result["password"]))
                group_item.addChild(child_item)

        self.tree_widget.expandAll()  # Mostrar todo al inicio (opcional)
        self.layout.addWidget(self.tree_widget)

        self.btn_confirm = QPushButton("Descargar seleccionados")
        self.btn_confirm.clicked.connect(self.confirm_selection)
        self.layout.addWidget(self.btn_confirm)

        self.setLayout(self.layout)

    def confirm_selection(self):
        self.selected_links = []
        self.pending = 0
        self.results_temp = []

        top_level_count = self.tree_widget.topLevelItemCount()
        index = 0

        for i in range(top_level_count):
            group_item = self.tree_widget.topLevelItem(i)
            for j in range(group_item.childCount()):
                child = group_item.child(j)
                if child.checkState(0) == Qt.Checked:
                    title, url, password = child.data(0, Qt.UserRole)
                    self.results_temp.append((index, title, None))  # Guardar posición y título
                    worker = URLWorker(index, title, url)
                    worker.signals.finished.connect(self.on_link_ready)
                    self.thread_pool.start(worker)
                    self.pending += 1
                    index += 1

        if self.pending == 0:
            QMessageBox.warning(self, "Nada seleccionado", "Por favor selecciona al menos un enlace.")

    def on_link_ready(self, index, title, link):
        if link:
            self.results_temp[index] = (index, title, link)
        else:
            self.results_temp[index] = None  # O descartar

        self.pending -= 1
        if self.pending == 0:
            self.show_results()

    def show_results(self):
        self.selected_links = [
            (title, link) for _, title, link in sorted(filter(None, self.results_temp))
        ]
        if self.selected_links:
            links_str = "\n\n".join(
                f"{title}\n{link}"
                for title, link in self.selected_links
            )
            QMessageBox.information(self, "Links seleccionados", links_str)

            self.selection_ready.emit([url for _, url in self.selected_links])

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

        search_layout = QHBoxLayout()
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Buscar anime, película o serie...")
        self.search_bar.returnPressed.connect(self.perform_search)
        search_layout.addWidget(self.search_bar)
        self.spinner_search_bar = QLabel()
        self.spinner_search_bar.setMovie(self.spinner_movie)
        self.spinner_search_bar.setFixedSize(24, 24)
        self.spinner_search_bar.setAlignment(Qt.AlignCenter)
        self.spinner_search_bar.setVisible(False)
        search_layout.addWidget(self.spinner_search_bar)
        layout.addLayout(search_layout)

        self.results_list = QListWidget()
        self.results_list.setIconSize(QSize(80, 120))
        self.results_list.itemClicked.connect(self.show_details)
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
        self.spinner_details = QLabel()
        self.spinner_details.setMovie(self.spinner_movie)
        self.spinner_details.setFixedSize(24, 24)
        self.spinner_details.setAlignment(Qt.AlignCenter)
        self.spinner_details.setVisible(False)
        buttons.addWidget(self.trailer_button)
        buttons.addWidget(self.download_button)
        buttons.addWidget(self.spinner_details)

        details_center = QVBoxLayout()
        # Descripción
        details_inner_layout = QHBoxLayout()
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        details_inner_layout.addWidget(self.details)
        # Campos adicionales
        details_right = QVBoxLayout()
        self.info_type = QLabel("Tipo: ")
        self.info_eps = QLabel("Episodios: ")
        self.info_score = QLabel("Score: ")
        self.info_rating = QLabel("Rating: ")
        for label in [self.info_type, self.info_eps, self.info_score, self.info_rating]:
            label.setStyleSheet("font-weight: bold;")
            label.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
            details_right.addWidget(label)
        details_inner_layout.addLayout(details_right)
        details_center.addLayout(details_inner_layout)
        details_center.addLayout(buttons)

        details_layout.addLayout(details_center)
        layout.addLayout(details_layout)

        self.setLayout(layout)
        self.current_item = None

    def perform_search(self):
        term = self.search_bar.text().strip()
        self.results_list.clear()
        self.details.clear()
        self.image_label.clear()
        self.download_button.setEnabled(False)

        if not term:
            return

        self.spinner_search_bar.show()
        self.spinner_movie.start()
        self.search_bar.setDisabled(True)
        self.search_bar.setPlaceholderText("Buscando...")

        worker = SearchWorker(term)
        worker.signals.finished.connect(self.populate_results)
        QThreadPool.globalInstance().start(worker)

    def populate_results(self, results):
        self.search_bar.setDisabled(False)
        self.search_bar.setPlaceholderText("Buscar anime, película o serie...")
        self.spinner_movie.stop()
        self.spinner_search_bar.setVisible(False)

        self.results_list.clear()

        for item in results:
            if item['source'] == "MyAnimeList":
                lw_item = QListWidgetItem(f"[{item['source']}] - {item['title']}")
            else:
                lw_item = QListWidgetItem(item['title'])

            lw_item.setData(Qt.UserRole, item)
            self.results_list.addItem(lw_item)

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

        desc = data.get("description", "Sin descripción.")
        self.details.setPlainText(desc)

        self.info_type.setText(f"<b>Tipo:<br>{data.get('type', '')}</b>")
        self.info_eps.setText(f"<b>Episodios:<br>{data.get('episodes', '')}</b>")
        self.info_score.setText(f"<b>Score:<br>{score_to_color(data.get('score', ''))}</b>")

        rating_text = RATING_TEXT.get(data.get("rating", ""), "<br>No rating")
        self.info_rating.setText(f"<b>Rating:{rating_text}</b>")

        self.download_button.setEnabled(True)

        self.spinner_movie.start()
        self.image_label.setMovie(self.spinner_movie)

        if data.get("image"):
            worker = ImageLoaderWorker(data["image"])
            worker.signals.finished.connect(self.set_detail_image)
            QThreadPool.globalInstance().start(worker)
        else:
            self.spinner_movie.stop()
            self.image_label.clear()

        worker = FullDetailsWorker(data["url"])
        worker.signals.finished.connect(self.update_details)
        QThreadPool.globalInstance().start(worker)

    def update_details(self, full_description,trailer_url):
        if self.current_item:
            data = self.current_item.data(Qt.UserRole)
            if "...read more" in data["description"]: 
                self.details.setPlainText(full_description)
                data["description"] = full_description
            data["trailer"] = trailer_url
            self.current_item.setData(Qt.UserRole, data)
        if trailer_url:
            self.trailer_button.setEnabled(True)
        
    def set_detail_image(self, pixmap):
        self.spinner_movie.stop()
        if pixmap:
            self.image_label.setPixmap(pixmap)
        else:
            self.image_label.clear()

    def show_trailer(self):
        yt_url = self.current_item.data(Qt.UserRole)["trailer"]
        embed_url = yt_url.split('?')[0]

        self.trailer_window = TrailerWindow(embed_url, self)
        self.trailer_window.show()

    def download_item(self):
        if not self.current_item:
            return

        title = self.current_item.data(Qt.UserRole)['title']
        print(f"📥 Buscar para descarga: {title}")
        self.spinner_details.show()
        self.spinner_movie.start()
        self.download_button.setText("Buscando...")
        self.download_button.setEnabled(False)

        self.results_dict = {}
        self.pending_sites = {"Aniteca", "Nyaa", "1337x"}
        self.total_links_found = 0

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
                self.total_links_found += len(sorted_results)

            self.download_button.setText(f"Cargando: {self.total_links_found} enlaces")
            self.pending_sites.discard(site_name)

            if not self.pending_sites:
                self.spinner_movie.stop()
                self.spinner_details.setVisible(False)
                if self.results_dict:
                    self.selector_window = MultiChoiceDownloader(self.results_dict)
                    self.selector_window.show()

                    self.selector_window = MultiChoiceDownloader(self.results_dict)
                    self.selector_window.show()

                    def handle_selection(links):
                        subprocess.Popen(["python", "download_manager.py"] + links)

                    self.selector_window.selection_ready.connect(handle_selection)

                else:
                    QMessageBox.information(self, "Sin resultados", f"No se encontraron descargas para: {title}")
                self.download_button.setText("Descargar")

        # Encolar cada búsqueda
        pool = QThreadPool.globalInstance()
        for name, func in [("Aniteca", search_aniteca), ("Nyaa", search_nyaa), ("1337x", search_1337x)]:
            worker = SiteSearchWorker(name, func, title)
            worker.signals.result_ready.connect(update_results)
            pool.start(worker)

class SilentPage(QWebEnginePage):
    def javaScriptConsoleMessage(self, level, message, lineNumber, sourceID):
        pass
class TrailerWindow(QDialog):
    def __init__(self, embed_url, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Tráiler")
        self.resize(640, 360)

        html = f"""
        <html>
          <head>
            <style>
              body {{ margin: 0; background-color: #000; }}
              iframe {{ width: 100%; height: 100%; border: none; }}
            </style>
          </head>
          <body>
            <iframe src="{embed_url}?autoplay=1" allow="autoplay; encrypted-media" allowfullscreen></iframe>
          </body>
        </html>
        """

        layout = QVBoxLayout()
        self.web_view = QWebEngineView()
        self.web_view.setPage(SilentPage(self.web_view)) 
        self.web_view.setHtml(html)
        layout.addWidget(self.web_view)
        self.setLayout(layout)
        #autoplay
        QTimer.singleShot(2000, self.simulate_k_keypress)

    def simulate_k_keypress(self):
        event = QKeyEvent(QKeyEvent.KeyPress, Qt.Key_K, Qt.NoModifier, 'k')
        QApplication.postEvent(self.web_view.focusProxy(), event)
        event_release = QKeyEvent(QKeyEvent.KeyRelease, Qt.Key_K, Qt.NoModifier, 'k')
        QApplication.postEvent(self.web_view.focusProxy(), event_release)

    def closeEvent(self, event):
        # Esto detiene el video cargando una página en blanco
        self.web_view.setHtml("<html><body></body></html>")
        super().closeEvent(event)

# --- Ejecutar aplicación ---
if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MediaSearchUI()
    window.show()
    sys.exit(app.exec_())
