from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtGui import QKeyEvent
from PyQt5.QtWidgets import QApplication, QDialog, QVBoxLayout
from PyQt5.QtWebEngineWidgets import QWebEnginePage, QWebEngineView


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
        layout = QVBoxLayout()
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
        self.setLayout(layout)
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
