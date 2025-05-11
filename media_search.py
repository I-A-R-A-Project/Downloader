import re
import subprocess
import sys
import webbrowser
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLineEdit, QListWidget,
    QLabel, QListWidgetItem, QTextEdit, QPushButton, QHBoxLayout,
    QGraphicsOpacityEffect,QMessageBox
)
from PyQt5.QtGui import QPixmap, QImage,QMovie
from PyQt5.QtCore import (
    Qt, QSize, QObject, pyqtSignal, QRunnable, QThreadPool, QTimer,QPropertyAnimation
)
import requests
from aniteca import search_aniteca_api,get_chapter_links,extract_direct_link
from bs4 import BeautifulSoup
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor, as_completed

def search_mal(query):
    url = f"https://myanimelist.net/anime.php?cat=anime&q={query}&type=0&score=0&status=0&p=0&r=0&sm=0&sd=0&sy=0&em=0&ed=0&ey=0&c[]=a&c[]=b&c[]=c&c[]=g"
    try:
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
    except Exception as e:
        print("[MyAnimeList] Error:", e)
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    results = []

    for row in soup.select("tr:has(img)"):
        try:
            img_tag = row.select_one("img")
            title_tag = row.select_one("td:nth-of-type(2) a strong")
            description_tag = row.select_one(".pt4")
            info_cells = row.find_all("td", class_="ac")

            title = title_tag.text.strip()
            link = title_tag.find_parent("a")["href"]

            # Extraer y mejorar la imagen
            raw_img = img_tag.get("data-src", img_tag.get("src", ""))
            full_img = raw_img.replace("/r/50x70", "").split("?")[0]  # remueve resize y parámetros

            description = description_tag.get_text(strip=True) if description_tag else ""
            tipo = info_cells[0].text.strip() if len(info_cells) > 0 else ""
            episodios = info_cells[1].text.strip() if len(info_cells) > 1 else ""
            score = info_cells[2].text.strip() if len(info_cells) > 2 else ""
            rating = info_cells[3].text.strip() if len(info_cells) > 3 else ""

            results.append({
                "title": title,
                "url": link,
                "image": full_img,
                "description": f"{description} | Tipo: {tipo} | Episodios: {episodios} | Score: {score} | Rating: {rating}",
                "source": "MyAnimeList",
                "rating": rating
            })
        except Exception as e:
            print("Error procesando un resultado:", e)
            continue

    return results

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
        #try:
        #    anilist_results = search_anilist(self.term)
        #except Exception as e:
        #    print(f"Error en la búsqueda: {e}")
        results = search_mal(self.term)
        #tmdb_results = search_tmdb(self.term)
        #combined = anilist_results + tmdb_results
        self.signals.finished.emit(results)

def search_aniteca(query):
    results = []
    try:
        animes = search_aniteca_api(query)
        for anime in animes:
            episodios = get_chapter_links(anime["id"], anime["numepisodios"])
            with ThreadPoolExecutor(max_workers=10) as executor:
                future_to_ep = {
                    executor.submit(extract_direct_link, ep['servername'], ep['online_id']): ep
                    for ep in episodios
                }
                for future in as_completed(future_to_ep):
                    ep = future_to_ep[future]
                    try:
                        link_directo = future.result()
                        if link_directo:
                            results.append({
                                "title": anime['nombre'],
                                "chapter": ep['capitulo'],
                                "chapters": anime["numepisodios"],
                                "url_type": ep['servername'],
                                "url": link_directo
                            })
                    except Exception as e:
                        print(f"[Thread Error] {e}")

    except Exception as e:
        print(f"[Aniteca] Error: {e}")
    return results

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
        self.results_dict = results_dict  # dict: {sitio: [(title, link), ...]}
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
        #lw_item = QListWidgetItem(f"{item['title']} ({item['year']}) - {item['type']} [{item['source']}]")
        if item['source']=="MyAnimeList":
            lw_item = QListWidgetItem(f"{item['title']} [{item['source']}]")

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
        def sort(array):
            sorted_array= sorted(
                array,
                key=lambda x: (
                    x.get("title", "").lower(),
                    int(x.get("chapter") or 0)  # Usa 0 si es None o si no existe
                )
            )
            return sorted_array
        if self.current_item:
            title = self.current_item['title']
            print(f"📥 Buscar para descarga: {title}")

            # 1. Buscar en los sitios (múltiples resultados)
            aniteca_links = search_aniteca(title)
            nyaa_links = search_nyaa(title)
            x1337_links = search_1337x(title)

            # 2. Organizar en dict por sitio
            results = {}
            if aniteca_links:
                results["Aniteca"] = sort(aniteca_links)
            if nyaa_links:
                results["Nyaa"] = sort(nyaa_links)
            if x1337_links:
                results["1337x"] = sort(x1337_links)

            if results:
                self.selector_window = MultiChoiceDownloader(results)
                self.selector_window.show()

                def handle_selection():
                    aux=self.selector_window.selected_links
                    print(aux)
                    array=[]
                    for _,url in aux:
                        array.append(url)
                    subprocess.Popen(["python", "main.py"]+array)

                self.selector_window.btn_confirm.clicked.connect(handle_selection)
            else:
                QMessageBox.information(self, "Sin resultados", f"No se encontraron descargas para: {title}")

            
# --- Ejecutar aplicación ---
if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MediaSearchUI()
    window.show()
    sys.exit(app.exec_())
