import os, requests, time
from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal
from config import DEFAULT_CONFIG, load_config


MAX_RETRIES = 100
RETRY_DELAY = 3
CHUNK_SIZE = 8192


class InvalidDownloadResponse(Exception):
    pass


def response_is_html(content_type, first_chunk):
    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    if media_type in {"text/html", "application/xhtml+xml"}:
        return True
    prefix = first_chunk[:1024].lstrip().lower()
    return any(
        marker in prefix
        for marker in (b"<!doctype html", b"<html", b"<head", b"<body")
    )


def file_is_html(path):
    try:
        with open(path, "rb") as handle:
            prefix = handle.read(1024)
    except OSError:
        return False
    return response_is_html("", prefix)


class DownloadSignals(QObject):
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(int, bool)
    cancelled = pyqtSignal(int)


class FileDownloader(QRunnable):
    def __init__(self, url, filename, index, signals, headers=None, cookies=None):
        super().__init__()
        self.url = url
        self.filename = filename
        self.index = index
        self.signals = signals
        self.headers = headers or {}
        self.cookies = cookies or {}
        self._cancelled = False
        self.error_text = ""

        QThreadPool.globalInstance().setMaxThreadCount(
            load_config().get("max_parallel_downloads", DEFAULT_CONFIG["max_parallel_downloads"])
        )

    def cancel(self):
        self._cancelled = True

    def run(self):
        for attempt in range(1, MAX_RETRIES + 1):
            if self._cancelled:
                return
            try:
                downloaded = 0
                mode = "wb"
                headers = dict(self.headers)
                extension = os.path.splitext(self.filename)[1].lower()
                expected_html = extension in {".html", ".htm"}

                if (
                    os.path.exists(self.filename)
                    and not (file_is_html(self.filename) and not expected_html)
                ):
                    downloaded = os.path.getsize(self.filename)
                    headers["Range"] = f"bytes={downloaded}-"
                    mode = "ab"

                with requests.get(
                    self.url,
                    stream=True,
                    headers=headers,
                    cookies=self.cookies,
                    timeout=15,
                ) as response:
                    response.raise_for_status()
                    if downloaded and response.status_code != 206:
                        downloaded = 0
                        mode = "wb"

                    chunks = iter(response.iter_content(chunk_size=CHUNK_SIZE))
                    first_chunk = b""
                    for chunk in chunks:
                        if chunk:
                            first_chunk = chunk
                            break
                    if not expected_html and response_is_html(
                        response.headers.get("content-type", ""),
                        first_chunk,
                    ):
                        raise InvalidDownloadResponse(
                            "El servidor devolvió una página HTML en vez del archivo. "
                            "La sesión puede haber expirado o Moodle no autorizó el enlace."
                        )

                    total_length = response.headers.get("content-length")
                    if total_length is None:
                        total_length = 0
                    else:
                        total_length = int(total_length) + downloaded

                    dir_path = os.path.dirname(self.filename)
                    if dir_path:
                        os.makedirs(dir_path, exist_ok=True)

                    with open(self.filename, mode) as f:
                        if first_chunk:
                            f.write(first_chunk)
                            downloaded += len(first_chunk)
                            if total_length:
                                percent = int((downloaded / total_length) * 100)
                                self.signals.progress.emit(self.index, percent)
                        for chunk in chunks:
                            if self._cancelled:
                                self.signals.cancelled.emit(self.index)
                                return
                            if chunk:
                                f.write(chunk)
                                downloaded += len(chunk)
                                if total_length:
                                    percent = int((downloaded / total_length) * 100)
                                    self.signals.progress.emit(self.index, percent)

                self.signals.finished.emit(self.index, True)
                return
            except InvalidDownloadResponse as exc:
                self.error_text = str(exc)
                print(f"[{self.index}] {self.error_text}")
                self.signals.finished.emit(self.index, False)
                return
            except Exception as exc:
                response = getattr(exc, "response", None)
                if response is not None and response.status_code:
                    self.error_text = f"Error HTTP {response.status_code}: {response.reason}"
                else:
                    self.error_text = str(exc)
                print(f"[{self.index}] Error en intento {attempt}: {exc}")
                if isinstance(exc, requests.exceptions.HTTPError):
                    status_code = exc.response.status_code if exc.response else 0
                    if status_code < 500 and status_code not in {408, 429}:
                        self.signals.finished.emit(self.index, False)
                        return
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY * attempt)
                else:
                    self.signals.finished.emit(self.index, False)
