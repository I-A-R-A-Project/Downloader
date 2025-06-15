import sys
import json
import requests
from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QPushButton, QLabel, QLineEdit, QProgressBar, QMessageBox
from PyQt5.QtCore import QTimer

ARIA2_RPC_URL = "http://localhost:6800/jsonrpc"

class Aria2Downloader(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Aria2 Magnet Downloader")

        self.layout = QVBoxLayout(self)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Pega un magnet link aquí")
        self.start_btn = QPushButton("Iniciar descarga")
        self.progress = QProgressBar()
        self.status = QLabel("Esperando...")
        self.layout.addWidget(self.input)
        self.layout.addWidget(self.start_btn)
        self.layout.addWidget(self.progress)
        self.layout.addWidget(self.status)

        self.start_btn.clicked.connect(self.start_download)
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_progress)

        self.gid = None

    def send_rpc(self, method, params=None):
        payload = {
            "jsonrpc": "2.0",
            "id": "qwer",
            "method": f"aria2.{method}",
            "params": params or []
        }
        try:
            r = requests.post(ARIA2_RPC_URL, data=json.dumps(payload))
            return r.json().get("result")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo conectar a aria2:\n{e}")
            return None

    def start_download(self):
        magnet = self.input.text().strip()
        if not magnet.startswith("magnet:"):
            QMessageBox.warning(self, "Error", "No es un magnet válido.")
            return

        result = self.send_rpc("addUri", [[magnet]])
        if result:
            self.gid = result
            self.status.setText("Descargando...")
            self.timer.start(1000)

    def update_progress(self):
        if not self.gid:
            return

        status = self.send_rpc("tellStatus", [self.gid, ["completedLength", "totalLength", "status", "downloadSpeed", "files", "followedBy"]])
        if not status:
            return

        if status.get("status") == "complete" and status.get("followedBy"):
            new_gid = status["followedBy"][0]
            print(f"Metadata completa, siguiendo nueva descarga con gid: {new_gid}")
            self.gid = new_gid
            return

        total = int(status.get("totalLength", 0))
        completed = int(status.get("completedLength", 0))
        speed = int(status.get("downloadSpeed", 0))
        state = status.get("status")

        if total == 0:
            self.progress.setValue(0)
            self.status.setText("Obteniendo metadata...")
            return

        percent = int((completed / total) * 100)
        self.progress.setValue(percent)
        self.status.setText(f"{percent}% - {speed / 1024:.1f} KB/s")

        if state == "complete":
            self.status.setText("¡Descarga completa!")
            self.timer.stop()

    def find_followup_gid(self):
        downloads = self.send_rpc("tellActive") or []
        for item in downloads:
            if item["followedBy"]:
                return item["followedBy"][0]
        return None

if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = Aria2Downloader()
    win.show()
    sys.exit(app.exec_())
