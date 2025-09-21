import os
import json
import requests
import subprocess
import time
import tempfile
import zipfile
import base64
from urllib.parse import urlparse, parse_qs, unquote
from PyQt5.QtCore import QRunnable, pyqtSignal, QObject

ARIA2_RPC_URL = "http://localhost:6800/jsonrpc"
ARIA2_SECRET = "aria2rpc"

class Aria2ClientError(Exception):
    pass

class TorrentUpdateSignals(QObject):
    result = pyqtSignal(list)
    error = pyqtSignal(str)

class TorrentUpdater(QRunnable):
    def __init__(self):
        super().__init__()
        self.signals = TorrentUpdateSignals()

    def run(self):
        try:
            client = Aria2Client()
            active_downloads = client.get_active_downloads()
            stopped_downloads = [d for d in client.get_stopped_downloads() 
                               if d.state not in ("complete", "removed") and d.progress < 1.0]
            all_downloads = active_downloads + stopped_downloads
            self.signals.result.emit(all_downloads)
        except Exception as e:
            self.signals.error.emit(str(e))

class Aria2Client:
    def __init__(self, url=ARIA2_RPC_URL, secret=ARIA2_SECRET):
        self.url = url
        self.secret = secret

    def send_rpc(self, method, params=None):
        if params is None:
            params = []
        
        if self.secret:
            params.insert(0, f"token:{self.secret}")

        payload = {
            "jsonrpc": "2.0",
            "id": "download_manager",
            "method": f"aria2.{method}",
            "params": params
        }
        
        headers = {'Content-Type': 'application/json'}
        
        try:
            response = requests.post(self.url, data=json.dumps(payload), headers=headers, timeout=10)
            response.raise_for_status()
            result = response.json()
            
            if "error" in result:
                raise Aria2ClientError(f"Aria2 RPC Error: {result['error']}")
            
            return result.get("result")
        except requests.exceptions.RequestException as e:
            raise Aria2ClientError(f"No se pudo conectar a Aria2: {e}")

    def is_running(self):
        try:
            self.send_rpc("getVersion")
            return True
        except:
            return False

    def find_aria2_executable(self):
        possible_paths = [
            "aria2c", "aria2c.exe", "./aria2c.exe", "./aria2/aria2c.exe",
            os.path.join(os.path.dirname(__file__), "aria2c.exe")
        ]
        
        for path in possible_paths:
            try:
                result = subprocess.run([path, "--version"], capture_output=True, timeout=5, check=True)
                if result.returncode == 0:
                    return path
            except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
                continue
        return None

    def start_aria2(self, download_dir=None):
        if self.is_running():
            return True

        aria2_path = self.find_aria2_executable()
        if not aria2_path:
            print("⚠️ Aria2 no encontrado. Intentando descarga automática...")
            aria2_path = self.download_aria2_if_needed()
            if not aria2_path:
                print("❌ No se pudo obtener Aria2")
                return False

        if download_dir is None:
            download_dir = os.path.expanduser("~/Downloads")

        aria2_cmd = [
            aria2_path, "--enable-rpc", "--rpc-listen-all", f"--rpc-secret={self.secret}",
            "--rpc-allow-origin-all", f"--dir={download_dir}", "--continue=true",
            "--max-connection-per-server=16", "--min-split-size=1M", "--split=16",
            "--daemon=true", "--enable-dht=true", "--bt-enable-lpd=true",
            "--bt-max-peers=50", "--seed-ratio=0.1", "--bt-detach-seed-only=true"
        ]

        try:
            subprocess.Popen(aria2_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            print("⏳ Esperando que Aria2 inicie...")
            for i in range(15):
                time.sleep(1)
                if self.is_running():
                    print(f"✅ Aria2 iniciado correctamente en el intento {i+1}")
                    return True
                print(f"   Intento {i+1}/15...", end="\r")
            
            print("\n❌ Aria2 no respondió después de 15 segundos")
            return False
        except Exception as e:
            print(f"❌ Error al iniciar Aria2: {e}")
            return False

    def download_aria2_if_needed(self):
        if os.name != 'nt':
            return None
        
        try:
            aria2_dir = os.path.join(os.path.dirname(__file__), "aria2")
            aria2_exe = os.path.join(aria2_dir, "aria2c.exe")
            
            if os.path.exists(aria2_exe):
                try:
                    subprocess.run([aria2_exe, "--version"], capture_output=True, timeout=5, check=True)
                    return aria2_exe
                except:
                    pass
            
            print("📥 Descargando Aria2...")
            
            url = "https://github.com/aria2/aria2/releases/download/release-1.36.0/aria2-1.36.0-win-64bit-build1.zip"
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            
            os.makedirs(aria2_dir, exist_ok=True)
            
            with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp_file:
                tmp_file.write(response.content)
                tmp_file.flush()
                
                with zipfile.ZipFile(tmp_file.name, 'r') as zip_ref:
                    for member in zip_ref.namelist():
                        if member.endswith('aria2c.exe'):
                            with zip_ref.open(member) as source, open(aria2_exe, 'wb') as target:
                                target.write(source.read())
                            break
                
                os.unlink(tmp_file.name)
            
            if os.path.exists(aria2_exe):
                print("✅ Aria2 descargado correctamente")
                return aria2_exe
            else:
                print("❌ Error extrayendo Aria2")
                return None
                
        except Exception as e:
            print(f"❌ Error descargando Aria2: {e}")
            return None

    def add_magnet(self, magnet_url, save_path=None):
        uris = [magnet_url]
        options = {}
        if save_path:
            options["dir"] = save_path
        
        params = [uris]
        if options:
            params.append(options)
            
        try:
            gid = self.send_rpc("addUri", params)
            return gid
        except Aria2ClientError as e:
            print(f"Error agregando magnet: {e}")
            return None

    def add_torrent_file(self, torrent_path, save_path=None):
        if not os.path.exists(torrent_path):
            print(f"Archivo torrent no encontrado: {torrent_path}")
            return None
            
        try:
            with open(torrent_path, "rb") as f:
                torrent_data = base64.b64encode(f.read()).decode()

            params = [torrent_data]
            options = {}
            if save_path:
                options["dir"] = save_path
            
            if options:
                params.append([], options)
            else:
                params.append([])

            gid = self.send_rpc("addTorrent", params)
            return gid
        except Exception as e:
            print(f"Error agregando archivo torrent: {e}")
            return None

    def get_download_status(self, gid):
        try:
            fields = ["gid", "status", "totalLength", "completedLength", 
                     "downloadSpeed", "files", "followedBy", "following", "bittorrent"]
            status = self.send_rpc("tellStatus", [gid, fields])
            return self._format_download_info(status)
        except Aria2ClientError:
            return None

    def get_active_downloads(self):
        try:
            fields = ["gid", "status", "totalLength", "completedLength", 
                     "downloadSpeed", "files", "followedBy", "following", "bittorrent"]
            downloads = self.send_rpc("tellActive", [fields])
            return [self._format_download_info(d) for d in downloads]
        except Aria2ClientError:
            return []

    def get_stopped_downloads(self, count=10):
        try:
            fields = ["gid", "status", "totalLength", "completedLength", 
                     "downloadSpeed", "files", "followedBy", "following", "bittorrent"]
            downloads = self.send_rpc("tellStopped", [0, count, fields])
            return [self._format_download_info(d) for d in downloads]
        except Aria2ClientError:
            return []

    def _format_download_info(self, download):
        gid = download.get("gid")
        status = download.get("status", "unknown")
        total_length = int(download.get("totalLength", 0))
        completed_length = int(download.get("completedLength", 0))
        download_speed = int(download.get("downloadSpeed", 0))
        files = download.get("files", [])
        followed_by = download.get("followedBy", [])
        bittorrent = download.get("bittorrent", {})

        progress = 0.0
        if total_length > 0:
            progress = completed_length / total_length

        name = "Unknown"
        if files:
            first_file = files[0]
            file_path = first_file.get("path", "")
            if file_path:
                name = os.path.basename(file_path)
        
        if bittorrent and "info" in bittorrent:
            torrent_name = bittorrent["info"].get("name")
            if torrent_name:
                name = torrent_name

        class DownloadInfo:
            def __init__(self, gid, name, status, progress, total_length, 
                        completed_length, download_speed, followed_by):
                self.hash = gid
                self.gid = gid
                self.name = name
                self.state = self._map_aria2_status(status)
                self.progress = progress
                self.total_size = total_length
                self.completed = completed_length
                self.dlspeed = download_speed
                self.followed_by = followed_by
                self.save_path = os.path.dirname(files[0].get("path", "")) if files else ""

            def _map_aria2_status(self, aria2_status):
                mapping = {
                    "active": "downloading", "waiting": "queuedDL", "paused": "pausedDL",
                    "error": "error", "complete": "uploading", "removed": "error"
                }
                return mapping.get(aria2_status, aria2_status)

        return DownloadInfo(gid, name, status, progress, total_length, 
                          completed_length, download_speed, followed_by)

    def remove_download(self, gid, force=False):
        try:
            method = "forceRemove" if force else "remove"
            return self.send_rpc(method, [gid])
        except Aria2ClientError as e:
            print(f"Error removiendo descarga: {e}")
            return False

    def pause_download(self, gid):
        try:
            return self.send_rpc("pause", [gid])
        except Aria2ClientError as e:
            print(f"Error pausando descarga: {e}")
            return False

    def unpause_download(self, gid):
        try:
            return self.send_rpc("unpause", [gid])
        except Aria2ClientError as e:
            print(f"Error reanudando descarga: {e}")
            return False

def ensure_aria2_running(download_dir=None):
    client = Aria2Client()
    if not client.is_running():
        print("Iniciando Aria2...")
        if client.start_aria2(download_dir):
            print("✅ Aria2 iniciado correctamente")
            return True
        else:
            print("❌ No se pudo iniciar Aria2")
            return False
    return True

def get_client():
    client = Aria2Client()
    if not client.is_running():
        ensure_aria2_running()
    return client

def add_magnet_link(magnet_url, save_path):
    client = get_client()
    if not client.is_running():
        print("Aria2 no está corriendo, magnet no agregado")
        return None
    
    gid = client.add_magnet(magnet_url, save_path)
    if gid:
        print(f"Magnet agregado con GID: {gid}")
        
        for _ in range(10):
            download_info = client.get_download_status(gid)
            if download_info:
                return gid
            time.sleep(1)
    
    return gid

def add_torrent_file(file_path, save_path):
    client = get_client()
    if not client.is_running():
        print("Aria2 no está corriendo, torrent no agregado")
        return None
    
    gid = client.add_torrent_file(file_path, save_path)
    if gid:
        print(f"Torrent agregado con GID: {gid}")
        
        for _ in range(10):
            download_info = client.get_download_status(gid)
            if download_info:
                return gid
            time.sleep(1)
    
    return gid

