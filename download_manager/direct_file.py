import os, re, requests
from urllib.parse import parse_qs, unquote, urlparse
from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal


SPECIAL_DOWNLOAD_HOSTS = {
    "mediafire.com",
    "4shared.com",
    "drive.google.com",
    "filecrypt.cc",
    "filecrypt.to",
    "rapidgator.net",
    "ddownload.com",
    "ddl.to",
    "fuckingfast.co",
    "datanodes.to",
    "megadb.net",
    "gofile.io",
}


def is_direct_file_url(url):
    parsed = urlparse((url or "").strip())
    host = (parsed.hostname or "").lower()
    if not parsed.scheme or not parsed.netloc or any(
        host == special_host or host.endswith(f".{special_host}")
        for special_host in SPECIAL_DOWNLOAD_HOSTS
    ):
        return False

    path = (parsed.path or "").rstrip("/")
    if path.lower().endswith(".php"):
        lower_path = path.lower()
        if lower_path.endswith("/mod_view.php") or lower_path.endswith("/enrol/index.php"):
            return False
    return bool(os.path.splitext(path)[1])


def build_download_path(base_path, *parts):
    segments = [segment for segment in (base_path, *parts) if segment]
    if not segments:
        return ""
    return os.path.normpath(os.path.join(*segments))


def extract_filename_from_headers(headers):
    content_disposition = headers.get("Content-Disposition") or headers.get("content-disposition") or ""
    if not content_disposition:
        return None

    filename = None
    filename_star = re.search(r"filename\*=(?:UTF-8''|)([^;]+)", content_disposition, re.IGNORECASE)
    if filename_star:
        filename = unquote(filename_star.group(1).strip().strip('"').strip("'"))
    else:
        filename_match = re.search(r'filename="?([^";]+)"?', content_disposition, re.IGNORECASE)
        if filename_match:
            filename = filename_match.group(1).strip()

    if filename:
        filename = os.path.basename(filename.replace("\\", "/"))
    return filename or None


def extract_filename_from_url(url):
    parsed = urlparse(url)
    dispositions = parse_qs(parsed.query).get("response-content-disposition", [])
    for disposition in dispositions:
        filename = extract_filename_from_headers({"content-disposition": unquote(disposition)})
        if filename:
            return filename
    return os.path.basename(unquote(parsed.path))


def is_generic_filename(filename):
    return filename.lower() in {"index.php", "index.html", "download.php", "download"}


def resolve_direct_filename(url, headers=None, cookies=None):
    request_headers = {"User-Agent": "Mozilla/5.0"}
    request_headers.update(headers or {})
    final_url = url
    filename = None

    try:
        response = requests.head(
            url,
            allow_redirects=True,
            timeout=15,
            headers=request_headers,
            cookies=cookies or {},
        )
        try:
            response.raise_for_status()
            final_url = response.url or url
            filename = extract_filename_from_headers(response.headers)
        finally:
            response.close()
    except Exception:
        pass

    if not filename:
        try:
            response = requests.get(
                url,
                stream=True,
                allow_redirects=True,
                timeout=15,
                headers=request_headers,
                cookies=cookies or {},
            )
            try:
                response.raise_for_status()
                final_url = response.url or final_url
                filename = extract_filename_from_headers(response.headers)
            finally:
                response.close()
        except Exception:
            pass

    source_filename = extract_filename_from_url(url)
    if filename and not is_generic_filename(filename):
        return filename
    if source_filename and os.path.splitext(source_filename)[1] and not is_generic_filename(source_filename):
        return source_filename

    final_filename = extract_filename_from_url(final_url)
    if final_filename and not is_generic_filename(final_filename):
        return final_filename

    return source_filename or filename or final_filename or "archivo_descargado"


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
