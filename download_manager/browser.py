import os
import re
import traceback
import uuid
from urllib.parse import urlparse
import requests
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEnginePage, QWebEngineProfile
from PyQt5.QtCore import QObject, QRunnable, QThreadPool, QUrl, QTimer, pyqtSignal, Qt
from bs4 import BeautifulSoup
from download_manager.gdrive_handler import (
    parse_gdrive_folder_id,
    parse_gdrive_file_id,
    resolve_gdrive_file,
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
}


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


def resolve_direct_filename(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    final_url = url
    filename = None

    try:
        response = requests.head(url, allow_redirects=True, timeout=15, headers=headers)
        final_url = response.url or url
        filename = extract_filename_from_headers(response.headers)
    except Exception:
        pass

    if not filename:
        try:
            response = requests.get(url, stream=True, allow_redirects=True, timeout=15, headers=headers)
            final_url = response.url or final_url
            filename = extract_filename_from_headers(response.headers)
            response.close()
        except Exception:
            pass

    if not filename:
        filename = os.path.basename(urlparse(final_url).path)

    if not filename:
        filename = "archivo_descargado"

    return filename


class DirectFileResolveSignals(QObject):
    finished = pyqtSignal(int, str, str, str)


class DirectFileResolveWorker(QRunnable):
    def __init__(self, index, url, current_path):
        super().__init__()
        self.index = index
        self.url = url
        self.current_path = current_path
        self.signals = DirectFileResolveSignals()

    def run(self):
        filename = resolve_direct_filename(self.url)
        self.signals.finished.emit(self.index, self.url, self.current_path, filename)


DIRECT_RESOLVE_THREAD_POOL = QThreadPool()
DIRECT_RESOLVE_THREAD_POOL.setMaxThreadCount(4)

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

class UniversalDownloader(QWebEngineView):
    direct_links_ready = pyqtSignal(list)

    def __init__(self, urls):
        super().__init__()
        profile = QWebEngineProfile(f"universal-downloader-{uuid.uuid4().hex[:8]}", self)
        profile.setPersistentCookiesPolicy(QWebEngineProfile.NoPersistentCookies)
        profile.setHttpCacheType(QWebEngineProfile.MemoryHttpCache)
        profile.downloadRequested.connect(self.on_download_requested)
        cookie_store = profile.cookieStore()
        cookie_store.cookieAdded.connect(self.on_cookie_added)
        cookie_store.cookieRemoved.connect(self.on_cookie_removed)
        cookie_store.loadAllCookies()
        self.profile = profile
        self.setPage(SilentPage(profile, self))
        self.resize(1200, 800)
        self.urls = []
        self.offscreen_results = []

        for entry in urls:
            url = entry.get("url", "")
            path = entry.get("path", "")
            password = entry.get("password", "")
            if url.startswith("magnet:?"):
                print(f"🔗 Magnet detectado: {url}")
                filename = f"{uuid.uuid4().hex[:8]}.magnet"
                self.offscreen_results.append((build_download_path(path, filename), url))
            elif url.endswith(".torrent") or "torrage" in url or "itorrents" in url:
                print(f"🔗 Torrent detectado: {url}")
                filename = url.split("/")[-1].split("?")[0]
                self.offscreen_results.append((build_download_path(path, filename), url))
            else:
                self.urls.append((url, path))

        self.current_index = 0
        self.results = []
        self._cookies = {}
        self._gdrive_click_attempts = 0
        self._gdrive_click_max_attempts = 10
        self._gdrive_waiting_download = False
        self._gdrive_folder_id = None
        self._gdrive_folder_path = ""
        self._active_direct_worker = None
        self._pending_direct_resolution = None
        self._filecrypt_wait_attempts = 0
        self._filecrypt_pending_batches = {}
        self._filecrypt_active_workers = []
        self._filecrypt_link_targets = {}
        self._filecrypt_link_wait_attempts = 0
        self._interactive_wait_attempts = 0
        self._interactive_download_path = ""

        self.setWindowTitle("Universal Downloader")
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.loadFinished.connect(self.on_load_finished)
        self.page().windowCloseRequested.connect(self.on_window_close_requested)
        self.renderProcessTerminated.connect(self.on_render_process_terminated)
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
            self.process_current_url()
        else:
            self.close()

    def current_source_url(self):
        if self.current_index >= len(self.urls):
            return ""
        return self.urls[self.current_index][0]

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
            if self.current_index >= len(self.urls):
                return
            print(self.urls[self.current_index])
            print(f"[{self.current_index+1}/{len(self.urls)}] Páginas cargadas...")
            if not ok:
                print("❌ La página no terminó de cargar correctamente.")
            QTimer.singleShot(1500, self.route_url_handling)
        except Exception:
            print("❌ Error en on_load_finished:")
            print(traceback.format_exc())

    def process_current_url(self):
        if not self.urls:
            self.direct_links_ready.emit(self.results)
            self.close()
            return
        url, path = self.urls[self.current_index]
        self._filecrypt_wait_attempts = 0
        self._filecrypt_link_wait_attempts = 0
        self._interactive_wait_attempts = 0
        self._interactive_download_path = ""
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
        self.load(QUrl(url))

    def route_url_handling(self):
        try:
            if self.current_index >= len(self.urls):
                return
            url, path = self.urls[self.current_index]
            if "mediafire.com" in url:
                self.handle_mediafire(url, path)
            elif self.is_filecrypt_url(url):
                self.handle_filecrypt(url, path)
            elif "4shared.com" in url:
                self.page().toHtml(lambda html: self.handle_4shared(html, path))
            elif "drive.google.com" in url:
                self.handle_gdrive(url, path)
            elif is_interactive_download_host(url):
                self.handle_interactive_download_host(url, path)
            elif self.is_direct_file_url(url):
                self.handle_direct_file(url, path)
            else:
                print("❌ Sitio no soportado.")
                self.results.append((None, None))
                self.proceed_to_next()
        except Exception:
            print("❌ Error en route_url_handling:")
            print(traceback.format_exc())

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
            self.urls.insert(insert_position, (row["link_url"], current_path))
        self.proceed_to_next()

    def handle_filecrypt_link_url(self, url, current_path):
        resolved_url = self._filecrypt_link_targets.pop(url, "")
        if resolved_url:
            print(f"✅ Filecrypt redirigió a: {resolved_url}")
            self.urls.insert(self.current_index + 1, (resolved_url, current_path))
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
            self.urls.insert(self.current_index + 1, (resolved_url, current_path))
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
        if "rapidgator.net" in host:
            self.try_click_rapidgator_free()
        self.setWindowTitle(f"Continuá manualmente en {host}")
        if self._interactive_wait_attempts % 10 == 1:
            print(f"⏳ Esperando acción manual en {host}...")
        QTimer.singleShot(1500, self.route_url_handling)

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
            print(f"✅ Click automático en Rapidgator free: {result.get('text', '')}")

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
                resolved = resolve_gdrive_file(url)
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

    def on_cookie_added(self, cookie):
        try:
            name = bytes(cookie.name()).decode("utf-8", errors="ignore")
            value = bytes(cookie.value()).decode("utf-8", errors="ignore")
            domain = cookie.domain() or ""
            path = cookie.path() or "/"
            self._cookies[(domain, path, name)] = {
                "name": name,
                "value": value,
                "domain": domain,
                "path": path,
            }
        except Exception:
            print("❌ Error almacenando cookie en UniversalDownloader:")
            print(traceback.format_exc())

    def on_cookie_removed(self, cookie):
        name = bytes(cookie.name()).decode("utf-8", errors="ignore")
        domain = cookie.domain() or ""
        path = cookie.path() or "/"
        self._cookies.pop((domain, path, name), None)

    def cookies_for_url(self, url):
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        path = parsed.path or "/"
        jar = {}
        for cookie in self._cookies.values():
            cookie_domain = (cookie["domain"] or "").lstrip(".").lower()
            cookie_path = cookie["path"] or "/"
            if cookie_domain and host != cookie_domain and not host.endswith(f".{cookie_domain}"):
                continue
            if not path.startswith(cookie_path):
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
                    "cookies": self.cookies_for_url(download_url),
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
                    "cookies": self.cookies_for_url(download_url),
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
            self.handle_mediafire_folder_api(url, path)
        elif "/file/" in url or "/download/" in url:
            self.handle_mediafire_file_requests(url, path)
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
            full_path = build_download_path(current_path, filename)
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
            QTimer.singleShot(0, self.process_current_url)
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

    def is_direct_file_url(self, url):
        parsed = urlparse(url)
        if is_interactive_download_host(url):
            return False
        path = parsed.path or ""
        ext = os.path.splitext(path)[1].lower()
        direct_exts = {
            ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz",
            ".iso", ".exe", ".msi", ".apk", ".pdf", ".cbz", ".cbr"
        }
        return ext in direct_exts

    def handle_direct_file(self, url, current_path):
        request = (self.current_index, url, current_path)
        self._pending_direct_resolution = request
        worker = DirectFileResolveWorker(*request)
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
