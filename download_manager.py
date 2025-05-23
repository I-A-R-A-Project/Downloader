import time, re, os, sys, uuid, functools
from PyQt5.QtWidgets import QApplication, QWidget, QHBoxLayout, QVBoxLayout, QLabel, QProgressBar, QScrollArea, QPushButton, QDialog, QTextEdit
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEnginePage
from PyQt5.QtCore import QUrl, QTimer, pyqtSignal, QThreadPool
from bs4 import BeautifulSoup
from workers import DownloadSignals, FileDownloader
from settings_dialog import SettingsDialog, load_config, DEFAULT_CONFIG
from torrent import TorrentUpdater, add_magnet_link, add_torrent_file

from enum import Enum

class DownloadType(Enum):
    NORMAL = 0
    TORRENT = 1
    TEMPORAL = 2

#Mucho ruido en consola, shhhh
class SilentPage(QWebEnginePage):
    def javaScriptConsoleMessage(self, level, message, lineNumber, sourceID):
        pass

#Navegador Web
class UniversalDownloader(QWebEngineView):
    direct_links_ready = pyqtSignal(list)
    
    def __init__(self, urls):
        super().__init__()
        self.setPage(SilentPage(self))
        self.urls = []
        self.offscreen_results = []
        # Clasificamos las URLs: torrents/magnets se manejan sin navegador
        for url in urls:
            if url.startswith("magnet:?"):
                print(f"🔗 Magnet detectado: {url}")
                filename = f"{uuid.uuid4().hex[:8]}.magnet"
                self.offscreen_results.append((filename, url))
            elif url.endswith(".torrent") or "torrage" in url or "itorrents" in url:
                print(f"🔗 Torrent detectado: {url}")
                filename = url.split("/")[-1].split("?")[0]
                self.offscreen_results.append((filename, url))
            else:
                self.urls.append((url, ""))  # Se maneja con QWebEngine

        self.current_index = 0
        self.results = []

        # Si no quedan URLs para el navegador, emitir directamente
        if not self.urls:
            QTimer.singleShot(100, lambda: self.direct_links_ready.emit(self.offscreen_results))
            return

        self.setWindowTitle("Universal Downloader")
        self.loadFinished.connect(self.on_load_finished)
        self.load(QUrl(self.urls[self.current_index][0]))

    def start(self):
        if self.urls:
            self.show()
        else:
            self.close()

    def on_load_finished(self):
        print(self.urls[self.current_index])
        print(f"[{self.current_index+1}/{len(self.urls)}] Páginas cargadas...")
        QTimer.singleShot(1000, self.route_url_handling)

    def route_url_handling(self):
        url, path = self.urls[self.current_index]
        
        if "mediafire.com" in url:
            self.handle_mediafire(url, path)
        elif "4shared.com" in url:
            self.page().toHtml(lambda html: self.handle_4shared(html, path))
        elif "drive.google.com" in url:
            self.handle_gdrive(url, path)
        else:
            print("❌ Sitio no soportado.")
            self.results.append(None)
            self.proceed_to_next()

    def handle_4shared(self, html, current_path):
        soup = BeautifulSoup(html, "html.parser")
        download_button = soup.find("a", {"id": "freeDlButton"})

        #No funciona por ahora, ver despues
        #link d prueba https://www.4shared.com/file/uCcrCZLG/DAEMON_Tools_Lite_44540315.html
        if download_button and download_button.has_attr("href"):
            direct_link = download_button["href"]
            title_tag = soup.find("title")
            filename = title_tag.text.strip().split(" - ")[0] if title_tag else os.path.basename(direct_link)
            full_path = os.path.join(current_path, filename)
            print(f"✅ Enlace directo (4shared): {direct_link}")
            print(f"💾 Guardar como: {full_path}")
            self.results.append((full_path, direct_link))
        else:
            print("❌ No se encontró el enlace de descarga en 4shared.")
            self.results.append((None, None))
        self.proceed_to_next()

    def handle_gdrive(self, url, current_path):
        match = re.search(r"/d/([a-zA-Z0-9_-]+)", url)
        if match:
            file_id = match.group(1)
            direct_link = f"https://drive.usercontent.google.com/download?id={file_id}&export=download"
            filename = f"{file_id}.bin"  # Nombre provisional si no podemos obtener el real
            full_path = os.path.join(current_path, filename)
            print(f"✅ Enlace directo (Google Drive): {direct_link}")
            print(f"💾 Guardar como: {full_path}")
            self.results.append((full_path, direct_link))
        else:
            print("❌ No se pudo extraer el ID del archivo de Google Drive.")
            self.results.append((None, None))
        self.proceed_to_next()

    def handle_mediafire(self, url, path):
        if "/folder/" in url:
            self.page().toHtml(lambda html: self.handle_mediafire_folder(html, path))
        elif "/file/" in url or "/download/" in url:
            self.page().toHtml(lambda html: self.handle_mediafire_file(html, path))
        else:
            print("❌ URL de MediaFire no reconocida.")
            self.results.append(None)
            self.proceed_to_next()

    def handle_mediafire_folder(self, html, base_path):
        soup = BeautifulSoup(html, "html.parser")
        aux = []
        title_tag = soup.find(id="folder_name")
        folder_name = "Subcarpeta"  # Fallback
        if title_tag and title_tag.has_attr("title"):
            folder_name = title_tag["title"]

        subfolder_path = os.path.join(base_path, folder_name)
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if re.match(r"^https?://www\.mediafire\.com/file/", href):
                aux.append(href)
            elif re.match(r"^#\w+", href):
                folder_id = href.lstrip("#")
                span = a.find("span", class_="item-name")
                if span:
                    span_name = span.text.strip().replace(" ", "_")
                    full_url = f"https://www.mediafire.com/folder/{folder_id}/{span_name}"
                    aux.append(full_url)

        file_links = list(set(aux))
        if file_links:
            print(f"📁 {len(file_links)} archivos encontrados en carpeta '{folder_name}'.")
            insert_position = self.current_index + 1
            for link in reversed(file_links):
                self.urls.insert(insert_position, (link, subfolder_path))
        else:
            print("❌ No se encontraron archivos en la carpeta.")
        self.proceed_to_next()

    def handle_mediafire_file(self, html, current_path):
        soup = BeautifulSoup(html, "html.parser")
        button = soup.find("a", {"id": "downloadButton"})
        filename_tag = soup.find("div", class_="filename")
        if button and button.has_attr("href"):
            direct_link = button["href"]
            filename = filename_tag.text.strip() if filename_tag else os.path.basename(direct_link)
            full_path = os.path.join(current_path, filename)
            print(f"✅ Enlace directo: {direct_link}")
            print(f"💾 Guardar como: {full_path}")
            self.results.append((full_path, direct_link))
        else:
            print("❌ No se encontró el enlace de descarga.")
            self.results.append((None, None))
        self.proceed_to_next()

    def proceed_to_next(self):
        self.current_index += 1
        if self.current_index < len(self.urls):
            self.load(QUrl(self.urls[self.current_index][0]))
        else:
            self.direct_links_ready.emit(self.results)
            self.close()

#Main - Ventana de descargas
class DownloadWindow(QWidget):
    def __init__(self, urls):
        super().__init__()
        self.setWindowTitle("Descargador Universal")
        self.setMinimumSize(400, 200)
        self.layout = QVBoxLayout(self)

        self.config = load_config()
        self.folder_path = self.config.get("folder_path",DEFAULT_CONFIG["folder_path"])
        self.open_on_finish = self.config.get("open_on_finish",DEFAULT_CONFIG["open_on_finish"])
        self.max_parallel_downloads = self.config.get("max_parallel_downloads",DEFAULT_CONFIG["max_parallel_downloads"])

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.inner_widget = QWidget()
        self.inner_layout = QVBoxLayout(self.inner_widget)
        self.scroll.setWidget(self.inner_widget)
        self.layout.addWidget(self.scroll)
        self.settings_button = QPushButton("Configuración ⚙")
        self.settings_button.clicked.connect(self.open_settings_dialog)
        self.layout.addWidget(self.settings_button)

        self.torrent_hashes = {}
        self.torrent_timer = QTimer()
        self.torrent_timer.timeout.connect(self.start_torrent_update)
        self.torrent_timer.start(3000)

        self.progress_bars = []
        self.labels = []
        self.temp_progress_bars = []
        self.temp_labels = []
        self.torrent_progress_bars = []
        self.torrent_labels = []
        self.downloader = UniversalDownloader(urls)
        self.downloader.direct_links_ready.connect(self.start_downloads)
        self.downloader.start()

    def start_downloads(self, direct_links):
        for index, (relative_path, link) in enumerate(direct_links):
            full_path = os.path.join(self.folder_path, relative_path)
            if not link:
                continue

            if link.startswith("magnet:?"):
                torrent_hash = add_magnet_link(link, self.folder_path)
                if torrent_hash:
                    print("Magnet agregado "+torrent_hash)

            elif link.endswith(".torrent"):
                def make_on_finished(idx, path):
                    def check_file(attempt=1):
                        if os.path.exists(path):
                            add_torrent_file(path, self.folder_path)
                            self.mark_finished(idx, DownloadType.TEMPORAL)
                        elif attempt < 10:
                            QTimer.singleShot(200, lambda: check_file(attempt + 1))
                        else:
                            print(f"❌ Archivo .torrent no encontrado: {path}")
                    return lambda: check_file()
                
                label = QLabel(f"Descargando .torrent: {relative_path}")
                bar = QProgressBar()
                bar.setValue(0)
                self.inner_layout.addWidget(label)
                self.inner_layout.addWidget(bar)
                self.temp_labels.append(label)
                self.temp_progress_bars.append(bar)

                signals = DownloadSignals()
                signals.progress.connect(self.update_progress)
                signals.finished.connect(make_on_finished(index, full_path))

                thread = FileDownloader(link, full_path, index, signals)
                QThreadPool.globalInstance().start(thread)

            else:
                # Descarga directa normal
                dir_path = os.path.dirname(full_path)
                if dir_path:
                    os.makedirs(dir_path, exist_ok=True)

                label = QLabel(f"Descargando: {relative_path}")
                bar = QProgressBar()
                bar.setValue(0)
                self.inner_layout.addWidget(label)
                self.inner_layout.addWidget(bar)
                self.labels.append(label)
                self.progress_bars.append(bar)

                signals = DownloadSignals()
                signals.progress.connect(self.update_progress)
                signals.finished.connect(lambda idx=index: self.mark_finished(idx))

                thread = FileDownloader(link, full_path, index, signals)
                QThreadPool.globalInstance().start(thread)
        
        self.show()

    def open_settings_dialog(self):
        dialog = SettingsDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            self.folder_path,
            self.open_on_finish,
            self.max_parallel_downloads = apply_settings()

    def start_torrent_update(self):
        updater = TorrentUpdater()
        updater.signals.result.connect(self.on_torrent_data_received)
        updater.signals.error.connect(self.on_torrent_update_error)
        QThreadPool.globalInstance().start(updater)

    def on_torrent_update_error(self,message):
        if message!="""<!DOCTYPE HTML PUBLIC "-//IETF//DTD HTML 2.0//EN">
            <html><head>
            <title>404 Not Found</title>
            </head><body>
            <h1>Not Found</h1>
            <p>The requested URL was not found on this server.</p>
            </body></html>""":
            print(f"Error al actualizar progreso de torrents: {message}")

    def on_torrent_data_received(self, torrents):
        for t in torrents:
            if t.state in ("pausedDL", "pausedUP", "checkingUP", "checkingDL", "queuedDL"):
                continue
            if t.hash in self.torrent_hashes:
                index, _ = self.torrent_hashes[t.hash]
                percent = int(t.progress * 100)
                self.torrent_progress_bars[index].setValue(percent)
                if percent >= 100:
                    self.mark_finished(index)
                    self.torrent_hashes.pop(t.hash, None)
            else:
                label = QLabel(f"Descargando torrent: {t.name}")
                bar = QProgressBar()
                bar.setValue(int(t.progress * 100))
                self.inner_layout.addWidget(label)
                self.inner_layout.addWidget(bar)
                index = len(self.torrent_labels)
                self.torrent_labels.append(label)
                self.torrent_progress_bars.append(bar)
                self.torrent_hashes[t.hash] = (index, t.name)

    def update_progress(self, index, percent):
        if index >= len(self.progress_bars):
            return
        self.progress_bars[index].setValue(percent)

    def mark_finished(self, index, download_type=DownloadType.NORMAL):
        if download_type == DownloadType.TORRENT:
            lb = self.torrent_labels[index]
            pb = self.torrent_progress_bars[index]
        elif download_type == DownloadType.TEMPORAL:
            lb = self.temp_labels[index]
            pb = self.temp_progress_bars[index]
        else:  # NORMAL
            lb = self.labels[index]
            pb = self.progress_bars[index]

        done = f"✅ Completado: {lb.text()[12:]}"
        print(done)
        lb.setText(done)
        self.inner_layout.removeWidget(pb)
        pb.deleteLater()
        if download_type == DownloadType.TEMPORAL:
            self.inner_layout.removeWidget(lb)
            lb.deleteLater()
         
#Optional - Ventana para ingresar links
class LinkInputWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pegar enlaces de paginas de descarga")
        self.setMinimumSize(400, 200)

        layout = QVBoxLayout()
        self.instructions = QLabel("Pega uno o más enlaces (uno por línea):")
        self.textbox = QTextEdit()
        self.accept_button = QPushButton("Iniciar Descargas")
        self.accept_button.clicked.connect(self.proceed)
        self.settings_button = QPushButton('⚙')
        self.settings_button.clicked.connect(self.open_settings_dialog)
        self.settings_button.setFixedWidth(25) 

        layout.addWidget(self.instructions)
        layout.addWidget(self.textbox)
        buttons = QHBoxLayout()
        buttons.addWidget(self.accept_button)
        buttons.addWidget(self.settings_button)
        layout.addLayout(buttons)
        self.setLayout(layout)

        self.links = []

    def open_settings_dialog(self):
        dialog = SettingsDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            apply_settings()
    
    def proceed(self):
        text = self.textbox.toPlainText().strip()
        if text:
            self.links = [line.strip() for line in text.splitlines() if line.strip()]
            self.close()

def apply_settings():
    config = load_config()
    folder_path = config.get("folder_path")
    open_on_finish = config.get("open_on_finish")
    max_parallel_downloads = config.get("max_parallel_downloads")
    print(f"✅ Configuración actualizada: {config}")
    return folder_path, open_on_finish, max_parallel_downloads

if __name__ == '__main__':
    os.system("title Descargas")
    app = QApplication(sys.argv)
    args = sys.argv[1:]
    if not args:
        link_input = LinkInputWindow()
        link_input.show()
        app.exec_()
        args = link_input.links
    if args:
        window = DownloadWindow(args)
        sys.exit(app.exec_())