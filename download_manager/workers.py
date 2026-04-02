import os, requests, time
from PyQt5.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal
from config import DEFAULT_CONFIG, load_config


MAX_RETRIES = 100
RETRY_DELAY = 3
CHUNK_SIZE = 8192


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

                if os.path.exists(self.filename):
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
                    total_length = response.headers.get("content-length")
                    if total_length is None:
                        total_length = 0
                    else:
                        total_length = int(total_length) + downloaded

                    dir_path = os.path.dirname(self.filename)
                    if dir_path:
                        os.makedirs(dir_path, exist_ok=True)

                    with open(self.filename, mode) as f:
                        for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
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
            except Exception as exc:
                print(f"[{self.index}] Error en intento {attempt}: {exc}")
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY * attempt)
                else:
                    self.signals.finished.emit(self.index, False)
