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

        for entry in urls:
            url = entry.get("url", "")
            path = entry.get("path", "")
            password = entry.get("password", "")
            if url.startswith("magnet:?"):
                print(f"🔗 Magnet detectado: {url}")
                filename = f"{uuid.uuid4().hex[:8]}.magnet"
                self.offscreen_results.append((os.path.join(path, filename), url))
            elif url.endswith(".torrent") or "torrage" in url or "itorrents" in url:
                print(f"🔗 Torrent detectado: {url}")
                filename = url.split("/")[-1].split("?")[0]
                self.offscreen_results.append((os.path.join(path, filename), url))
            else:
                self.urls.append((url, path))

        self.current_index = 0
        self.results = []

        self.setWindowTitle("Universal Downloader")
        self.loadFinished.connect(self.on_load_finished)
        self._started = False

    def start(self):
        if self._started:
            return
        self._started = True
        if not self.urls:
            QTimer.singleShot(0, lambda: self.direct_links_ready.emit(self.offscreen_results))
            self.close()
            return
        if self.urls:
            self.show()
            self.process_current_url()
        else:
            self.close()

    def on_load_finished(self):
        print(self.urls[self.current_index])
        print(f"[{self.current_index+1}/{len(self.urls)}] Páginas cargadas...")
        QTimer.singleShot(1000, self.route_url_handling)

    def process_current_url(self):
        if not self.urls:
            self.direct_links_ready.emit(self.results)
            self.close()
            return
        url, path = self.urls[self.current_index]
        # Evitar cargar páginas de archivos MediaFire con WebEngine (causan crash)
        if "mediafire.com" in url and ("/file/" in url or "/download/" in url):
            self.handle_mediafire_file_requests(url, path)
            return
        self.load(QUrl(url))

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
            self.handle_mediafire_folder_api(url, path)
        elif "/file/" in url or "/download/" in url:
            self.handle_mediafire_file_requests(url, path)
        else:
            print("❌ URL de MediaFire no reconocida.")
            self.results.append(None)
            self.proceed_to_next()

    def safe_handle_mediafire_folder(self, html, url, base_path):
        try:
            self.handle_mediafire_folder(html, base_path)
        except Exception as e:
            print(f"❌ Error procesando carpeta MediaFire (WebEngine): {e}")
            self.results.append((None, None))
            self.proceed_to_next()

    def handle_mediafire_folder_api(self, url, base_path):
        folder_key = self.extract_mediafire_folder_key(url)
        if not folder_key:
            print("❌ No se pudo extraer el folder_key de MediaFire.")
            self.results.append((None, None))
            self.proceed_to_next()
            return
        try:
            all_links = []
            all_folders = []
            files = self.fetch_mediafire_folder_items(folder_key, "files")
            folders = self.fetch_mediafire_folder_items(folder_key, "folders")
            for f in files:
                quickkey = f.get("quickkey")
                filename = f.get("filename") or "archivo"
                if quickkey:
                    all_links.append(self.build_mediafire_file_url(quickkey, filename))
            for f in folders:
                sub_key = f.get("folderkey")
                name = (f.get("name") or "Subcarpeta").replace(" ", "_")
                if sub_key:
                    all_folders.append(self.build_mediafire_folder_url(sub_key, name))
            if all_links or all_folders:
                folder_name = self.extract_mediafire_folder_name(url)
                subfolder_path = os.path.join(base_path, folder_name)
                print(f"📁 {len(all_links)} archivos encontrados en carpeta '{folder_name}'.")
                insert_position = self.current_index + 1
                for link in reversed(all_links + all_folders):
                    self.urls.insert(insert_position, (link, subfolder_path))
                self.proceed_to_next()
            else:
                print("❌ No se encontraron archivos en la carpeta (API). Probando HTML...")
                self.page().toHtml(lambda html: self.safe_handle_mediafire_folder(html, url, base_path))
        except Exception as e:
            print(f"❌ Error procesando carpeta MediaFire (API): {e}")
            self.results.append((None, None))
            self.proceed_to_next()

    def handle_mediafire_file_api(self, url, current_path):
        quickkey = self.extract_mediafire_quickkey(url)
        if not quickkey:
            print("❌ No se pudo extraer el quickkey de MediaFire.")
            self.results.append((None, None))
            self.proceed_to_next()
            return
        try:
            import requests
            headers = {"User-Agent": "Mozilla/5.0"}
            params = {"quick_key": quickkey, "response_format": "json"}
            response = requests.get(
                "https://www.mediafire.com/api/1.5/file/get_links.php",
                params=params,
                timeout=30,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()
            links = data.get("response", {}).get("links", {})
            direct_link = links.get("normal_download") or links.get("direct_download")
            if direct_link:
                filename = self.extract_mediafire_filename(url) or os.path.basename(direct_link)
                full_path = os.path.join(current_path, filename)
                print(f"✅ Enlace directo: {direct_link}")
                print(f"💾 Guardar como: {full_path}")
                self.results.append((full_path, direct_link))
            else:
                print("❌ No se encontró el enlace de descarga (API). Probando HTML...")
                if self.try_mediafire_file_html(url, current_path):
                    return
                print("❌ No se encontró el enlace de descarga (HTML).")
                self.results.append((None, None))
            self.proceed_to_next()
        except Exception as e:
            print(f"❌ Error procesando archivo MediaFire (API): {e}")
            self.results.append((None, None))
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
            self.process_current_url()
        else:
            self.direct_links_ready.emit(self.results)
            self.close()

    def extract_mediafire_folder_key(self, url):
        match = re.search(r"/folder/([^/]+)", url)
        return match.group(1) if match else None

    def extract_mediafire_folder_name(self, url):
        match = re.search(r"/folder/[^/]+/([^/]+)", url)
        if match:
            return match.group(1).replace("_", " ")
        return "Subcarpeta"

    def extract_mediafire_quickkey(self, url):
        match = re.search(r"/file/([^/]+)", url)
        if match:
            return match.group(1)
        match = re.search(r"/download/([^/]+)", url)
        return match.group(1) if match else None

    def extract_mediafire_filename(self, url):
        match = re.search(r"/file/[^/]+/([^/]+)/", url)
        return match.group(1) if match else None

    def build_mediafire_file_url(self, quickkey, filename):
        safe_name = filename.replace(" ", "_")
        return f"https://www.mediafire.com/file/{quickkey}/{safe_name}/file"

    def build_mediafire_folder_url(self, folder_key, folder_name):
        safe_name = folder_name.replace(" ", "_")
        return f"https://www.mediafire.com/folder/{folder_key}/{safe_name}"

    def normalize_mediafire_items(self, value, key):
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = value.get(key)
            if isinstance(nested, list):
                return nested
            if isinstance(nested, dict):
                return [nested]
        return []

    def fetch_mediafire_folder_items(self, folder_key, content_type):
        import requests
        headers = {"User-Agent": "Mozilla/5.0"}
        items = []
        chunk = 1
        more_chunks = True
        while more_chunks:
            params = {
                "folder_key": folder_key,
                "content_type": content_type,
                "filter": "all",
                "response_format": "json",
                "chunk": chunk,
            }
            response = requests.get(
                "https://www.mediafire.com/api/1.5/folder/get_content.php",
                params=params,
                timeout=30,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()
            content = data.get("response", {}).get("folder_content", {})
            raw = content.get(content_type)
            if content_type == "files":
                items.extend(self.normalize_mediafire_items(raw, "file"))
            else:
                items.extend(self.normalize_mediafire_items(raw, "folder"))
            more_chunks = str(content.get("more_chunks", "")).lower() == "yes"
            chunk += 1
        return items

    def try_mediafire_folder_html(self, url, base_path):
        html = self.fetch_mediafire_html(url)
        if not html:
            return False
        try:
            self.handle_mediafire_folder(html, base_path)
            return True
        except Exception as e:
            print(f"❌ Error procesando carpeta MediaFire (HTML): {e}")
            return False

    def try_mediafire_file_html(self, url, current_path):
        html = self.fetch_mediafire_html(url)
        if not html:
            return False
        try:
            self.handle_mediafire_file(html, current_path)
            return True
        except Exception as e:
            print(f"❌ Error procesando archivo MediaFire (HTML): {e}")
            return False

    def fetch_mediafire_html(self, url):
        try:
            import requests
            headers = {"User-Agent": "Mozilla/5.0"}
            response = requests.get(url, timeout=30, headers=headers)
            response.raise_for_status()
            return response.text
        except Exception as e:
            print(f"❌ Error obteniendo HTML de MediaFire: {e}")
            return None

    def handle_mediafire_file_requests(self, url, current_path):
        if self.try_mediafire_file_html(url, current_path):
            return
        print("❌ No se encontró el enlace de descarga (HTML).")
        self.results.append((None, None))
        self.proceed_to_next()
