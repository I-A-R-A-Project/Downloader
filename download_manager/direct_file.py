import os, re, requests
from urllib.parse import urlparse
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
