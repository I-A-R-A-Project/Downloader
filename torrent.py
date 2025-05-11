import libtorrent as lt
import time
import os

# Ruta al archivo .torrent o magnet link
torrent_path = 'ruta/del/archivo.torrent'  # o usa un magnet link
save_path = './descargas'

# Sesión
ses = lt.session()
ses.listen_on(6881, 6891)

# Agrega el torrent
if torrent_path.startswith("magnet:"):
    params = {
        'save_path': save_path,
        'storage_mode': lt.storage_mode_t(2),
    }
    handle = lt.add_magnet_uri(ses, torrent_path, params)
else:
    info = lt.torrent_info(torrent_path)
    params = {
        'save_path': save_path,
        'storage_mode': lt.storage_mode_t(2),
        'ti': info
    }
    handle = ses.add_torrent(params)

print("Iniciando descarga...")

# Esperar metadata si es magnet
while not handle.has_metadata():
    time.sleep(1)

# Monitorear progreso
while handle.status().state != lt.torrent_status.seeding:
    s = handle.status()
    print(f"{s.progress * 100:.2f}% completado - {s.download_rate / 1000:.2f} kB/s")
    time.sleep(1)

print("Descarga completa.")