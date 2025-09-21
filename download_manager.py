import os, sys, json
from PyQt5.QtWidgets import QApplication, QMessageBox
from ui import LinkInputWindow, DownloadWindow
from torrent import ensure_aria2_running

def parse_input(args):
    if len(args) == 1 and isinstance(args[0], str) and args[0].endswith(".json"):
        with open(args[0], "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
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
    
    # Verificar disponibilidad de Aria2
    check_aria2_availability()
    
    args = sys.argv[1:]

    if not args:
        link_input = LinkInputWindow()
        link_input.show()
        app.exec_()
        args = link_input.links
    else:
        args = parse_input(args)

    if args:
        window = DownloadWindow(args)
        sys.exit(app.exec_())