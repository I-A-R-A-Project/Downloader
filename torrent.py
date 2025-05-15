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
    res = client.torrents_add(torrent_files=file_path, save_path=save_path)
    time.sleep(1)
    # Buscamos el torrent recién agregado
    torrents = client.torrents_info()
    for t in torrents:
        if t.save_path == save_path and t.name in file_path:
            return t.hash
    return None

def add_magnet_link(magnet_url, save_path):
    client = get_client()
    client.auth_log_in()
    res = client.torrents_add(urls=magnet_url, save_path=save_path)
    time.sleep(1)
    torrents = client.torrents_info()
    for t in torrents:
        if t.magnet_uri == magnet_url:
            return t.hash
    return None
