import os
from qbittorrentapi import Client
import time
from PyQt5.QtCore import QObject, pyqtSignal

appclosed="""<!DOCTYPE HTML PUBLIC "-//IETF//DTD HTML 2.0//EN">
            <html><head>
            <title>404 Not Found</title>
            </head><body>
            <h1>Not Found</h1>
            <p>The requested URL was not found on this server.</p>
            </body></html>"""

def get_client():
    return Client(
        host='localhost:8080',
        username='admin',
        password='adminadmin'
    )

from PyQt5.QtCore import QRunnable, pyqtSignal, QObject

class TorrentUpdateSignals(QObject):
    result = pyqtSignal(list)
    error = pyqtSignal(str)

class TorrentUpdater(QRunnable):
    def __init__(self):
        super().__init__()
        self.signals = TorrentUpdateSignals()

    def run(self):
        try:
            client = get_client()
            client.auth_log_in()
            torrents = client.torrents_info()
            self.signals.result.emit(list(torrents))
        except Exception as e:
            self.signals.error.emit(str(e))

def add_torrent_file(file_path, save_path):
    client = get_client()
    try:
        client.auth_log_in()
        client.torrents_add(torrent_files=file_path, save_path=save_path)
        filename = os.path.basename(file_path)
        for _ in range(10):
            torrents = client.torrents_info()
            for t in torrents:
                if filename.lower() in t.name.lower():
                    return t.hash
            time.sleep(1)
    except Exception as e:
        if e==appclosed:
            print("qBittorrent no abierto, torrent no agregado")
        else:
            print(e)
    return None

def add_magnet_link(magnet_url, save_path):
    client = get_client()
    try:
        client.auth_log_in()
        client.torrents_add(urls=magnet_url, save_path=save_path)
        # Extraemos el nombre del magnet (campo dn)
        from urllib.parse import urlparse, parse_qs, unquote
        parsed = parse_qs(urlparse(magnet_url).query)
        name = unquote(parsed.get("dn", [None])[0]) if "dn" in parsed else None
        for _ in range(10):
            torrents = client.torrents_info()
            for t in torrents:
                if name and name.lower() in t.name.lower():
                    return t.hash
                # fallback: si no hay name, usamos el save_path
                if not name and t.save_path == save_path:
                    return t.hash
            time.sleep(1)
    except Exception as e:
        if e==appclosed:
            print("qBittorrent no abierto, torrent no agregado")
        else:
            print(e)

    return None

