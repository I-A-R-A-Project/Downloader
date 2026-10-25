import os, re, time, traceback, uuid, requests
from urllib.parse import parse_qs, unquote, urljoin, urlparse
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import (
    QWebEngineHttpRequest,
    QWebEnginePage,
    QWebEngineProfile,
)
from PyQt6.QtCore import QObject, QRunnable, QThreadPool, QUrl, QTimer, QDateTime, pyqtSignal, Qt
from PyQt6.QtNetwork import QNetworkCookie
from bs4 import BeautifulSoup
from download_manager.direct_file import is_direct_file_url, resolve_direct_filename
from download_manager.gdrive_handler import (
    parse_gdrive_folder_id, parse_gdrive_file_id, resolve_gdrive_file,
)
from web_common.downloads.browser_downloads import (
    cookies_for_url,
    request_cookie_jar,
)

FILECRYPT_HOSTS = {
    "filecrypt.cc",
    "www.filecrypt.cc",
    "filecrypt.to",
    "www.filecrypt.to",
}

INTERACTIVE_DOWNLOAD_HOSTS = {
    "rapidgator.net",
    "www.rapidgator.net",
    "ddownload.com",
    "www.ddownload.com",
    "ddl.to",
    "www.ddl.to",
    "fuckingfast.co",
    "www.fuckingfast.co",
    "datanodes.to",
    "www.datanodes.to",
    "megadb.net",
    "www.megadb.net",
    "gofile.io",
    "www.gofile.io",
}

AUTO_INTERACTIVE_DOWNLOAD_HOSTS = {
    "rapidgator.net",
    "www.rapidgator.net",
    "ddownload.com",
    "www.ddownload.com",
    "ddl.to",
    "www.ddl.to",
    "fuckingfast.co",
    "www.fuckingfast.co",
    "datanodes.to",
    "www.datanodes.to",
    "megadb.net",
    "www.megadb.net",
    "gofile.io",
    "www.gofile.io",
}

# VikingFile is intentionally not registered here for now.
# The page is not loading correctly in the embedded WebEngine flow, so the
# host remains disabled until the rendering/navigation issue is debugged.


def build_download_path(base_path, *parts):
    segments = [segment for segment in (base_path, *parts) if segment]
    if not segments:
        return ""
    return os.path.normpath(os.path.join(*segments))


def is_meaningful_external_url(url):
    if not url:
        return False
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host or host in FILECRYPT_HOSTS:
        return False
    if parsed.path and parsed.path not in {"", "/"}:
        return True
    if parsed.query or parsed.fragment:
        return True
    return False


def is_interactive_download_host(url):
    host = (urlparse(url).hostname or "").lower()
    return host in INTERACTIVE_DOWNLOAD_HOSTS


def find_moodle_pluginfile_url(html, base_url):
    soup = BeautifulSoup(html or "", "html.parser")
    for element in soup.find_all(["a", "iframe", "embed", "object"]):
        target = (
            element.get("href")
            or element.get("src")
            or element.get("data")
            or ""
        )
        target = urljoin(base_url, target)
        path = urlparse(target).path.lower()
        if "/pluginfile.php/" in path or path.endswith("/pluginfile.php"):
            return target
    return ""


def extract_filename_from_headers(headers):
    content_disposition = headers.get("Content-Disposition") or headers.get("content-disposition") or ""
    if not content_disposition:
        return None

    filename = None
    filename_star = re.search(r"filename\*=(?:UTF-8''|)([^;]+)", content_disposition, re.IGNORECASE)
    if filename_star:
        filename = filename_star.group(1).strip().strip('"').strip("'")
    else:
        filename_match = re.search(r'filename="?([^";]+)"?', content_disposition, re.IGNORECASE)
        if filename_match:
            filename = filename_match.group(1).strip()

    if filename:
        filename = os.path.basename(filename)
    return filename or None


def extract_fuckingfast_download_url(html):
    if not html:
        return ""

    patterns = [
        r'window\.open\(\s*"(https://fuckingfast\.co/dl/[^"]+)"',
        r"window\.open\(\s*'(https://fuckingfast\.co/dl/[^']+)'",
        r'https://fuckingfast\.co/dl/[A-Za-z0-9._~%+\-/]+',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            return match.group(1) if match.groups() else match.group(0)
    return ""


def extract_megadb_download_url(html):
    if not html:
        return ""

    patterns = [
        r'https?://[A-Za-z0-9.-]*megadb\.[A-Za-z.]+(?::\d+)?/d/[^"\'>\s]+',
        r'https?://fs\d+\.[A-Za-z0-9.-]+/d/[^"\'>\s]+',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            return match.group(0)
    return ""


def extract_gofile_download_url(html):
    if not html:
        return ""

    match = re.search(r'https?://[^"\'>\s]*gofile\.io/download/web/[^"\'>\s]+', html, re.IGNORECASE)
    return match.group(0) if match else ""


def extract_filename_from_url_candidate(url):
    if not url:
        return ""

    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    dispositions = query.get("response-content-disposition") or query.get("response-content-disposition".lower()) or []
    for disposition in dispositions:
        filename = extract_filename_from_headers({"content-disposition": unquote(disposition)})
        if filename:
            return filename

    path_name = os.path.basename(unquote(parsed.path))
    if path_name:
        return path_name
    return ""


class DirectFileResolveSignals(QObject):
    finished = pyqtSignal(int, str, str, str)


class DirectFileResolveWorker(QRunnable):
    def __init__(self, index, url, current_path, headers=None, cookies=None):
        super().__init__()
        self.index = index
        self.url = url
        self.current_path = current_path
        self.headers = headers or {}
        self.cookies = cookies or {}
        self.signals = DirectFileResolveSignals()

    def run(self):
        filename = resolve_direct_filename(
            self.url,
            headers=self.headers,
            cookies=self.cookies,
        )
        self.signals.finished.emit(self.index, self.url, self.current_path, filename)


DIRECT_RESOLVE_THREAD_POOL = QThreadPool()
DIRECT_RESOLVE_THREAD_POOL.setMaxThreadCount(4)


class HostResolveSignals(QObject):
    finished = pyqtSignal(object, object)
    error = pyqtSignal(object, str)


class GDriveResolveWorker(QRunnable):
    def __init__(self, request_id, url):
        super().__init__()
        self.request_id = request_id
        self.url = url
        self.signals = HostResolveSignals()

    def run(self):
        try:
            resolved = resolve_gdrive_file(self.url)
            self.signals.finished.emit(self.request_id, resolved)
        except Exception:
            self.signals.error.emit(self.request_id, traceback.format_exc())


class MediaFireResolveWorker(QRunnable):
    def __init__(self, request_id, mode, url):
        super().__init__()
        self.request_id = request_id
        self.mode = mode
        self.url = url
        self.signals = HostResolveSignals()

    def run(self):
        try:
            if self.mode == "folder":
                result = self.resolve_folder(self.url)
            else:
                result = self.resolve_file(self.url)
            self.signals.finished.emit(self.request_id, result)
        except Exception:
            self.signals.error.emit(self.request_id, traceback.format_exc())

    def resolve_folder(self, url):
        folder_key = self.extract_mediafire_folder_key(url)
        if not folder_key:
            return {"ok": False, "reason": "missing-folder-key"}

        all_links = []
        all_folders = []
        files = self.fetch_mediafire_folder_items(folder_key, "files")
        folders = self.fetch_mediafire_folder_items(folder_key, "folders")

        for file_item in files:
            quickkey = file_item.get("quickkey")
            filename = file_item.get("filename") or "archivo"
            if quickkey:
                all_links.append(self.build_mediafire_file_url(quickkey, filename))

        for folder_item in folders:
            sub_key = folder_item.get("folderkey")
            name = (folder_item.get("name") or "Subcarpeta").replace(" ", "_")
            if sub_key:
                all_folders.append(self.build_mediafire_folder_url(sub_key, name))

        return {
            "ok": True,
            "folder_name": self.extract_mediafire_folder_name(url),
            "links": all_links,
            "folders": all_folders,
        }

    def resolve_file(self, url):
        quickkey = self.extract_mediafire_quickkey(url)
        if not quickkey:
            return {"ok": False, "reason": "missing-quickkey"}

        headers = {"User-Agent": "Mozilla/5.0"}
        params = {"quick_key": quickkey, "response_format": "json"}

        try:
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
                return {
                    "ok": True,
                    "direct_link": direct_link,
                    "filename": self.extract_mediafire_filename(url) or os.path.basename(direct_link),
                }
        except Exception:
            pass

        html = self.fetch_mediafire_html(url)
        if not html:
            return {"ok": False, "reason": "html-unavailable"}

        soup = BeautifulSoup(html, "html.parser")
        button = soup.find("a", {"id": "downloadButton"})
        filename_tag = soup.find("div", class_="filename")
        if button and button.has_attr("href"):
            direct_link = button["href"]
            filename = filename_tag.text.strip() if filename_tag else os.path.basename(direct_link)
            return {"ok": True, "direct_link": direct_link, "filename": filename}

        return {"ok": False, "reason": "download-link-not-found"}

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

    def fetch_mediafire_html(self, url):
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, timeout=30, headers=headers)
        response.raise_for_status()
        return response.text


HOST_RESOLVE_THREAD_POOL = QThreadPool()
HOST_RESOLVE_THREAD_POOL.setMaxThreadCount(4)

class SilentPage(QWebEnginePage):
    def __init__(self, profile, owner):
        super().__init__(profile, owner)
        self.owner = owner

    def javaScriptConsoleMessage(self, level, message, lineNumber, sourceID):
        pass

    def createWindow(self, window_type):
        if self.owner and self.owner.should_block_popups():
            print("⛔ Popup bloqueado.")
            return None
        return super().createWindow(window_type)

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        if self.owner and not self.owner.should_allow_navigation(url, navigation_type, is_main_frame):
            print(f"⛔ Navegación bloqueada: {url.toString()}")
            return False
        return super().acceptNavigationRequest(url, navigation_type, is_main_frame)


class ResultBuffer(list):
    def __init__(self, on_append):
        super().__init__()
        self.on_append = on_append

    def append(self, item):
        super().append(item)
        if self.on_append:
            self.on_append(item)

    def extend(self, items):
        for item in items:
            self.append(item)


class UniversalDownloader(QWebEngineView):
    direct_links_ready = pyqtSignal(list)
    direct_link_found = pyqtSignal(object)

    def __init__(self, urls):
        super().__init__()
        profile = QWebEngineProfile(f"universal-downloader-{uuid.uuid4().hex[:8]}")
        profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        profile.downloadRequested.connect(self.on_download_requested)
        cookie_store = profile.cookieStore()
        cookie_store.cookieAdded.connect(self.on_cookie_added)
        cookie_store.cookieRemoved.connect(self.on_cookie_removed)
        self.profile = profile
        page = SilentPage(profile, self)
        page.destroyed.connect(profile.deleteLater)
        self.setPage(page)
        self.resize(1200, 800)
        self.urls = []
        self.offscreen_results = []

        for entry in urls:
            url = entry.get("url", "")
            path = entry.get("path", "")
            password = entry.get("password", "")
            auth_cookies = entry.get("auth_cookies") or []
            auth_headers = entry.get("auth_headers") or {}
            if url.startswith("magnet:?"):
                print(f"🔗 Magnet detectado: {url}")
                filename = f"{uuid.uuid4().hex[:8]}.magnet"
                self.offscreen_results.append((build_download_path(path, filename), url))
            elif url.endswith(".torrent") or "torrage" in url or "itorrents" in url:
                print(f"🔗 Torrent detectado: {url}")
                filename = url.split("/")[-1].split("?")[0]
                self.offscreen_results.append((build_download_path(path, filename), url))
            else:
                self.urls.append((url, path, auth_cookies, auth_headers))

        self.current_index = 0
        self.results = ResultBuffer(self._on_result_appended)
        self._cookies = {}
        self._auth_cookies = []
        self._pending_auth_cookies = set()
        self._waiting_for_auth_cookies = False
        self._auth_cookie_timer = QTimer(self)
        self._auth_cookie_timer.setSingleShot(True)
        self._auth_cookie_timer.timeout.connect(self._continue_after_cookie_timeout)
        self._gdrive_click_attempts = 0
        self._gdrive_click_max_attempts = 10
        self._gdrive_waiting_download = False
        self._gdrive_folder_id = None
        self._gdrive_folder_path = ""
        self._active_direct_worker = None
        self._pending_direct_resolution = None
        self._pending_gdrive_resolution = None
        self._pending_mediafire_resolution = None
        self._filecrypt_wait_attempts = 0
        self._filecrypt_pending_batches = {}
        self._filecrypt_active_workers = []
        self._filecrypt_link_targets = {}
        self._filecrypt_link_wait_attempts = 0
        self._interactive_wait_attempts = 0
        self._interactive_download_path = ""
        self._rapidgator_free_clicked_urls = set()
        self._ddownload_regular_clicked_urls = set()
        self._datanodes_free_clicked_urls = set()
        self._datanodes_started_urls = set()
        self._datanodes_free_retry_after = {}
        self._gofile_clicked_urls = set()

        self.setWindowTitle("Universal Downloader")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.loadFinished.connect(self.on_load_finished)
        self.page().windowCloseRequested.connect(self.on_window_close_requested)
        self.renderProcessTerminated.connect(self.on_render_process_terminated)
        self._started = False
        self._navigation_started = False
        self._closed = False
        self._completion_emitted = False
        cookie_store.loadAllCookies()

    def start(self):
        if self._started or self._closed:
            return
        self._started = True
        if not self.urls:
            QTimer.singleShot(0, lambda: self.emit_results(self.offscreen_results))
            self.close()
            return
        self._prepare_auth_cookies()

    def _current_auth_context(self):
        if self.current_index >= len(self.urls):
            return [], {}
        _url, _path, cookies, headers = self.urls[self.current_index]
        return cookies, headers

    def _prepare_auth_cookies(self):
        cookies, _headers = self._current_auth_context()
        self._auth_cookies = list(cookies)
        cookie_store = self.profile.cookieStore()
        cookies_to_set = []
        for record in self._auth_cookies:
            name = str(record.get("name") or "").encode("utf-8")
            value = str(record.get("value") or "").encode("utf-8")
            domain = str(record.get("domain") or "")
            if not name or not domain:
                continue
            cookie = QNetworkCookie(name, value)
            cookie.setDomain(domain)
            cookie.setPath(str(record.get("path") or "/"))
            cookie.setSecure(bool(record.get("secure")))
            cookie.setHttpOnly(bool(record.get("http_only")))
            cookie_path = cookie.path() or "/"
            if isinstance(cookie_path, bytes):
                cookie_path = cookie_path.decode("utf-8", errors="replace")
            expires_at = record.get("expires_at")
            if expires_at:
                cookie.setExpirationDate(
                    QDateTime.fromMSecsSinceEpoch(int(expires_at))
                )
            key = (
                domain,
                cookie_path,
                name.decode("utf-8", errors="replace"),
                value.decode("utf-8", errors="replace"),
            )
            self._pending_auth_cookies.add(key)
            origin_scheme = "https" if cookie.isSecure() else "http"
            cookies_to_set.append(
                (cookie, QUrl(f"{origin_scheme}://{domain.lstrip('.')}/"))
            )

        for cookie, origin in cookies_to_set:
            cookie_store.setCookie(cookie, origin)

        if self._pending_auth_cookies:
            self._waiting_for_auth_cookies = True
            self._auth_cookie_timer.start(5000)
        else:
            self._begin_navigation()

    def _begin_navigation(self):
        if self._navigation_started or self._closed:
            return
        self._waiting_for_auth_cookies = False
        self._auth_cookie_timer.stop()
        self._navigation_started = True
        self.process_current_url()

    def _continue_after_cookie_timeout(self):
        if self._pending_auth_cookies:
            print("⚠ No se confirmaron todas las cookies de sesión antes de resolver.")
        self._begin_navigation()

    def _on_result_appended(self, item):
        if not item:
            return
        if isinstance(item, tuple) and len(item) >= 2 and not all(item[:2]):
            return
        self.direct_link_found.emit(item)

    def current_source_url(self):
        if self.current_index >= len(self.urls):
            return ""
        return self.urls[self.current_index][0]

    def _insert_url(self, index, url, path):
        cookies, headers = self._current_auth_context()
        self.urls.insert(index, (url, path, cookies, headers))

    def should_block_popups(self):
        return self.is_filecrypt_url(self.current_source_url())

    def should_allow_navigation(self, url, navigation_type, is_main_frame):
        if not is_main_frame:
            return True

        current_source = self.current_source_url()
        if not self.is_filecrypt_url(current_source):
            return True

        target = url.toString()
        if target.startswith("about:") or target.startswith("data:"):
            return True

        target_host = (urlparse(target).hostname or "").lower()
        if not target_host:
            return True

        if target_host in FILECRYPT_HOSTS:
            return True

        if self.is_filecrypt_link_url(current_source):
            if is_meaningful_external_url(target):
                self.capture_filecrypt_link_target(current_source, target)
                return False
            return False

        return False

    def on_load_finished(self, ok=True):
        try:
            if self._closed or self.current_index >= len(self.urls):
                return
            source_url = self.current_source_url()
            print(self.urls[self.current_index][:2])
            print(f"[{self.current_index+1}/{len(self.urls)}] Páginas cargadas...")
            if not ok:
                print("❌ La página no terminó de cargar correctamente.")
            QTimer.singleShot(1500, lambda source_url=source_url: self.route_url_handling_for(source_url))
        except Exception:
            print("❌ Error en on_load_finished:")
            print(traceback.format_exc())

    def process_current_url(self):
        if self._closed:
            return
        if not self.urls:
            self.finish(self.results)
            return
        url, path = self.urls[self.current_index][:2]
        self._filecrypt_wait_attempts = 0
        self._filecrypt_link_wait_attempts = 0
        self._interactive_wait_attempts = 0
        self._interactive_download_path = ""
        self._datanodes_free_retry_after[url] = time.time() + 1.5
        if self.is_direct_file_url(url):
            self.handle_direct_file(url, path)
            return
        # Evitar cargar páginas de archivos MediaFire con WebEngine (causan crash)
        if "mediafire.com" in url and ("/file/" in url or "/download/" in url):
            self.handle_mediafire_file_requests(url, path)
            return
        self.show()
        self.raise_()
        self.activateWindow()
        _cookies, headers = self._current_auth_context()
        if headers:
            request = QWebEngineHttpRequest(QUrl(url))
            for name, value in headers.items():
                request.setHeader(name.encode("ascii"), value.encode("utf-8"))
            self.load(request)
        else:
            self.load(QUrl(url))

    def route_url_handling(self):
        try:
            if self._closed or self.current_index >= len(self.urls):
                return
            url, path = self.urls[self.current_index][:2]
            if "mediafire.com" in url:
                self.handle_mediafire(url, path)
            elif self.is_filecrypt_url(url):
                self.handle_filecrypt(url, path)
            elif "4shared.com" in url:
                self.page().toHtml(lambda html: self.handle_4shared(html, path))
            elif "drive.google.com" in url:
                self.handle_gdrive(url, path)
            elif "fuckingfast.co" in (urlparse(url).hostname or "").lower():
                self.page().toHtml(lambda html: self.handle_fuckingfast(url, path, html))
            elif is_interactive_download_host(url):
                self.handle_interactive_download_host(url, path)
            elif self.is_direct_file_url(url):
                self.handle_direct_file(url, path)
            elif self.is_moodle_mod_view_url(url):
                self.page().toHtml(
                    lambda html: self.handle_moodle_mod_view(url, path, html)
                )
            else:
                print("❌ Sitio no soportado.")
                self.results.append((None, None))
                self.proceed_to_next()
        except Exception:
            print("❌ Error en route_url_handling:")
            print(traceback.format_exc())

    def route_url_handling_for(self, source_url):
        if self._closed or source_url != self.current_source_url():
            return
        self.route_url_handling()

    @staticmethod
    def is_moodle_mod_view_url(url):
        return urlparse(url).path.lower().endswith("/mod_view.php")

    def handle_moodle_mod_view(self, source_url, current_path, html):
        if self._closed or source_url != self.current_source_url():
            return
        target = find_moodle_pluginfile_url(html, source_url)
        if not target:
            print("❌ Moodle no expuso un enlace pluginfile.php en la página del recurso.")
            self.results.append((None, None))
            self.proceed_to_next()
            return

        self.capture_interactive_direct_link(
            source_url,
            current_path,
            target,
        )

    def is_filecrypt_url(self, url):
        return (urlparse(url).hostname or "").lower() in FILECRYPT_HOSTS

    def is_filecrypt_link_url(self, url):
        parsed = urlparse(url)
        return self.is_filecrypt_url(url) and parsed.path.lower().startswith("/link/")

    def handle_filecrypt(self, url, current_path):
        if self.is_filecrypt_link_url(url):
            self.handle_filecrypt_link_url(url, current_path)
            return

        js = (
            "(() => {"
            "  const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();"
            "  const rows = Array.from(document.querySelectorAll('table tr.kwj3, table tbody tr'));"
            "  const results = [];"
            "  for (const row of rows) {"
            "    const cells = row.querySelectorAll('td');"
            "    const titleCell = cells.length > 1 ? cells[1] : null;"
            "    const hostAnchor = row.querySelector('a.external_link');"
            "    const button = row.querySelector('button.download');"
            "    if (!titleCell || !hostAnchor || !button) { continue; }"
            "    let code = '';"
            "    for (const attr of Array.from(button.attributes)) {"
            "      if (!attr.name.startsWith('data-')) { continue; }"
            "      const value = normalize(attr.value);"
            "      if (/^[A-Z0-9]{6,}$/.test(value)) { code = value; break; }"
            "    }"
            "    if (!code) { continue; }"
            "    const rawNode = titleCell.childNodes.length ? titleCell.childNodes[0] : null;"
            "    const rawTitle = rawNode ? rawNode.textContent : '';"
            "    const title = normalize(titleCell.getAttribute('title') || rawTitle || titleCell.textContent);"
            "    const host = normalize(hostAnchor.textContent || hostAnchor.href || '');"
            "    results.push({"
            "      title,"
            "      host,"
            "      link_url: `${location.origin}/Link/${code}.html`,"
            "    });"
            "  }"
            "  return { ready: results.length > 0, results };"
            "})()"
        )
        self.page().runJavaScript(js, lambda result: self.on_filecrypt_container_scanned(result, url, current_path))

    def on_filecrypt_container_scanned(self, result, source_url, current_path):
        rows = result.get("results") if isinstance(result, dict) else None
        if rows:
            if source_url in self._filecrypt_pending_batches:
                return
            print(f"✅ Filecrypt resuelto: {len(rows)} enlaces encontrados.")
            self.resolve_filecrypt_batch(source_url, current_path, rows)
            return

        self._filecrypt_wait_attempts += 1
        self.setWindowTitle("Filecrypt: resolvé el captcha para continuar")
        if self._filecrypt_wait_attempts % 10 == 1:
            print("⏳ Esperando resolución manual del captcha de Filecrypt...")
        QTimer.singleShot(1500, self.route_url_handling)

    def resolve_filecrypt_batch(self, source_url, current_path, rows):
        batch = {
            "rows": rows,
            "resolved_urls": [],
            "source_url": source_url,
            "path": current_path,
        }
        self._filecrypt_pending_batches[source_url] = batch
        insert_position = self.current_index + 1
        for row in reversed(rows):
            self._insert_url(insert_position, row["link_url"], current_path)
        self.proceed_to_next()

    def handle_filecrypt_link_url(self, url, current_path):
        resolved_url = self._filecrypt_link_targets.pop(url, "")
        if resolved_url:
            print(f"✅ Filecrypt redirigió a: {resolved_url}")
            self._insert_url(self.current_index + 1, resolved_url, current_path)
            self.proceed_to_next()
            return

        self.page().toHtml(lambda html: self.handle_filecrypt_link_html(url, current_path, html))

    def capture_filecrypt_link_target(self, link_url, target_url):
        if is_meaningful_external_url(target_url):
            self._filecrypt_link_targets[link_url] = target_url

    def handle_filecrypt_link_html(self, link_url, current_path, html):
        resolved_url = self.extract_external_url_from_html(html)
        if resolved_url:
            self.capture_filecrypt_link_target(link_url, resolved_url)
            print(f"✅ Filecrypt redirigió a: {resolved_url}")
            self._insert_url(self.current_index + 1, resolved_url, current_path)
            self.proceed_to_next()
            return

        self._filecrypt_link_wait_attempts += 1
        if self._filecrypt_link_wait_attempts % 10 == 1:
            print("⏳ Esperando redirección del mirror de Filecrypt...")
        if self._filecrypt_link_wait_attempts >= 40:
            print("❌ No se pudo resolver el enlace de Filecrypt.")
            self.proceed_to_next()
            return
        QTimer.singleShot(1000, self.route_url_handling)

    def extract_external_url_from_html(self, html):
        if not html:
            return ""

        specific_patterns = [
            r"""location(?:\.href)?\s*=\s*['"](https?://[^'"]+)['"]""",
            r"""window\.open\(['"](https?://[^'"]+)['"]""",
            r"""content=['"][^'"]*url=(https?://[^'">]+)""",
        ]

        for pattern in specific_patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if not match:
                continue
            candidate = match.group(1)
            if is_meaningful_external_url(candidate):
                return candidate

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all(["a", "form", "iframe", "meta"]):
            candidate = (
                tag.get("href")
                or tag.get("action")
                or tag.get("src")
                or tag.get("content")
                or ""
            )
            if "url=" in candidate.lower():
                parts = re.split(r"url=", candidate, flags=re.IGNORECASE)
                candidate = parts[-1] if parts else candidate
            if is_meaningful_external_url(candidate):
                return candidate
        return ""

    def handle_interactive_download_host(self, url, current_path):
        self._interactive_download_path = current_path
        self._interactive_wait_attempts += 1
        host = (urlparse(url).hostname or url)
        actual_url = self.url().toString()
        if self.try_capture_interactive_direct_url(url, current_path, actual_url):
            return

        if "megadb.net" in host:
            self.page().toHtml(lambda html: self.handle_megadb(url, current_path, html))
            return
        if "gofile.io" in host:
            self.page().toHtml(lambda html: self.handle_gofile(url, current_path, html))
            return
        if "rapidgator.net" in host and url not in self._rapidgator_free_clicked_urls:
            self.try_click_rapidgator_free()
        if "ddownload.com" in host and url not in self._ddownload_regular_clicked_urls:
            self.try_click_ddownload_regular()
        if "datanodes.to" in host and url not in self._datanodes_started_urls:
            self.try_click_datanodes_download()
        if host in AUTO_INTERACTIVE_DOWNLOAD_HOSTS:
            self.setWindowTitle(f"Resolviendo enlace en {host}")
        else:
            self.setWindowTitle(f"Continuá manualmente en {host}")
        if self._interactive_wait_attempts % 10 == 1 and host not in AUTO_INTERACTIVE_DOWNLOAD_HOSTS:
            print(f"⏳ Esperando acción manual en {host}...")
        QTimer.singleShot(1500, lambda url=url: self.route_url_handling_for(url))

    def is_likely_direct_download_url(self, url):
        if not url:
            return False
        if self.is_direct_file_url(url):
            return True
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if "cloudflarestorage.com" in host:
            return True
        if "gofile.io" in host and "/download/web/" in (parsed.path or "").lower():
            return True
        if "megadb." in host and "/d/" in (parsed.path or "").lower():
            return True
        return "response-content-disposition=" in (parsed.query or "").lower()

    def capture_interactive_direct_link(self, source_url, current_path, direct_url, filename=""):
        filename = filename or extract_filename_from_url_candidate(direct_url) or "archivo_descargado"
        save_target = build_download_path(current_path, filename)
        print(f"✅ Enlace directo ({urlparse(source_url).hostname or source_url}): {direct_url}")
        print(f"💾 Guardar como: {save_target}")
        self.results.append({
            "type": "direct",
            "path": save_target,
            "url": direct_url,
            "headers": {"User-Agent": "Mozilla/5.0", "Referer": source_url},
            "cookies": self.cookies_for_url(direct_url, include_auth=False),
        })
        self.proceed_to_next()

    def try_capture_interactive_direct_url(self, source_url, current_path, actual_url):
        if not self.is_likely_direct_download_url(actual_url):
            return False
        self.capture_interactive_direct_link(source_url, current_path, actual_url)
        return True

    def try_click_rapidgator_free(self):
        js = (
            "(() => {"
            "  const btn = document.querySelector('a.link.act-link.btn-free');"
            "  if (!btn) { return { clicked: false, reason: 'missing' }; }"
            "  const style = window.getComputedStyle(btn);"
            "  if (style.display === 'none' || style.visibility === 'hidden') {"
            "    return { clicked: false, reason: 'hidden' };"
            "  }"
            "  btn.click();"
            "  return { clicked: true, text: (btn.innerText || btn.textContent || '').trim() };"
            "})()"
        )
        self.page().runJavaScript(js, self.on_rapidgator_free_clicked)

    def on_rapidgator_free_clicked(self, result):
        if isinstance(result, dict) and result.get("clicked"):
            self._rapidgator_free_clicked_urls.add(self.current_source_url())
            print(f"✅ Click automático en Rapidgator free: {result.get('text', '')}")

    def try_click_ddownload_regular(self):
        js = (
            "(() => {"
            "  const btn = document.getElementById('downloadbtn');"
            "  if (!btn) { return { clicked: false, reason: 'missing' }; }"
            "  if (btn.disabled || btn.classList.contains('dk2-btn-disabled')) {"
            "    return { clicked: false, reason: 'disabled' };"
            "  }"
            "  if (btn.classList.contains('dk2-btn-blocked')) {"
            "    return { clicked: false, reason: 'blocked' };"
            "  }"
            "  btn.click();"
            "  return { clicked: true, text: (btn.innerText || btn.textContent || '').trim() };"
            "})()"
        )
        self.page().runJavaScript(js, self.on_ddownload_regular_clicked)

    def on_ddownload_regular_clicked(self, result):
        if isinstance(result, dict) and result.get("clicked"):
            self._ddownload_regular_clicked_urls.add(self.current_source_url())
            print(f"✅ Click automático en DDownload regular: {result.get('text', '')}")

    def try_click_datanodes_download(self):
        current_url = self.current_source_url()
        allow_free_click = (
            current_url
            and current_url not in self._datanodes_free_clicked_urls
            and time.time() >= self._datanodes_free_retry_after.get(current_url, 0)
        )
        js = (
            "(() => {"
            "  const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();"
            "  const clickElement = (el) => {"
            "    const rect = el.getBoundingClientRect();"
            "    const options = { bubbles: true, cancelable: true, view: window, clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2 };"
            "    el.dispatchEvent(new MouseEvent('mouseover', options));"
            "    el.dispatchEvent(new MouseEvent('mousedown', options));"
            "    el.dispatchEvent(new MouseEvent('mouseup', options));"
            "    el.dispatchEvent(new MouseEvent('click', options));"
            "    if (typeof el.click === 'function') { el.click(); }"
            "  };"
            "  const clickable = Array.from(document.querySelectorAll('button, a, [role=\"button\"], input[type=\"button\"], input[type=\"submit\"]'));"
            "  const visible = clickable.filter((el) => {"
            "    const style = window.getComputedStyle(el);"
            "    return style.display !== 'none' && style.visibility !== 'hidden' && !el.disabled;"
            "  });"
            "  const labels = visible.map((el) => normalize(el.innerText || el.textContent || el.value || ''));"
            "  const pick = (matcher) => visible.find((el) => matcher(normalize(el.innerText || el.textContent || el.value || '')));"
            "  const startBtn = pick((text) => text.includes('start download'));"
            "  if (startBtn) {"
            "    clickElement(startBtn);"
            "    return { clicked: true, phase: 'start', text: normalize(startBtn.innerText || startBtn.textContent || startBtn.value || '') };"
            "  }"
            f"  const allowFreeClick = {str(bool(allow_free_click)).lower()};"
            "  const freeBtn = pick((text) => text.includes('free download'));"
            "  if (allowFreeClick && freeBtn) {"
            "    clickElement(freeBtn);"
            "    return { clicked: true, phase: 'free', text: normalize(freeBtn.innerText || freeBtn.textContent || freeBtn.value || '') };"
            "  }"
            "  return { clicked: false, labels: labels.slice(0, 30) };"
            "})()"
        )
        self.page().runJavaScript(js, self.on_datanodes_download_clicked)

    def on_datanodes_download_clicked(self, result):
        if not isinstance(result, dict) or not result.get("clicked"):
            return

        current_url = self.current_source_url()
        phase = result.get("phase")
        text = result.get("text", "")
        if phase == "start":
            self._datanodes_started_urls.add(current_url)
            print(f"✅ Click automático en DataNodes start: {text}")
        elif phase == "free":
            self._datanodes_free_clicked_urls.add(current_url)
            print(f"✅ Click automático en DataNodes free: {text}")

    def handle_megadb(self, source_url, current_path, html):
        direct_link = extract_megadb_download_url(html)
        if direct_link:
            filename = extract_filename_from_url_candidate(direct_link)
            if not filename:
                soup = BeautifulSoup(html, "html.parser")
                filename_tag = soup.find("span", class_="dfilename")
                filename = filename_tag.get_text(" ", strip=True) if filename_tag else ""
            self.capture_interactive_direct_link(source_url, current_path, direct_link, filename)
            return

        if self._interactive_wait_attempts % 10 == 1:
            print("⏳ MegaDB: resolvé el captcha y hacé click en Download para continuar...")
        self.setWindowTitle("MegaDB: resolvé el captcha y hacé click en Download")
        QTimer.singleShot(1500, lambda url=source_url: self.route_url_handling_for(url))

    def handle_gofile(self, source_url, current_path, html):
        direct_link = extract_gofile_download_url(html)
        if direct_link:
            self.capture_interactive_direct_link(source_url, current_path, direct_link)
            return

        if source_url not in self._gofile_clicked_urls:
            self.try_click_gofile_download()

        if self._interactive_wait_attempts % 10 == 1:
            print("⏳ Esperando botón o enlace final de Gofile...")
        self.setWindowTitle("Resolviendo enlace en gofile.io")
        QTimer.singleShot(1500, lambda url=source_url: self.route_url_handling_for(url))

    def try_click_gofile_download(self):
        js = (
            "(() => {"
            "  const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();"
            "  const clickElement = (el) => {"
            "    const rect = el.getBoundingClientRect();"
            "    const options = { bubbles: true, cancelable: true, view: window, clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2 };"
            "    el.dispatchEvent(new MouseEvent('mouseover', options));"
            "    el.dispatchEvent(new MouseEvent('mousedown', options));"
            "    el.dispatchEvent(new MouseEvent('mouseup', options));"
            "    el.dispatchEvent(new MouseEvent('click', options));"
            "    if (typeof el.click === 'function') { el.click(); }"
            "  };"
            "  const elements = Array.from(document.querySelectorAll('a, button, [role=\"button\"]'));"
            "  const visible = elements.filter((el) => {"
            "    const style = window.getComputedStyle(el);"
            "    return style.display !== 'none' && style.visibility !== 'hidden' && !el.disabled;"
            "  });"
            "  const anchorWithHref = visible.find((el) => {"
            "    const href = el.getAttribute('href') || '';"
            "    return href.includes('/download/web/');"
            "  });"
            "  if (anchorWithHref) {"
            "    return { clicked: false, href: anchorWithHref.href || anchorWithHref.getAttribute('href') || '' };"
            "  }"
            "  const itemList = document.getElementById('filemanager_itemslist');"
            "  const specific = itemList ? Array.from(itemList.querySelectorAll('.item_download')).find((el) => {"
            "    const style = window.getComputedStyle(el);"
            "    return style.display !== 'none' && style.visibility !== 'hidden' && !el.disabled;"
            "  }) : null;"
            "  if (specific) {"
            "    const row = specific.closest('[data-item-id]');"
            "    clickElement(specific);"
            "    return {"
            "      clicked: true,"
            "      source: 'item_download',"
            "      itemId: row ? row.getAttribute('data-item-id') || '' : '',"
            "      text: normalize(specific.innerText || specific.textContent || specific.value || '')"
            "    };"
            "  }"
            "  const button = visible.find((el) => normalize(el.innerText || el.textContent || el.value || '').includes('download'));"
            "  if (!button) { return { clicked: false, reason: 'missing' }; }"
            "  clickElement(button);"
            "  return { clicked: true, source: 'generic', text: normalize(button.innerText || button.textContent || button.value || '') };"
            "})()"
        )
        self.page().runJavaScript(js, self.on_gofile_download_clicked)

    def on_gofile_download_clicked(self, result):
        if not isinstance(result, dict):
            return
        if result.get("href"):
            self.capture_interactive_direct_link(self.current_source_url(), self._interactive_download_path, result["href"])
            return
        if result.get("clicked"):
            current_url = self.current_source_url()
            self._gofile_clicked_urls.add(current_url)
            item_id = result.get("itemId", "")
            if item_id:
                print(f"✅ Click automático en Gofile item {item_id}: {result.get('text', '')}")
            else:
                print(f"✅ Click automático en Gofile: {result.get('text', '')}")

    def handle_4shared(self, html, current_path):
        soup = BeautifulSoup(html, "html.parser")
        download_button = soup.find("a", {"id": "freeDlButton"})
        if download_button and download_button.has_attr("href"):
            direct_link = download_button["href"]
            title_tag = soup.find("title")
            filename = title_tag.text.strip().split(" - ")[0] if title_tag else os.path.basename(direct_link)
            full_path = build_download_path(current_path, filename)
            print(f"✅ Enlace directo (4shared): {direct_link}")
            print(f"💾 Guardar como: {full_path}")
            self.results.append((full_path, direct_link))
        else:
            print("❌ No se encontró el enlace de descarga en 4shared.")
            self.results.append((None, None))
        self.proceed_to_next()

    def handle_fuckingfast(self, source_url, current_path, html):
        direct_link = extract_fuckingfast_download_url(html)
        if not direct_link:
            print("❌ No se encontró el enlace /dl/ en FuckingFast.")
            self.handle_interactive_download_host(source_url, current_path)
            return

        soup = BeautifulSoup(html, "html.parser")
        title_tag = soup.find("title")
        filename = (title_tag.get_text(" ", strip=True) if title_tag else "").strip()
        if not filename:
            meta_title = soup.find("meta", attrs={"name": "title"})
            filename = (meta_title.get("content", "") if meta_title else "").strip()
        if not filename:
            filename = os.path.basename(urlparse(direct_link).path) or "archivo_descargado"

        save_target = build_download_path(current_path, filename)
        print(f"✅ Enlace directo (FuckingFast): {direct_link}")
        print(f"💾 Guardar como: {save_target}")
        self.results.append({
            "type": "direct",
            "path": save_target,
            "url": direct_link,
            "headers": {"User-Agent": "Mozilla/5.0", "Referer": source_url},
            "cookies": self.cookies_for_url(direct_link, include_auth=False),
        })
        self.proceed_to_next()

    def handle_gdrive(self, url, current_path):
        try:
            folder_id = parse_gdrive_folder_id(url)
            file_id = parse_gdrive_file_id(url)

            if folder_id:
                print(f"📁 Google Drive folder detectada: {url}")
                self._gdrive_click_attempts = 0
                self._gdrive_waiting_download = True
                self._gdrive_folder_id = folder_id
                self._gdrive_folder_path = current_path
                QTimer.singleShot(3000, self.try_click_gdrive_download_all)
                return

            if file_id:
                request_id = (self.current_index, url, current_path)
                self._pending_gdrive_resolution = request_id
                worker = GDriveResolveWorker(request_id, url)
                worker.signals.finished.connect(self.on_gdrive_resolved)
                worker.signals.error.connect(self.on_gdrive_resolution_error)
                HOST_RESOLVE_THREAD_POOL.start(worker)
                return

            print("❌ No se pudo extraer el ID del archivo de Google Drive.")
            self.results.append((None, None))
            self.proceed_to_next()
        except Exception:
            print("❌ Error en handle_gdrive:")
            print(traceback.format_exc())
            self.results.append((None, None))
            self.proceed_to_next()

    def try_click_gdrive_download_all(self):
        if not self._gdrive_waiting_download:
            return
        js = (
            "(() => {"
            "  const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();"
            "  const buttons = Array.from(document.querySelectorAll('[role=\"button\"]'));"
            "  const target = buttons.find((btn) => normalize(btn.innerText || btn.textContent) === 'Descargar todo');"
            "  if (!target) {"
            "    return { clicked: false, buttons: buttons.slice(0, 40).map((btn) => normalize(btn.innerText || btn.textContent)) };"
            "  }"
            "  const rect = target.getBoundingClientRect();"
            "  const options = { bubbles: true, cancelable: true, view: window, clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2 };"
            "  target.dispatchEvent(new MouseEvent('mouseover', options));"
            "  target.dispatchEvent(new MouseEvent('mousedown', options));"
            "  target.dispatchEvent(new MouseEvent('mouseup', options));"
            "  target.dispatchEvent(new MouseEvent('click', options));"
            "  return { clicked: true, text: normalize(target.innerText || target.textContent), className: target.className };"
            "})()"
        )
        self.page().runJavaScript(js, self.on_gdrive_download_all_clicked)

    def on_gdrive_download_all_clicked(self, result):
        try:
            if isinstance(result, dict) and result.get("clicked"):
                print(f"✅ Click en 'Descargar todo': {result}")
                return

            self._gdrive_click_attempts += 1
            if self._gdrive_click_attempts < self._gdrive_click_max_attempts:
                QTimer.singleShot(1500, self.try_click_gdrive_download_all)
                return

            print(f"⚠️ No se encontró 'Descargar todo': {result}")
            self._gdrive_waiting_download = False
            self.results.append((None, None))
            self.proceed_to_next()
        except Exception:
            print("❌ Error en on_gdrive_download_all_clicked:")
            print(traceback.format_exc())
            self._gdrive_waiting_download = False
            self.results.append((None, None))
            self.proceed_to_next()

    def on_gdrive_resolved(self, request_id, resolved):
        if self._pending_gdrive_resolution != request_id:
            return

        self._pending_gdrive_resolution = None
        _, _, current_path = request_id
        if resolved:
            full_path = build_download_path(current_path, resolved["filename"])
            print(f"✅ Enlace directo (Google Drive): {resolved['download_url']}")
            print(f"💾 Guardar como: {full_path}")
            self.results.append({
                "type": "direct",
                "path": full_path,
                "url": resolved["download_url"],
                "headers": resolved["headers"],
                "cookies": resolved["cookies"],
            })
        else:
            print("❌ No se pudo resolver el archivo de Google Drive.")
            self.results.append((None, None))

        self.proceed_to_next()

    def on_gdrive_resolution_error(self, request_id, error_text):
        if self._pending_gdrive_resolution != request_id:
            return

        self._pending_gdrive_resolution = None
        print("❌ Error en handle_gdrive:")
        print(error_text)
        self.results.append((None, None))
        self.proceed_to_next()

    def on_cookie_added(self, cookie):
        try:
            name = bytes(cookie.name()).decode("utf-8", errors="ignore")
            value = bytes(cookie.value()).decode("utf-8", errors="ignore")
            domain = cookie.domain() or ""
            path = cookie.path() or "/"
            if isinstance(path, bytes):
                path = path.decode("utf-8", errors="replace")
            expires_at = None
            expiration = cookie.expirationDate()
            if not cookie.isSessionCookie() and expiration.isValid():
                expires_at = expiration.toMSecsSinceEpoch()
            self._cookies[(domain, path, name)] = {
                "name": name,
                "value": value,
                "domain": domain,
                "path": path,
                "secure": cookie.isSecure(),
                "expires_at": expires_at,
            }
            pending_key = (domain, path, name, value)
            if pending_key in self._pending_auth_cookies:
                self._pending_auth_cookies.discard(pending_key)
                if self._waiting_for_auth_cookies and not self._pending_auth_cookies:
                    self._begin_navigation()
        except Exception:
            print("❌ Error almacenando cookie en UniversalDownloader:")
            print(traceback.format_exc())

    def on_cookie_removed(self, cookie):
        name = bytes(cookie.name()).decode("utf-8", errors="ignore")
        domain = cookie.domain() or ""
        path = cookie.path() or "/"
        if isinstance(path, bytes):
            path = path.decode("utf-8", errors="replace")
        self._cookies.pop((domain, path, name), None)

    def cookies_for_url(self, url, include_auth=True):
        jar = {}
        auth_keys = set()
        if not include_auth:
            auth_keys = {
                (
                    cookie["domain"].lstrip(".").lower(),
                    cookie.get("path") or "/",
                    cookie["name"],
                )
                for cookie in cookies_for_url(self._auth_cookies, url)
            }
        for cookie in cookies_for_url(self._cookies.values(), url):
            cookie_key = (
                cookie["domain"].lstrip(".").lower(),
                cookie.get("path") or "/",
                cookie["name"],
            )
            if not include_auth and cookie_key in auth_keys:
                continue
            jar[cookie["name"]] = cookie["value"]
        return jar

    def on_download_requested(self, download):
        try:
            download_url = download.url().toString()
            filename = download.downloadFileName() or download.path() or f"{self._gdrive_folder_id or 'drive'}.zip"
            if os.path.basename(filename) != filename:
                filename = os.path.basename(filename)

            if self._gdrive_waiting_download:
                save_target = build_download_path(self._gdrive_folder_path, filename)
                print(f"✅ ZIP capturado desde Google Drive: {download_url}")
                self.results.append({
                    "type": "direct",
                    "path": save_target,
                    "url": download_url,
                    "headers": {"User-Agent": "Mozilla/5.0"},
                    "cookies": self.cookies_for_url(download_url, include_auth=False),
                })
                self._gdrive_waiting_download = False
                download.cancel()
                self.proceed_to_next()
                return

            current_url = self.current_source_url()
            if is_interactive_download_host(current_url):
                save_target = build_download_path(self._interactive_download_path, filename)
                print(f"✅ Descarga capturada desde navegador: {download_url}")
                self.results.append({
                    "type": "direct",
                    "path": save_target,
                    "url": download_url,
                    "headers": {"User-Agent": "Mozilla/5.0", "Referer": current_url},
                    "cookies": self.cookies_for_url(download_url, include_auth=False),
                })
                download.cancel()
                self.proceed_to_next()
                return

            if self.is_moodle_mod_view_url(current_url):
                current_path = self.urls[self.current_index][1]
                save_target = build_download_path(current_path, filename)
                _cookies, auth_headers = self._current_auth_context()
                headers = dict(auth_headers)
                headers["Referer"] = current_url
                print(f"✅ Descarga Moodle capturada: {download_url}")
                self.results.append({
                    "type": "direct",
                    "path": save_target,
                    "url": download_url,
                    "headers": headers,
                    "cookies": self.cookies_for_url(
                        download_url,
                        include_auth=False,
                    ),
                })
                download.cancel()
                self.proceed_to_next()
                return

            download.cancel()
        except Exception:
            print("❌ Error en on_download_requested de UniversalDownloader:")
            print(traceback.format_exc())
            self._gdrive_waiting_download = False
            self.results.append((None, None))
            self.proceed_to_next()

    def on_window_close_requested(self):
        if self._gdrive_waiting_download:
            return
        self.close()

    def on_render_process_terminated(self, status, exit_code):
        print(f"❌ QWebEngine render process terminated. status={status}, exit_code={exit_code}")
        if self._gdrive_waiting_download:
            self._gdrive_waiting_download = False
            self.results.append((None, None))
            self.proceed_to_next()

    def handle_mediafire(self, url, path):
        if "/folder/" in url:
            self.resolve_mediafire_folder_async(url, path)
        elif "/file/" in url or "/download/" in url:
            self.resolve_mediafire_file_async(url, path)
        else:
            print("❌ URL de MediaFire no reconocida.")
            self.results.append((None, None))
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
                subfolder_path = build_download_path(base_path, folder_name)
                print(f"📁 {len(all_links)} archivos encontrados en carpeta '{folder_name}'.")
                insert_position = self.current_index + 1
                for link in reversed(all_links + all_folders):
                    self._insert_url(insert_position, link, subfolder_path)
                self.proceed_to_next()
            else:
                print("❌ No se encontraron archivos en la carpeta (API). Probando HTML...")
                self.page().toHtml(lambda html: self.safe_handle_mediafire_folder(html, url, base_path))
        except Exception as e:
            print(f"❌ Error procesando carpeta MediaFire (API): {e}")
            self.results.append((None, None))
            self.proceed_to_next()

    def resolve_mediafire_folder_async(self, url, base_path):
        request_id = (self.current_index, "folder", url, base_path)
        self._pending_mediafire_resolution = request_id
        worker = MediaFireResolveWorker(request_id, "folder", url)
        worker.signals.finished.connect(self.on_mediafire_resolved)
        worker.signals.error.connect(self.on_mediafire_resolution_error)
        HOST_RESOLVE_THREAD_POOL.start(worker)

    def resolve_mediafire_file_async(self, url, current_path):
        request_id = (self.current_index, "file", url, current_path)
        self._pending_mediafire_resolution = request_id
        worker = MediaFireResolveWorker(request_id, "file", url)
        worker.signals.finished.connect(self.on_mediafire_resolved)
        worker.signals.error.connect(self.on_mediafire_resolution_error)
        HOST_RESOLVE_THREAD_POOL.start(worker)

    def on_mediafire_resolved(self, request_id, result):
        if self._pending_mediafire_resolution != request_id:
            return

        self._pending_mediafire_resolution = None
        _, mode, url, current_path = request_id

        if mode == "folder":
            if not result or not result.get("ok"):
                reason = result.get("reason") if isinstance(result, dict) else None
                if reason == "missing-folder-key":
                    print("❌ No se pudo extraer el folder_key de MediaFire.")
                    self.results.append((None, None))
                    self.proceed_to_next()
                    return
                print("❌ Error procesando carpeta MediaFire (API).")
                self.results.append((None, None))
                self.proceed_to_next()
                return

            all_links = result.get("links", [])
            all_folders = result.get("folders", [])
            if all_links or all_folders:
                folder_name = result.get("folder_name") or self.extract_mediafire_folder_name(url)
                subfolder_path = build_download_path(current_path, folder_name)
                print(f"📁 {len(all_links)} archivos encontrados en carpeta '{folder_name}'.")
                insert_position = self.current_index + 1
                for link in reversed(all_links + all_folders):
                    self._insert_url(insert_position, link, subfolder_path)
                self.proceed_to_next()
                return

            print("❌ No se encontraron archivos en la carpeta (API). Probando HTML...")
            self.page().toHtml(lambda html: self.safe_handle_mediafire_folder(html, url, current_path))
            return

        if not result or not result.get("ok"):
            reason = result.get("reason") if isinstance(result, dict) else None
            if reason == "missing-quickkey":
                print("❌ No se pudo extraer el quickkey de MediaFire.")
            else:
                print("❌ No se encontró el enlace de descarga (HTML).")
            self.results.append((None, None))
            self.proceed_to_next()
            return

        direct_link = result["direct_link"]
        filename = result["filename"]
        full_path = build_download_path(current_path, filename)
        print(f"✅ Enlace directo: {direct_link}")
        print(f"💾 Guardar como: {full_path}")
        self.results.append((full_path, direct_link))
        self.proceed_to_next()

    def on_mediafire_resolution_error(self, request_id, error_text):
        if self._pending_mediafire_resolution != request_id:
            return

        self._pending_mediafire_resolution = None
        _, mode, _, _ = request_id
        if mode == "folder":
            print("❌ Error procesando carpeta MediaFire (API):")
        else:
            print("❌ Error procesando archivo MediaFire:")
        print(error_text)
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
                full_path = build_download_path(current_path, filename)
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
        subfolder_path = build_download_path(base_path, folder_name)

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
                self._insert_url(insert_position, link, subfolder_path)
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
            full_path = build_download_path(current_path, filename)
            print(f"✅ Enlace directo: {direct_link}")
            print(f"💾 Guardar como: {full_path}")
            self.results.append((full_path, direct_link))
        else:
            print("❌ No se encontró el enlace de descarga.")
            self.results.append((None, None))
        self.proceed_to_next()

    def proceed_to_next(self):
        if self._closed:
            return
        self.current_index += 1
        if self.current_index < len(self.urls):
            QTimer.singleShot(0, self.process_current_url)
        else:
            self.finish(self.results)

    def emit_results(self, results):
        if self._completion_emitted or self._closed:
            return
        self._completion_emitted = True
        try:
            self.direct_links_ready.emit(results)
        except RuntimeError as error:
            print(f"❌ No se pudieron emitir resultados del navegador: {error}")

    def finish(self, results):
        if self._closed:
            return
        self.emit_results(results)
        self._closed = True
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
        self.resolve_mediafire_file_async(url, current_path)

    def is_direct_file_url(self, url):
        return is_direct_file_url(url)

    def handle_direct_file(self, url, current_path):
        request = (self.current_index, url, current_path)
        self._pending_direct_resolution = request
        auth_cookies, auth_headers = self._current_auth_context()
        cookie_jar = request_cookie_jar(auth_cookies, url)
        headers = {"User-Agent": "Mozilla/5.0"}
        headers.update(auth_headers)
        worker = DirectFileResolveWorker(
            *request,
            headers=headers,
            cookies=cookie_jar,
        )
        worker.signals.finished.connect(self.on_direct_file_resolved)
        self._active_direct_worker = worker
        DIRECT_RESOLVE_THREAD_POOL.start(worker)

    def on_direct_file_resolved(self, index, url, current_path, filename):
        if self._pending_direct_resolution != (index, url, current_path):
            return

        self._pending_direct_resolution = None
        self._active_direct_worker = None
        full_path = build_download_path(current_path, filename)
        print(f"✅ Enlace directo detectado: {url}")
        print(f"💾 Guardar como: {full_path}")
        self.results.append((full_path, url))
        self.proceed_to_next()

    def resolve_direct_filename(self, url):
        return resolve_direct_filename(url)

    def extract_filename_from_headers(self, headers):
        return extract_filename_from_headers(headers)
