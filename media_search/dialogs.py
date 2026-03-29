import os

from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtGui import QKeyEvent
from PyQt5.QtWidgets import (
    QApplication,
    QFileDialog,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)
from PyQt5.QtWebEngineWidgets import QWebEnginePage, QWebEngineView

from config import DEFAULT_CONFIG, load_config, save_config


MEDIA_CATEGORY_PATHS = {
    "general": ("general_folder_path", "General"),
    "anime": ("anime_folder_path", "Anime"),
    "manga": ("manga_folder_path", "Manga"),
    "VN": ("vn_folder_path", "Visual Novel"),
    "games": ("games_folder_path", "Games"),
}


class SilentPage(QWebEnginePage):
    def javaScriptConsoleMessage(self, level, message, lineNumber, sourceID):
        pass


class TrailerWindow(QDialog):
    def __init__(self, embed_url, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Tráiler")
        self.resize(640, 360)
        embed_url = embed_url or ""
        self.web_view = None
        layout = QVBoxLayout(self)
        html = f"""
        <html>
          <head>
            <style>
              body {{ margin: 0; background-color: #000; }}
              iframe {{ width: 100%; height: 100%; border: none; }}
            </style>
          </head>
          <body>
            <iframe src="{embed_url}?autoplay=1" allow="autoplay; encrypted-media" allowfullscreen></iframe>
          </body>
        </html>
        """
        self.web_view = QWebEngineView()
        self.web_view.setPage(SilentPage(self.web_view))
        self.web_view.setHtml(html)
        layout.addWidget(self.web_view)
        QTimer.singleShot(2000, self.simulate_k_keypress)

    def simulate_k_keypress(self):
        if not self.web_view:
            return
        event = QKeyEvent(QKeyEvent.KeyPress, Qt.Key_K, Qt.NoModifier, "k")
        QApplication.postEvent(self.web_view.focusProxy(), event)
        event_release = QKeyEvent(QKeyEvent.KeyRelease, Qt.Key_K, Qt.NoModifier, "k")
        QApplication.postEvent(self.web_view.focusProxy(), event_release)

    def closeEvent(self, event):
        if self.web_view:
            self.web_view.setHtml("<html><body></body></html>")
        super().closeEvent(event)


class MediaPathsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Carpetas de Descarga")
        self.config = load_config()
        self.path_inputs = {}
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        for category, (config_key, label) in MEDIA_CATEGORY_PATHS.items():
            layout.addWidget(QLabel(f"Carpeta de descarga ({label}):"))
            path_edit = QLineEdit()
            path_edit.setText(self.config.get(config_key, DEFAULT_CONFIG[config_key]))
            browse_btn = QPushButton("📁")
            browse_btn.setFixedWidth(30)
            browse_btn.clicked.connect(lambda _checked=False, current=category: self.choose_folder(current))
            row = QHBoxLayout()
            row.addWidget(path_edit)
            row.addWidget(browse_btn)
            layout.addLayout(row)
            self.path_inputs[category] = path_edit

        buttons_layout = QHBoxLayout()
        save_btn = QPushButton("Guardar")
        cancel_btn = QPushButton("Cancelar")
        save_btn.clicked.connect(self.save_and_close)
        cancel_btn.clicked.connect(self.reject)
        buttons_layout.addWidget(save_btn)
        buttons_layout.addWidget(cancel_btn)
        layout.addLayout(buttons_layout)

    def choose_folder(self, category):
        config_key, label = MEDIA_CATEGORY_PATHS[category]
        current_path = self.path_inputs[category].text().strip() or self.config.get(config_key, DEFAULT_CONFIG[config_key])
        folder = QFileDialog.getExistingDirectory(self, f"Seleccionar carpeta ({label})", current_path)
        if folder:
            self.path_inputs[category].setText(folder)

    def save_and_close(self):
        for category, (config_key, label) in MEDIA_CATEGORY_PATHS.items():
            folder_path = self.path_inputs[category].text().strip() or self.config.get(config_key, DEFAULT_CONFIG[config_key])
            if not self.ensure_folder_exists(folder_path, f"Carpeta de descarga ({label})"):
                return
            self.config[config_key] = folder_path

        save_config(self.config)
        self.accept()

    def ensure_folder_exists(self, folder_path, label):
        if os.path.exists(folder_path) and os.path.isdir(folder_path):
            return True

        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Warning)
        msg.setWindowTitle("Carpeta no encontrada")
        msg.setText(f"{label} no existe:\n\n{folder_path}")
        msg.setInformativeText("¿Deseas crearla?")
        create_btn = msg.addButton("Crear carpeta", QMessageBox.AcceptRole)
        msg.addButton("Cancelar", QMessageBox.RejectRole)
        msg.exec_()
        if msg.clickedButton() != create_btn:
            return False

        try:
            os.makedirs(folder_path, exist_ok=True)
            return True
        except Exception as exc:
            QMessageBox.critical(self, "Error al crear carpeta", f"No se pudo crear la carpeta:\n{exc}")
            return False
