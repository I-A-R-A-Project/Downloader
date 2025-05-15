import re, subprocess, sys, requests
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLineEdit, QListWidget,
    QLabel, QListWidgetItem, QTextEdit, QPushButton, QHBoxLayout,
    QMessageBox, QDialog
)
from PyQt5.QtGui import QMovie
from PyQt5.QtCore import (
    Qt, QSize, QThreadPool, 
)
from aniteca import search_aniteca
from bs4 import BeautifulSoup
from PyQt5.QtWebEngineWidgets import QWebEngineView

from workers import FullDetailsWorker, ImageLoaderWorker, SearchWorker, SiteSearchWorker

# --- TMDb con imagen ---
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
        url = f"https://nyaa.si/?f=0&c=0_0&q={query.replace(' ', '+')}&s=seeders&o=desc"
        r = requests.get(url, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        rows = soup.select("tr.default")
        for row in rows[:100]:
            title_tag = row.select_one("td:nth-child(2) a[href*='view']")
            torrent_tag = row.select_one("td.text-center a[href$='.torrent']")

            if title_tag and torrent_tag:
                title = title_tag.text.strip()
                torrent_url = "https://nyaa.si" + torrent_tag["href"]

                # Heurística: buscar número de episodio
                chapter = None
                match = re.search(r'\b(?:ep?\.?|episode)?\s*(\d{1,4})\b', title, re.IGNORECASE)
                if match:
                    chapter = int(match.group(1))

                results.append({
                    "title": title,
                    "chapter": chapter,
                    "chapters": None,
                    "url_type": "torrent",
                    "url": torrent_url
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
            if link:
                title = link.text.strip()
                detail_url = "https://1337x.to" + link["href"]

                # Scrapear enlace directo .torrent desde la página del torrent
                detail_r = requests.get(detail_url, headers=headers, timeout=10)
                detail_soup = BeautifulSoup(detail_r.text, "html.parser")
                torrent_tag = detail_soup.select_one("a[href$='.torrent']")

                if not torrent_tag:
                    continue

                torrent_url = torrent_tag["href"]

                # Heurística: buscar número de episodio
                chapter = None
                match = re.search(r'\b(?:ep?\.?|episode)?\s*(\d{1,4})\b', title, re.IGNORECASE)
                if match:
                    chapter = int(match.group(1))

                results.append({
                    "title": title,
                    "chapter": chapter,
                    "chapters": None,
                    "url_type": "torrent",
                    "url": torrent_url
                })
    except Exception as e:
        print(f"[1337x] Error: {e}")
    return results

class MultiChoiceDownloader(QWidget):
    def __init__(self, results_dict):
        super().__init__()
        self.setWindowTitle("Selecciona los enlaces para descargar")
        self.setGeometry(300, 300, 600, 400)
        self.results_dict = results_dict
        self.selected_links = []
        self.layout = QVBoxLayout()
        self.list_widget = QListWidget()
        for source, results in results_dict.items():
            for result in results:
                title = result["title"]
                item = QListWidgetItem(f"[{source}] {result['url_type']} - {title} {result['chapter']}/{result['chapters']}")
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Unchecked)
                item.setData(Qt.UserRole, result["url"])
                self.list_widget.addItem(item)
        self.layout.addWidget(self.list_widget)
        self.btn_confirm = QPushButton("Descargar seleccionados")
        self.btn_confirm.clicked.connect(self.confirm_selection)
        self.layout.addWidget(self.btn_confirm)
        self.setLayout(self.layout)

    def confirm_selection(self):
        self.selected_links = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.checkState() == Qt.Checked:
                self.selected_links.append((item.text(), item.data(Qt.UserRole)))
        if self.selected_links:
            links_str = "\n".join(f"{title}\n{link}" for title, link in self.selected_links)
            QMessageBox.information(self, "Links seleccionados", links_str)
        else:
            QMessageBox.warning(self, "Nada seleccionado", "Por favor selecciona al menos un enlace.")

# --- UI principal ---
class MediaSearchUI(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Buscador de medios")
        self.resize(800, 500)
        layout = QVBoxLayout()

        search_layout = QHBoxLayout()
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Buscar anime, película o serie...")
        self.search_bar.returnPressed.connect(self.perform_search)
        search_layout.addWidget(self.search_bar)
        self.spinner = QLabel()
        self.spinner_movie = QMovie("spinner.gif")
        self.spinner_movie.setScaledSize(QSize(20, 20)) 
        self.spinner.setMovie(self.spinner_movie)
        self.spinner.setFixedSize(24, 24)
        self.spinner.setAlignment(Qt.AlignCenter)
        self.spinner.setVisible(False)
        search_layout.addWidget(self.spinner)
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
        buttons.addWidget(self.trailer_button)
        buttons.addWidget(self.download_button)

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

        self.spinner.show()
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
        self.spinner.setVisible(False)

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
        html = f"""<html><head><style>
        body {{ margin: 0; background-color: #000; }}
        iframe {{ width: 100%; height: 100%; border: none; }}
        </style></head><body>
        <iframe src="{embed_url}?autoplay=1" allow="autoplay; encrypted-media" allowfullscreen></iframe>        </body>
        </html>"""
        self.trailer_window = QDialog(self)
        self.trailer_window.setWindowTitle("Tráiler")
        self.trailer_window.resize(640, 360)
        layout = QVBoxLayout()
        web_view = QWebEngineView()
        web_view.setHtml(html)
        layout.addWidget(web_view)
        self.trailer_window.setLayout(layout)
        self.trailer_window.exec_()

    def download_item(self):
        if not self.current_item:
            return

        title = self.current_item.data(Qt.UserRole)['title']
        print(f"📥 Buscar para descarga: {title}")
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
                        x.get("title", "").lower(),
                        int(x.get("chapter") or 0)
                    )
                )
                self.results_dict[site_name] = sorted_results
                self.total_links_found += len(sorted_results)

            self.download_button.setText(f"Cargando: {self.total_links_found} enlaces")
            self.pending_sites.discard(site_name)

            if not self.pending_sites:
                if self.results_dict:
                    self.selector_window = MultiChoiceDownloader(self.results_dict)
                    self.selector_window.show()

                    def handle_selection():
                        aux = self.selector_window.selected_links
                        links = [url for _, url in aux]
                        subprocess.Popen(["python", "download_manager.py"] + links)

                    self.selector_window.btn_confirm.clicked.connect(handle_selection)
                else:
                    QMessageBox.information(self, "Sin resultados", f"No se encontraron descargas para: {title}")
                self.download_button.setText("Descargar")

        # Encolar cada búsqueda
        pool = QThreadPool.globalInstance()
        for name, func in [("Aniteca", search_aniteca), ("Nyaa", search_nyaa), ("1337x", search_1337x)]:
            worker = SiteSearchWorker(name, func, title)
            worker.signals.result_ready.connect(update_results)
            pool.start(worker)
           
# --- Ejecutar aplicación ---
if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MediaSearchUI()
    window.show()
    sys.exit(app.exec_())
