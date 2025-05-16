import os
from qbittorrentapi import Client
import time

def get_client():
    return Client(
        host='localhost:8080',
        username='admin',
        password='adminadmin'
    )

def add_torrent_file(file_path, save_path):
    client = get_client()
    client.auth_log_in()
    client.torrents_add(torrent_files=file_path, save_path=save_path)

    filename = os.path.basename(file_path)
    for _ in range(10):  # Esperamos hasta 10 segundos
        torrents = client.torrents_info()
        for t in torrents:
            if filename.lower() in t.name.lower():
                return t.hash
        time.sleep(1)
    return None


def add_magnet_link(magnet_url, save_path):
    client = get_client()
    client.auth_log_in()

    # Agregamos el magnet
    client.torrents_add(urls=magnet_url, save_path=save_path)

    # Extraemos el nombre del magnet (campo dn)
    from urllib.parse import urlparse, parse_qs, unquote
    parsed = parse_qs(urlparse(magnet_url).query)
    name = unquote(parsed.get("dn", [None])[0]) if "dn" in parsed else None

    # Esperamos a que el torrent aparezca
    for _ in range(10):  # Hasta 10 intentos
        torrents = client.torrents_info()
        for t in torrents:
            if name and name.lower() in t.name.lower():
                return t.hash
            # fallback: si no hay name, usamos el save_path
            if not name and t.save_path == save_path:
                return t.hash
        time.sleep(1)

    return None  # No encontrado

