import os
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QScrollArea, QLabel, QProgressBar,
    QPushButton, QHBoxLayout, QDialog, QTextEdit
)
from PyQt5.QtCore import QTimer, QThreadPool

from browser_handler import UniversalDownloader
from workers import DownloadSignals, FileDownloader
from torrent import TorrentUpdater, add_magnet_link, add_torrent_file
from settings_dialog import SettingsDialog, load_config, DEFAULT_CONFIG
from enum import Enum


class DownloadType(Enum):
    NORMAL = 0
    TORRENT = 1
    TEMPORAL = 2


class DownloadWindow(QWidget):
    def __init__(self, urls):
        super().__init__()
        self.setWindowTitle("Descargador Universal")
        self.setMinimumSize(400, 200)
        self.layout = QVBoxLayout(self)

        self.config = load_config()
        self.folder_path = self.config.get("folder_path", DEFAULT_CONFIG["folder_path"])
        self.open_on_finish = self.config.get("open_on_finish", DEFAULT_CONFIG["open_on_finish"])
        self.max_parallel_downloads = self.config.get("max_parallel_downloads", DEFAULT_CONFIG["max_parallel_downloads"])

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.inner_widget = QWidget()
        self.inner_layout = QVBoxLayout(self.inner_widget)
        self.scroll.setWidget(self.inner_widget)
        self.layout.addWidget(self.scroll)
        self.settings_button = QPushButton("Configuración ⚙")
        self.settings_button.clicked.connect(self.open_settings_dialog)
        self.layout.addWidget(self.settings_button)

        self.torrent_hashes = {}
        self.torrent_timer = QTimer()
        self.torrent_timer.timeout.connect(self.start_torrent_update)
        self.torrent_timer.start(3000)

        self.progress_bars = []
        self.labels = []
        self.temp_progress_bars = []
        self.temp_labels = []
        self.torrent_progress_bars = []
        self.torrent_labels = []
        self.downloader = UniversalDownloader(urls)
        self.downloader.direct_links_ready.connect(self.start_downloads)
        self.downloader.start()

    def start_downloads(self, direct_links):
        for index, (relative_path, link) in enumerate(direct_links):
            full_path = os.path.join(self.folder_path, relative_path)
            if not link:
                continue

            if link.startswith("magnet:?"):
                torrent_hash = add_magnet_link(link, self.folder_path)
                if torrent_hash:
                    print("Magnet agregado " + torrent_hash)

            elif link.endswith(".torrent"):
                def make_on_finished(idx, path):
                    def check_file(attempt=1):
                        if os.path.exists(path):
                            add_torrent_file(path, self.folder_path)
                            self.mark_finished(idx, DownloadType.TEMPORAL)
                        elif attempt < 10:
                            QTimer.singleShot(200, lambda: check_file(attempt + 1))
                        else:
                            print(f"❌ Archivo .torrent no encontrado: {path}")
                    return lambda: check_file()

                label = QLabel(f"Descargando .torrent: {relative_path}")
                bar = QProgressBar()
                bar.setValue(0)
                self.inner_layout.addWidget(label)
                self.inner_layout.addWidget(bar)
                self.temp_labels.append(label)
                self.temp_progress_bars.append(bar)

                signals = DownloadSignals()
                signals.progress.connect(self.update_progress)
                signals.finished.connect(make_on_finished(index, full_path))

                thread = FileDownloader(link, full_path, index, signals)
                QThreadPool.globalInstance().start(thread)

            else:
                dir_path = os.path.dirname(full_path)
                if dir_path:
                    os.makedirs(dir_path, exist_ok=True)

                label = QLabel(f"Descargando: {relative_path}")
                bar = QProgressBar()
                bar.setValue(0)
                self.inner_layout.addWidget(label)
                self.inner_layout.addWidget(bar)
                self.labels.append(label)
                self.progress_bars.append(bar)

                signals = DownloadSignals()
                signals.progress.connect(self.update_progress)
                signals.finished.connect(lambda idx=index: self.mark_finished(idx))

                thread = FileDownloader(link, full_path, index, signals)
                QThreadPool.globalInstance().start(thread)

        self.show()

    def open_settings_dialog(self):
        dialog = SettingsDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            self.folder_path, self.open_on_finish, self.max_parallel_downloads = apply_settings()

    def start_torrent_update(self):
        updater = TorrentUpdater()
        updater.signals.result.connect(self.on_torrent_data_received)
        updater.signals.error.connect(self.on_torrent_update_error)
        QThreadPool.globalInstance().start(updater)

    def on_torrent_update_error(self, message):
        if "404 Not Found" not in message:
            print(f"Error al actualizar progreso de torrents: {message}")

    def on_torrent_data_received(self, torrents):
        for t in torrents:
            if t.state in ("pausedDL", "pausedUP", "checkingUP", "checkingDL", "queuedDL"):
                continue
            if t.hash in self.torrent_hashes:
                index, _ = self.torrent_hashes[t.hash]
                percent = int(t.progress * 100)
                self.torrent_progress_bars[index].setValue(percent)
                if percent >= 100:
                    self.mark_finished(index)
                    self.torrent_hashes.pop(t.hash, None)
            else:
                label = QLabel(f"Descargando torrent: {t.name}")
                bar = QProgressBar()
                bar.setValue(int(t.progress * 100))
                self.inner_layout.addWidget(label)
                self.inner_layout.addWidget(bar)
                index = len(self.torrent_labels)
                self.torrent_labels.append(label)
                self.torrent_progress_bars.append(bar)
                self.torrent_hashes[t.hash] = (index, t.name)

    def update_progress(self, index, percent):
        if index >= len(self.progress_bars):
            return
        self.progress_bars[index].setValue(percent)

    def mark_finished(self, index, download_type=DownloadType.NORMAL):
        if download_type == DownloadType.TORRENT:
            lb = self.torrent_labels[index]
            pb = self.torrent_progress_bars[index]
        elif download_type == DownloadType.TEMPORAL:
            lb = self.temp_labels[index]
            pb = self.temp_progress_bars[index]
        else:
            lb = self.labels[index]
            pb = self.progress_bars[index]

        done = f"✅ Completado: {lb.text()[12:]}"
        print(done)
        lb.setText(done)
        self.inner_layout.removeWidget(pb)
        pb.deleteLater()
        if download_type == DownloadType.TEMPORAL:
            self.inner_layout.removeWidget(lb)
            lb.deleteLater()


class LinkInputWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pegar enlaces de paginas de descarga")
        self.setMinimumSize(400, 200)

        layout = QVBoxLayout()
        self.instructions = QLabel("Pega uno o más enlaces (uno por línea):")
        self.textbox = QTextEdit()
        self.accept_button = QPushButton("Iniciar Descargas")
        self.accept_button.clicked.connect(self.proceed)
        self.settings_button = QPushButton('⚙')
        self.settings_button.clicked.connect(self.open_settings_dialog)
        self.settings_button.setFixedWidth(25)

        layout.addWidget(self.instructions)
        layout.addWidget(self.textbox)
        buttons = QHBoxLayout()
        buttons.addWidget(self.accept_button)
        buttons.addWidget(self.settings_button)
        layout.addLayout(buttons)
        self.setLayout(layout)

        self.links = []

    def open_settings_dialog(self):
        dialog = SettingsDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            apply_settings()

    def proceed(self):
        text = self.textbox.toPlainText().strip()
        if text:
            self.links = [line.strip() for line in text.splitlines() if line.strip()]
            self.close()


def apply_settings():
    config = load_config()
    folder_path = config.get("folder_path")
    open_on_finish = config.get("open_on_finish")
    max_parallel_downloads = config.get("max_parallel_downloads")
    print(f"✅ Configuración actualizada: {config}")
    return folder_path, open_on_finish, max_parallel_downloads