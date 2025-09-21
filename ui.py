import os
from PyQt5.QtWidgets import (
    QLineEdit, QFormLayout, QDialogButtonBox, QFileDialog, 
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QLabel,
    QPushButton, QHBoxLayout, QProgressBar, QDialog, QTextEdit
)
from PyQt5.QtCore import QTimer, QThreadPool
from browser_handler import UniversalDownloader
from workers import DownloadSignals, FileDownloader
from torrent import TorrentUpdater, add_magnet_link, add_torrent_file, ensure_aria2_running
from settings_dialog import SettingsDialog, load_config, DEFAULT_CONFIG
from enum import Enum


class DownloadType(Enum):
    NORMAL = 0
    TORRENT = 1
    TEMPORAL = 2

class DownloadWindow(QWidget):
    def __init__(self, download_entries):
        super().__init__()
        self.setWindowTitle("Descargador Universal")
        self.setMinimumSize(400, 200)
        self.layout = QVBoxLayout(self)

        self.config = load_config()
        self.folder_path = self.config.get("folder_path", DEFAULT_CONFIG["folder_path"])
        self.open_on_finish = self.config.get("open_on_finish", DEFAULT_CONFIG["open_on_finish"])
        self.max_parallel_downloads = self.config.get("max_parallel_downloads", DEFAULT_CONFIG["max_parallel_downloads"])
        
        # Asegurar que Aria2 esté corriendo para torrents
        ensure_aria2_running(self.folder_path)
        
        # Limpiar descargas completadas de sesiones anteriores
        self.cleanup_previous_downloads()

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.inner_widget = QWidget()
        self.inner_layout = QVBoxLayout(self.inner_widget)
        self.scroll.setWidget(self.inner_widget)
        self.layout.addWidget(self.scroll)
        self.settings_button = QPushButton("Configuración ⚙")
        self.settings_button.clicked.connect(self.open_settings_dialog)
        self.layout.addWidget(self.settings_button)

        self.torrent_hashes = {}  # {hash: (index, name, completed)}
        self.completed_torrents = set()  # Set para evitar mensajes duplicados
        self.torrent_timer = QTimer()
        self.torrent_timer.timeout.connect(self.start_torrent_update)
        self.torrent_timer.start(3000)

        self.progress_bars = []
        self.labels = []
        self.temp_progress_bars = []
        self.temp_labels = []
        self.torrent_progress_bars = []
        self.torrent_labels = []
        self.downloader = UniversalDownloader(download_entries)
        self.downloader.direct_links_ready.connect(self.start_downloads)
        self.downloader.start()
        
    def cleanup_previous_downloads(self):
        try:
            from torrent import Aria2Client
            client = Aria2Client()
            if client.is_running():
                stopped = client.get_stopped_downloads(50)
                removed_count = 0
                for download in stopped:
                    if download.state == "complete" or download.progress >= 1.0:
                        client.remove_download(download.gid, force=True)
                        removed_count += 1
                
                if removed_count > 0:
                    print(f"🧹 Limpiadas {removed_count} descargas de sesiones anteriores")
        except Exception as e:
            pass

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
        # Manejar errores específicos de Aria2
        if "No se pudo conectar a Aria2" in message:
            print("⚠️ Aria2 no está disponible - reintentando inicio...")
            # Intentar reiniciar Aria2
            if ensure_aria2_running(self.folder_path):
                print("✅ Aria2 reiniciado exitosamente")
        elif "Connection refused" not in message and "timeout" not in message:
            print(f"Error al actualizar progreso de torrents: {message}")

    def get_clean_torrent_name(self, name):
        """Limpia el nombre del torrent removiendo [METADATA] y otros prefijos"""
        if not name:
            return "Torrent desconocido"
        
        # Remover [METADATA] al inicio
        clean_name = name
        if clean_name.startswith("[METADATA]"):
            clean_name = clean_name[10:]
        
        # Remover espacios extra
        clean_name = clean_name.strip()
        
        # Si queda vacío después de limpiar, usar nombre original
        if not clean_name:
            clean_name = name
            
        return clean_name

    def is_duplicate_torrent(self, torrent):
        """Verifica si un torrent es duplicado basándose en el nombre limpio"""
        clean_name = self.get_clean_torrent_name(torrent.name)
        
        # Verificar si ya existe un torrent con el mismo nombre limpio
        for existing_hash, (index, stored_name, completed) in self.torrent_hashes.items():
            if existing_hash != torrent.hash:
                existing_clean_name = self.get_clean_torrent_name(stored_name)
                if clean_name == existing_clean_name:
                    return True
        return False

    def on_torrent_data_received(self, torrents):
        for t in torrents:
            # Mapear estados de Aria2 - omitir descargas pausadas o en cola
            if t.state in ("pausedDL", "pausedUP", "checkingUP", "checkingDL", "queuedDL", "waiting", "paused"):
                continue
            
            # Omitir descargas con error
            if t.state == "error":
                if t.hash in self.torrent_hashes:
                    index, name, completed = self.torrent_hashes[t.hash]
                    if index < len(self.torrent_labels):
                        clean_name = self.get_clean_torrent_name(name)
                        self.torrent_labels[index].setText(f"❌ Error: {clean_name}")
                    self.torrent_hashes.pop(t.hash, None)
                continue
            
            # Verificar si ya se completó este torrent para evitar spam
            if (t.state == "complete" or t.progress >= 1.0) and t.hash in self.completed_torrents:
                continue
                
            # Verificar duplicados por nombre (para manejar [METADATA] vs nombre real)
            if t.hash not in self.torrent_hashes and self.is_duplicate_torrent(t):
                continue
                
            if t.hash in self.torrent_hashes:
                index, stored_name, completed = self.torrent_hashes[t.hash]
                if index < len(self.torrent_progress_bars):
                    percent = int(t.progress * 100)
                    self.torrent_progress_bars[index].setValue(percent)
                    
                    # Actualizar nombre si cambió (de METADATA a nombre real)
                    current_name = t.name
                    if current_name != stored_name and not current_name.startswith("[METADATA]"):
                        # Actualizar con el nombre real
                        self.torrent_hashes[t.hash] = (index, current_name, completed)
                        stored_name = current_name
                    
                    # Usar el nombre más limpio disponible
                    display_name = self.get_clean_torrent_name(stored_name)
                    
                    # Actualizar etiqueta con velocidad de descarga
                    speed_text = ""
                    if hasattr(t, 'dlspeed') and t.dlspeed > 0:
                        speed_mb = t.dlspeed / (1024 * 1024)
                        if speed_mb >= 1:
                            speed_text = f" - {speed_mb:.1f} MB/s"
                        else:
                            speed_kb = t.dlspeed / 1024
                            speed_text = f" - {speed_kb:.0f} KB/s"
                    
                    self.torrent_labels[index].setText(f"Descargando torrent: {display_name}{speed_text}")
                    
                    # Marcar como completado si el progreso es 100% o el estado es 'complete'
                    if (percent >= 100 or t.state == "complete") and not completed:
                        # Marcar como completado y agregar al set para evitar spam
                        self.completed_torrents.add(t.hash)
                        self.torrent_hashes[t.hash] = (index, stored_name, True)
                        self.mark_finished(index, DownloadType.TORRENT, display_name)
            else:
                # Nueva descarga - agregar a la interfaz
                # Omitir si ya está en torrents completados
                if t.hash in self.completed_torrents:
                    continue
                    
                clean_name = self.get_clean_torrent_name(t.name)
                
                speed_text = ""
                if hasattr(t, 'dlspeed') and t.dlspeed > 0:
                    speed_mb = t.dlspeed / (1024 * 1024)
                    if speed_mb >= 1:
                        speed_text = f" - {speed_mb:.1f} MB/s"
                    else:
                        speed_kb = t.dlspeed / 1024
                        speed_text = f" - {speed_kb:.0f} KB/s"
                        
                label = QLabel(f"Descargando torrent: {clean_name}{speed_text}")
                bar = QProgressBar()
                bar.setValue(int(t.progress * 100))
                self.inner_layout.addWidget(label)
                self.inner_layout.addWidget(bar)
                index = len(self.torrent_labels)
                self.torrent_labels.append(label)
                self.torrent_progress_bars.append(bar)
                self.torrent_hashes[t.hash] = (index, t.name, False)  # Agregar estado de completado

    def update_progress(self, index, percent):
        if index >= len(self.progress_bars):
            return
        self.progress_bars[index].setValue(percent)

    def mark_finished(self, index, download_type=DownloadType.NORMAL, custom_name=None):
        if download_type == DownloadType.TORRENT:
            if index >= len(self.torrent_labels):
                return  # Índice inválido
            lb = self.torrent_labels[index]
            pb = self.torrent_progress_bars[index]
        elif download_type == DownloadType.TEMPORAL:
            if index >= len(self.temp_labels):
                return  # Índice inválido
            lb = self.temp_labels[index]
            pb = self.temp_progress_bars[index]
        else:
            if index >= len(self.labels):
                return  # Índice inválido
            lb = self.labels[index]
            pb = self.progress_bars[index]

        if download_type == DownloadType.TORRENT:
            # Para torrents, usar el nombre personalizado si está disponible
            if custom_name:
                done = f"✅ Torrent completado: {custom_name}"
            else:
                # Fallback: extraer del texto de la etiqueta
                torrent_name = lb.text().replace("Descargando torrent: ", "").split(" - ")[0]  # Remover velocidad
                done = f"✅ Torrent completado: {torrent_name}"
        else:
            done = f"✅ Completado: {lb.text()[12:]}"
        
        print(done)
        lb.setText(done)
        self.inner_layout.removeWidget(pb)
        pb.deleteLater()
        if download_type == DownloadType.TEMPORAL:
            self.inner_layout.removeWidget(lb)
            lb.deleteLater()

class DownloadDetailsDialog(QDialog):
    def __init__(self, urls, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Detalles de descarga")
        self.resize(600, 400)

        self.config = load_config()
        self.default_path = self.config.get("folder_path", DEFAULT_CONFIG["folder_path"])

        self.entries = []  # Guarda widgets por fila

        layout = QVBoxLayout(self)
        for url in urls:
            form = QFormLayout()
            url_label = QLabel(url)
            url_label.setWordWrap(True)

            # Campo contraseña
            pass_input = QLineEdit()
            pass_input.setEchoMode(QLineEdit.Password)

            # Campo path con botón
            form.addRow(QLabel("<b>URL:</b>"), url_label)
            form.addRow("Contraseña:", pass_input)

            path_input = QLineEdit(self.default_path)
            browse_btn = QPushButton("📁")
            browse_btn.setFixedWidth(30)
            browse_btn.clicked.connect(lambda _, p=path_input: self.choose_path(p))
            path_container = QHBoxLayout()
            path_container.addWidget(path_input)
            path_container.addWidget(browse_btn)
            form.addRow("Guardar en:", path_container)

            layout.addLayout(form)
            self.entries.append({
                "url": url,
                "password_widget": pass_input,
                "path_widget": path_input
            })

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def choose_path(self, path_input):
        folder = QFileDialog.getExistingDirectory(self, "Seleccionar carpeta", path_input.text())
        if folder:
            path_input.setText(folder)

    def get_results(self):
        return [
            {
                "url": e["url"],
                "password": e["password_widget"].text().strip(),
                "path": e["path_widget"].text().strip()
            }
            for e in self.entries
        ]

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
        if not text:
            return
        urls = [line.strip() for line in text.splitlines() if line.strip()]
        if not urls:
            return
        dialog = DownloadDetailsDialog(urls, self)
        if dialog.exec_() == QDialog.Accepted:
            self.links = dialog.get_results()
            self.close()

def apply_settings():
    config = load_config()
    folder_path = config.get("folder_path")
    open_on_finish = config.get("open_on_finish")
    max_parallel_downloads = config.get("max_parallel_downloads")
    print(f"✅ Configuración actualizada: {config}")
    return folder_path, open_on_finish, max_parallel_downloads