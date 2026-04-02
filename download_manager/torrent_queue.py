import os, tempfile, requests
from PyQt5.QtCore import QObject, QRunnable, pyqtSignal
from download_manager.torrent import add_magnet_link, add_torrent_file

class TorrentProcessorSignals(QObject):
    finished = pyqtSignal()

class TorrentProcessor(QRunnable):
    def __init__(self, torrents, save_path):
        super().__init__()
        self.torrents = torrents
        self.save_path = save_path
        self.signals = TorrentProcessorSignals()

    def run(self):
        magnet_count = 0
        torrent_file_count = 0

        for entry in self.torrents:
            if isinstance(entry, dict):
                torrent_url = entry.get("url", "")
                target_path = entry.get("path") or self.save_path
            else:
                torrent_url = entry
                target_path = self.save_path

            if target_path:
                try:
                    os.makedirs(target_path, exist_ok=True)
                except Exception as exc:
                    print(f"No se pudo preparar la carpeta para torrent {torrent_url}: {exc}")
                    continue

            if torrent_url.startswith("magnet:?"):
                add_magnet_link(torrent_url, target_path)
                magnet_count += 1
            elif torrent_url.endswith(".torrent"):
                self.download_and_add_torrent(torrent_url, target_path)
                torrent_file_count += 1

        if magnet_count > 0:
            print(f"{magnet_count} magnets agregados en paralelo")
        if torrent_file_count > 0:
            print(f"{torrent_file_count} archivos .torrent procesados")

        self.signals.finished.emit()

    def download_and_add_torrent(self, torrent_url, save_path):
        try:
            response = requests.get(torrent_url, timeout=30)
            response.raise_for_status()

            with tempfile.NamedTemporaryFile(suffix=".torrent", delete=False) as tmp_file:
                tmp_file.write(response.content)
                tmp_file_path = tmp_file.name

            add_torrent_file(tmp_file_path, save_path)
            try:
                os.unlink(tmp_file_path)
            except Exception:
                pass
        except Exception as exc:
            print(f"Error descargando archivo torrent {torrent_url}: {exc}")
