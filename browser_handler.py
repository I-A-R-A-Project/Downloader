import re, os, uuid
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEnginePage
from PyQt5.QtCore import QUrl, QTimer, pyqtSignal
from bs4 import BeautifulSoup

class SilentPage(QWebEnginePage):
    def javaScriptConsoleMessage(self, level, message, lineNumber, sourceID):
        pass

class UniversalDownloader(QWebEngineView):
    direct_links_ready = pyqtSignal(list)

    def __init__(self, urls):
        super().__init__()
        self.setPage(SilentPage(self))
        self.urls = []
        self.offscreen_results = []

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
                self.urls.append((url, ""))

        self.current_index = 0
        self.results = []

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
            filename = f"{file_id}.bin"
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
        folder_name = title_tag["title"] if title_tag and title_tag.has_attr("title") else "Subcarpeta"
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