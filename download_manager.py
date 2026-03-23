import os, sys, json, time
from PyQt5.QtCore import QObject, QIODevice, QSharedMemory, QSystemSemaphore, QTimer, pyqtSignal
from PyQt5.QtNetwork import QLocalServer, QLocalSocket
from PyQt5.QtWidgets import QApplication, QMessageBox
from ui import DownloadWindow
from torrent import ensure_aria2_running

SERVER_NAME = "MediaSearchPrototype.DownloadManager"


class SingleInstanceBridge(QObject):
    entries_received = pyqtSignal(list)
    focus_requested = pyqtSignal()

    def __init__(self, server_name, parent=None):
        super().__init__(parent)
        self.server_name = server_name
        self.server = None
        self._buffers = {}
        self._owns_primary = False
        self._memory = QSharedMemory(f"{server_name}.Memory", self)
        self._memory_guard = QSystemSemaphore(f"{server_name}.Semaphore", 1)

    def claim_primary(self):
        if self._owns_primary:
            return True

        self._memory_guard.acquire()
        try:
            if self._memory.attach():
                self._memory.detach()
                return False

            if self._memory.create(1):
                self._owns_primary = True
                return True

            if self._memory.error() == QSharedMemory.AlreadyExists:
                return False

            if self._memory.attach():
                self._memory.detach()
                return False

            raise RuntimeError(self._memory.errorString())
        finally:
            self._memory_guard.release()

    def send_to_primary(self, entries, attempts=20, wait_ms=250):
        payload = {
            "entries": entries,
            "focus_only": not entries,
        }
        data = json.dumps(payload).encode("utf-8") + b"\n"
        for attempt in range(attempts):
            socket = QLocalSocket(self)
            socket.connectToServer(self.server_name, QIODevice.ReadWrite)
            if socket.waitForConnected(wait_ms):
                if socket.write(data) == -1:
                    socket.abort()
                    socket.deleteLater()
                    return False
                socket.flush()
                if not socket.waitForBytesWritten(wait_ms * 2):
                    socket.abort()
                    socket.deleteLater()
                    return False
                if not socket.waitForReadyRead(wait_ms * 2):
                    socket.abort()
                    socket.deleteLater()
                    return False
                ack = bytes(socket.readAll()).strip()
                socket.disconnectFromServer()
                if socket.state() != QLocalSocket.UnconnectedState:
                    socket.waitForDisconnected(wait_ms)
                socket.deleteLater()
                return ack == b"ok"

            socket.abort()
            socket.deleteLater()
            if attempt + 1 < attempts:
                time.sleep(wait_ms / 1000.0)

        return False

    def start_listening(self):
        if self.server is not None:
            return True

        server = QLocalServer(self)
        if server.listen(self.server_name):
            self.server = server
            self.server.newConnection.connect(self._handle_new_connection)
            return True

        if self._can_connect_to_primary():
            server.deleteLater()
            return False

        QLocalServer.removeServer(self.server_name)
        server.deleteLater()
        server = QLocalServer(self)
        if server.listen(self.server_name):
            self.server = server
            self.server.newConnection.connect(self._handle_new_connection)
            return True

        server.deleteLater()
        return False

    def close(self):
        if self.server is None:
            pass
        else:
            self.server.close()
            self.server.deleteLater()
            self.server = None
            QLocalServer.removeServer(self.server_name)

        if self._owns_primary and self._memory.isAttached():
            self._memory.detach()
        self._owns_primary = False

    def _can_connect_to_primary(self):
        probe = QLocalSocket(self)
        probe.connectToServer(self.server_name, QIODevice.WriteOnly)
        connected = probe.waitForConnected(300)
        if connected:
            probe.disconnectFromServer()
            if probe.state() != QLocalSocket.UnconnectedState:
                probe.waitForDisconnected(100)
        return connected

    def _handle_new_connection(self):
        while self.server and self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            if socket is None:
                continue
            self._buffers[socket] = bytearray()
            socket.readyRead.connect(lambda s=socket: self._read_socket(s))
            socket.disconnected.connect(lambda s=socket: self._cleanup_socket(s))
            self._read_socket(socket)

    def _read_socket(self, socket):
        buffer = self._buffers.get(socket)
        if buffer is None:
            return
        buffer.extend(bytes(socket.readAll()))
        while b"\n" in buffer:
            payload, _, remaining = buffer.partition(b"\n")
            self._buffers[socket] = bytearray(remaining)
            self._process_message(socket, bytes(payload))
            buffer = self._buffers.get(socket)
            if buffer is None:
                break

    def _process_message(self, socket, payload):
        entries = []
        if payload:
            try:
                message = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                message = None
            else:
                parsed_entries = message.get("entries")
                if isinstance(parsed_entries, list):
                    entries = parsed_entries

        if socket.state() == QLocalSocket.ConnectedState:
            socket.write(b"ok\n")
            socket.flush()
            socket.waitForBytesWritten(500)
            socket.disconnectFromServer()

        QTimer.singleShot(0, lambda items=entries: self._dispatch_message(items))

    def _cleanup_socket(self, socket):
        self._buffers.pop(socket, None)
        socket.deleteLater()

    def _dispatch_message(self, entries):
        if entries:
            self.entries_received.emit(entries)
        self.focus_requested.emit()


def parse_input(args, base_dir=None):
    if len(args) == 1 and isinstance(args[0], str) and args[0].endswith(".json"):
        json_path = args[0]
        if base_dir and not os.path.isabs(json_path):
            json_path = os.path.join(base_dir, json_path)
        with open(os.path.abspath(json_path), "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    else:
        return [{"url": a, "path": ""} for a in args]

def check_aria2_availability():
    try:
        # Intentar asegurar que Aria2 esté disponible
        if not ensure_aria2_running():
            # Si no se pudo iniciar Aria2, mostrar advertencia
            msg = QMessageBox()
            msg.setIcon(QMessageBox.Warning)
            msg.setWindowTitle("Aria2 no disponible")
            msg.setText(
                "No se pudo iniciar Aria2 para el manejo de torrents.\n\n"
                "El programa funcionará normalmente para descargas HTTP, "
                "pero no podrá manejar archivos .torrent o enlaces magnet.\n\n"
                "Para habilitar torrents, instala Aria2:"
            )
            msg.setDetailedText(
                "Pasos para instalar Aria2:\n\n"
                "Windows:\n"
                "1. Descarga Aria2 desde https://aria2.github.io/\n"
                "2. Extrae aria2c.exe a una carpeta en tu PATH\n"
                "3. O coloca aria2c.exe en la carpeta del programa\n\n"
                "Linux/Mac:\n"
                "sudo apt install aria2  (Ubuntu/Debian)\n"
                "brew install aria2  (Mac con Homebrew)\n"
                "pacman -S aria2  (Arch Linux)"
            )
            msg.exec_()
            return False
        else:
            print("✅ Aria2 configurado correctamente para torrents")
            return True
    except Exception as e:
        print(f"Error verificando Aria2: {e}")
        return False

if __name__ == '__main__':
    os.system("title Descargas")
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)

    args = sys.argv[1:]
    entries = parse_input(args, os.getcwd()) if args else []

    bridge = SingleInstanceBridge(SERVER_NAME, app)
    try:
        is_primary = bridge.claim_primary()
    except RuntimeError as exc:
        QMessageBox.critical(
            None,
            "Error al iniciar",
            f"No se pudo reservar la instancia unica del gestor de descargas.\n\n{exc}",
        )
        sys.exit(1)

    if not is_primary:
        if bridge.send_to_primary(entries):
            sys.exit(0)
        QMessageBox.warning(
            None,
            "Gestor ya abierto",
            "Ya hay una ventana de descargas abierta, pero no respondió al traspaso de enlaces.\n\n"
            "Esperá unos segundos o cerrala antes de volver a intentar.",
        )
        sys.exit(1)

    if not bridge.start_listening():
        QMessageBox.critical(
            None,
            "Error al iniciar",
            "No se pudo iniciar el canal de transferencia hacia la ventana principal.",
        )
        bridge.close()
        sys.exit(1)

    # Verificar disponibilidad de Aria2
    check_aria2_availability()

    window = DownloadWindow(entries)
    bridge.entries_received.connect(window.enqueue_external_entries)
    bridge.focus_requested.connect(window.bring_to_front)
    app.aboutToQuit.connect(bridge.close)
    window.show()
    sys.exit(app.exec_())
