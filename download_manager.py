import os, sys, json, traceback, datetime

from PyQt5.QtWidgets import QApplication, QMessageBox
from PyQt5.QtCore import Qt, QCoreApplication
from ui import LinkInputWindow, DownloadWindow
from torrent import ensure_aria2_running, Aria2Client

def parse_input(args):
    print(f"[DEBUG] parse_input args: {args}")
    if len(args) == 1 and isinstance(args[0], str) and args[0].endswith(".json"):
        print(f"[DEBUG] Loading JSON file: {args[0]}")
        with open(args[0], "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"[DEBUG] Loaded JSON entries: {len(data)}")
        return data
    else:
        print(f"[DEBUG] Treating args as URLs. Count: {len(args)}")
        return [{"url": a, "path": ""} for a in args]

def check_aria2_availability():
    try:
        client = Aria2Client()
        print(f"[DEBUG] Aria2 running: {client.is_running()}")
        if client.is_running():
            print("✅ Aria2 configurado correctamente para torrents")
            return True

        # Iniciar Aria2 en segundo plano para no bloquear la UI
        print("[DEBUG] Starting Aria2 in background")
        ensure_aria2_running(background=True)

        # Si no hay ejecutable y no se puede descargar (no Windows), avisar
        if client.find_aria2_executable() is None and os.name != "nt":
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
        return True
    except Exception as e:
        print(f"Error verificando Aria2: {e}")
        traceback.print_exc()
        return False

if __name__ == '__main__':
    os.system("title Descargas")

    log_dir = os.path.join(os.environ.get("APPDATA", os.getcwd()), "MediaSearchPrototype", "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(
        log_dir,
        f"download_manager_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    )
    _log_file = open(log_path, "a", encoding="utf-8", buffering=1)
    sys.stdout = _log_file
    sys.stderr = _log_file
    print(f"[DEBUG] Starting app. argv: {sys.argv}")

    def _excepthook(exc_type, exc, tb):
        print("[FATAL] Unhandled exception:")
        traceback.print_exception(exc_type, exc, tb)
    sys.excepthook = _excepthook

    app = QApplication(sys.argv)
    
    # Verificar disponibilidad de Aria2
    check_aria2_availability()
    
    args = sys.argv[1:]
    print(f"[DEBUG] Raw args: {args}")

    try:
        if not args:
            print("[DEBUG] No args. Launching LinkInputWindow.")
            link_input = LinkInputWindow()
            link_input.show()
            app.exec_()
            args = link_input.links
            print(f"[DEBUG] Links from UI: {len(args) if args else 0}")
        else:
            args = parse_input(args)

        if args:
            print(f"[DEBUG] Launching DownloadWindow with {len(args)} entries")
            window = DownloadWindow(args)
            sys.exit(app.exec_())
        else:
            print("[DEBUG] No download entries. Exiting.")
    except Exception as e:
        print(f"[FATAL] Crash in main: {e}")
        traceback.print_exc()
