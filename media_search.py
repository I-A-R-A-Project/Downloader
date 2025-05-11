import sys
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLineEdit, QListWidget,
    QLabel, QListWidgetItem, QTextEdit, QPushButton, QHBoxLayout,
    QGraphicsOpacityEffect
)
from PyQt5.QtGui import QPixmap, QImage,QMovie
from PyQt5.QtCore import (
    Qt, QSize, QObject, pyqtSignal, QRunnable, QThreadPool, QTimer,QPropertyAnimation
)
import requests
from io import BytesIO

# --- AniList con resultados ---
def search_anilist(query):
    url = "https://graphql.anilist.co"
    query_str = '''
    query ($search: String) {
      Page(perPage: 15) {
        media(search: $search, type: ANIME) {
          title {
            romaji
            english
          }
          startDate { year }
          description(asHtml: false)
          format
          coverImage {
            medium
          }
        }
      }
    }
    '''
    variables = { "search": query }
    res = requests.post(url, json={'query': query_str, 'variables': variables})
    items = res.json().get("data", {}).get("Page", {}).get("media", [])
    return [{
        "source": "AniList",
        "title": m["title"]["english"] or m["title"]["romaji"],
        "year": m["startDate"]["year"],
        "type": m["format"],
        "description": m["description"],
        "image": m["coverImage"]["medium"]
    } for m in items]

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

class SearchWorkerSignals(QObject):
    finished = pyqtSignal(list)

class SearchWorker(QRunnable):
    def __init__(self, term):
        super().__init__()
        self.term = term
        self.signals = SearchWorkerSignals()

    def run(self):
        try:
            anilist_results = search_anilist(self.term)
            tmdb_results = search_tmdb(self.term)
            combined = anilist_results + tmdb_results
        except Exception as e:
            combined = []
            print(f"Error en la búsqueda: {e}")
        self.signals.finished.emit(combined)

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

        self.image_label = QLabel()
        self.image_label.setFixedSize(120, 180)
        self.image_label.setScaledContents(True)

        self.details = QTextEdit()
        self.details.setReadOnly(True)

        self.download_button = QPushButton("Descargar")
        self.download_button.clicked.connect(self.download_item)
        self.download_button.setEnabled(False)

        details_layout = QHBoxLayout()
        details_layout.addWidget(self.image_label)
        details_right = QVBoxLayout()
        details_right.addWidget(self.details)
        details_right.addWidget(self.download_button)
        details_layout.addLayout(details_right)
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
        self.animated_results = results
        self.animation_index = 0
        self.result_timer = QTimer()
        self.result_timer.timeout.connect(self.add_next_result)
        self.result_timer.start(100)  # 100 ms entre ítems

    def add_next_result(self):
        if self.animation_index >= len(self.animated_results):
            self.result_timer.stop()
            return

        item = self.animated_results[self.animation_index]
        lw_item = QListWidgetItem(f"{item['title']} ({item['year']}) - {item['type']} [{item['source']}]")

        if item.get("image"):
            try:
                img_data = requests.get(item["image"]).content
                image = QImage()
                image.loadFromData(img_data)
                lw_item.setIcon(QPixmap.fromImage(image))
            except:
                pass

        lw_item.setData(Qt.UserRole, item)
        self.results_list.addItem(lw_item)

        row = self.results_list.count() - 1
        item_widget = self.results_list.item(row)
        item_effect = QGraphicsOpacityEffect()
        self.results_list.itemWidget(item_widget)  # None, fallback a animar global
        self.results_list.setGraphicsEffect(item_effect)

        anim = QPropertyAnimation(item_effect, b"opacity")
        anim.setDuration(300)
        anim.setStartValue(0)
        anim.setEndValue(1)
        anim.start()

        self.animation_index += 1


    def show_details(self, item):
        data = item.data(Qt.UserRole)
        self.current_item = data
        self.details.setPlainText(data.get("description", "Sin descripción."))
        self.download_button.setEnabled(True)
        # Mostrar imagen grande
        if data.get("image"):
            try:
                img_data = requests.get(data["image"]).content
                image = QImage()
                image.loadFromData(img_data)
                self.image_label.setPixmap(QPixmap.fromImage(image))
            except:
                self.image_label.clear()
        else:
            self.image_label.clear()

    def download_item(self):
        if self.current_item:
            print(f"📥 Descargar: {self.current_item['title']} ({self.current_item['source']})")
            
# --- Ejecutar aplicación ---
if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MediaSearchUI()
    window.show()
    sys.exit(app.exec_())
