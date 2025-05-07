import os
import time
import threading
import requests
from PyQt5.QtCore import pyqtSignal, QObject

MAX_RETRIES = 100
RETRY_DELAY = 3
CHUNK_SIZE = 8192

download_semaphore = None

# Helper para emitir señales desde hilos
class DownloadSignals(QObject):
    progress = pyqtSignal(int, int)  # index, percentage
    finished = pyqtSignal(int)       # index

class FileDownloader(threading.Thread):
    def __init__(self, url, filename, index, signals):
        super().__init__()
        self.url = url
        self.filename = filename
        self.index = index
        self.signals = signals

    def run(self):
        with download_semaphore:  # Limita descargas simultáneas
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    downloaded = 0
                    mode = 'wb'
                    headers = {}

                    if os.path.exists(self.filename):
                        downloaded = os.path.getsize(self.filename)
                        headers['Range'] = f'bytes={downloaded}-'
                        mode = 'ab'

                    with requests.get(self.url, stream=True, headers=headers, timeout=15) as r:
                        total_length = r.headers.get('content-length')
                        if total_length is None:
                            total_length = 0
                        else:
                            total_length = int(total_length) + downloaded

                        dir_path = os.path.dirname(self.filename)
                        if dir_path:
                            os.makedirs(dir_path, exist_ok=True)
                        with open(self.filename, mode) as f:
                            for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                                if chunk:
                                    f.write(chunk)
                                    downloaded += len(chunk)
                                    if total_length:
                                        percent = int((downloaded / total_length) * 100)
                                        self.signals.progress.emit(self.index, percent)

                    self.signals.finished.emit(self.index)
                    return  # Éxito: salir

                except Exception as e:
                    print(f"[{self.index}] ❌ Error en intento {attempt}: {e}")
                    if attempt < MAX_RETRIES:
                        time.sleep(RETRY_DELAY * attempt)
                    else:
                        self.signals.finished.emit(self.index)

def set_max_parallel_downloads(n):
    global download_semaphore
    download_semaphore = threading.Semaphore(n)