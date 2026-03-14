import re, os, uuid, requests
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
        self.visited_urls = set()
        self._load_finished = False
        self._load_index = None
        self._load_timeout_ms = 20000
        self._html_timeout_ms = 15000
        self._html_callback_pending = False

        print(f"[DEBUG] UniversalDownloader init with {len(urls)} entries")
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

        if not self.urls:
            QTimer.singleShot(100, lambda: self.direct_links_ready.emit(self.offscreen_results))
            return

        self.setWindowTitle("Universal Downloader")
        self.loadFinished.connect(self.on_load_finished)
        self.loadStarted.connect(self.on_load_started)
        # Defer processing until callers connect signals and call start()

    def start_processing(self):
        while self.current_index < len(self.urls):
            url, path = self.urls[self.current_index]
            if "mediafire.com" in url and ("/file/" in url or "/download/" in url):
                if self._handle_mediafire_file_via_requests(url, path):
                    self.current_index += 1
                    continue
                print("[WARN] MediaFire file requests handling failed. Skipping.")
                self.results.append((None, None))
                self.current_index += 1
                continue

            print(f"[DEBUG] Loading URL: {url}")
            self.visited_urls.add(url)
            self.load(QUrl(url))
            return

        print(f"[DEBUG] Completed. Results: {len(self.results)}")
        self.direct_links_ready.emit(self.results)
        self.close()

    def on_load_started(self):
        self._load_finished = False
        idx = self.current_index
        self._load_index = idx
        QTimer.singleShot(self._load_timeout_ms, lambda: self._on_load_timeout(idx))

    def _on_load_timeout(self, index):
        if self._load_finished:
            return
        if index != self.current_index:
            return
        url, path = self.urls[self.current_index]
        print(f"[WARN] Load timeout at idx={self.current_index} url={url}.")
        if "mediafire.com" in url:
            try:
                print("[DEBUG] Fallback: fetching MediaFire HTML via requests.")
                r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
                r.raise_for_status()
                if "/folder/" in url:
                    self.handle_mediafire_folder(r.text, path)
                    return
                if "/file/" in url or "/download/" in url:
                    self.handle_mediafire_file(r.text, path, url)
                    return
            except Exception as e:
                print(f"[ERROR] Fallback requests failed: {e}")
        self.results.append((None, None))
        self.proceed_to_next()

    def start(self):
        if self.urls:
            self.show()
            self.start_processing()
        else:
            self.close()

    def on_load_finished(self):
        if self._load_index is not None and self._load_index != self.current_index:
            print(f"[DEBUG] Ignoring stale loadFinished for idx={self._load_index}")
            return
        self._load_finished = True
        print(f"[DEBUG] on_load_finished idx={self.current_index} total={len(self.urls)}")
        print(self.urls[self.current_index])
        print(f"[{self.current_index+1}/{len(self.urls)}] Páginas cargadas...")
        QTimer.singleShot(1000, self.route_url_handling)

    def route_url_handling(self):
        url, path = self.urls[self.current_index]
        print(f"[DEBUG] route_url_handling url={url}")
        if "mediafire.com" in url:
            self.handle_mediafire(url, path)
        elif "4shared.com" in url:
            self._call_with_html_timeout(self.handle_4shared, path)
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
            self._call_with_html_timeout(self.handle_mediafire_folder, path)
        elif "/file/" in url or "/download/" in url:
            if not self._handle_mediafire_file_via_requests(url, path):
                self._call_with_html_timeout(self.handle_mediafire_file, path, url)
        else:
            print("❌ URL de MediaFire no reconocida.")
            self.results.append(None)
            self.proceed_to_next()

    def _handle_mediafire_file_via_requests(self, url, path):
        try:
            print("[DEBUG] MediaFire file via requests")
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
            r.raise_for_status()
            if "/file/" in url or "/download/" in url:
                self.handle_mediafire_file(r.text, path, url, auto_next=False)
                return True
        except Exception as e:
            print(f"[ERROR] MediaFire file requests failed: {e}")
        return False

    def _call_with_html_timeout(self, handler, *args):
        self._html_callback_pending = True

        def _on_html(html):
            if not self._html_callback_pending:
                return
            self._html_callback_pending = False
            handler(html, *args)

        self.page().toHtml(_on_html)
        QTimer.singleShot(
            self._html_timeout_ms,
            lambda: self._on_html_timeout(handler.__name__)
        )

    def _on_html_timeout(self, handler_name):
        if not self._html_callback_pending:
            return
        self._html_callback_pending = False
        print(f"[WARN] toHtml timeout in {handler_name}. Skipping.")
        self.results.append((None, None))
        self.proceed_to_next()

    def handle_mediafire_folder(self, html, base_path, auto_next=True):
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
            print(f"[DEBUG] base_path={base_path} subfolder_path={subfolder_path}")
            insert_position = self.current_index + 1
            existing_urls = {u for u, _ in self.urls}
            for link in reversed(file_links):
                if link in existing_urls or link in self.visited_urls:
                    continue
                self.urls.insert(insert_position, (link, subfolder_path))
                existing_urls.add(link)
            print(f"[DEBUG] Queue size after insert: {len(self.urls)}")
        else:
            print("❌ No se encontraron archivos en la carpeta.")
        if auto_next:
            self.proceed_to_next()

    def handle_mediafire_file(self, html, current_path, source_url=None, auto_next=True):
        print(f"[DEBUG] handle_mediafire_file url={source_url}")
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
            print("❌ No se encontró el enlace de descarga en HTML.")
            # Fallback: fetch via requests and re-parse
            if source_url:
                try:
                    r = requests.get(source_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
                    r.raise_for_status()
                    soup2 = BeautifulSoup(r.text, "html.parser")
                    button2 = soup2.find("a", {"id": "downloadButton"})
                    filename_tag2 = soup2.find("div", class_="filename")
                    if button2 and button2.has_attr("href"):
                        direct_link = button2["href"]
                        filename = filename_tag2.text.strip() if filename_tag2 else os.path.basename(direct_link)
                        full_path = os.path.join(current_path, filename)
                        print(f"✅ Enlace directo (fallback): {direct_link}")
                        print(f"💾 Guardar como: {full_path}")
                        self.results.append((full_path, direct_link))
                    else:
                        print("❌ Fallback tampoco encontró enlace.")
                        self.results.append((None, None))
                except Exception as e:
                    print(f"❌ Error en fallback MediaFire: {e}")
                    self.results.append((None, None))
            else:
                self.results.append((None, None))
        if auto_next:
            self.proceed_to_next()

    def proceed_to_next(self):
        self.current_index += 1
        if self.current_index < len(self.urls):
            print(f"[DEBUG] Advancing to idx={self.current_index} total={len(self.urls)}")
            self.start_processing()
        else:
            print(f"[DEBUG] Completed. Results: {len(self.results)}")
            self.direct_links_ready.emit(self.results)
            self.close()
