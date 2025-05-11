import sys
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLineEdit, QListWidget,
    QLabel, QListWidgetItem, QTextEdit, QPushButton, QHBoxLayout
)
from PyQt5.QtGui import QPixmap, QImage
from PyQt5.QtCore import Qt, QSize
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

# --- UI principal ---
class MediaSearchUI(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Buscador de medios")
        self.resize(800, 500)
        layout = QVBoxLayout()

        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Buscar anime, película o serie...")
        self.search_bar.returnPressed.connect(self.perform_search)
        layout.addWidget(self.search_bar)

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

        results = search_anilist(term) + search_tmdb(term)
        for item in results:
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
