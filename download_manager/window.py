import os
from PyQt5.QtWidgets import (
    QApplication, QDialog, QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QLabel,
    QPushButton, QProgressBar, QMessageBox, QGroupBox, QFrame, QSizePolicy
)
from PyQt5.QtCore import QObject, QRunnable, QTimer, QThreadPool, pyqtSignal
from download_manager.browser import UniversalDownloader
from download_manager.torrent import TorrentUpdater, ensure_aria2_running, Aria2Client
from download_manager.dialogs import LinkInputWindow, SettingsDialog, apply_settings
from download_manager.workers import DownloadSignals, FileDownloader
from config import DEFAULT_CONFIG, load_config
from download_manager.torrent_queue import TorrentProcessor
from enum import Enum


class DownloadType(Enum):
    NORMAL = 0
    TORRENT = 1
    TEMPORAL = 2


class TorrentCancelSignals(QObject):
    finished = pyqtSignal(str, bool, str)


class TorrentCancelWorker(QRunnable):
    def __init__(self, gid):
        super().__init__()
        self.gid = gid
        self.signals = TorrentCancelSignals()

    def run(self):
        try:
            result = Aria2Client().remove_download(self.gid, force=True)
            self.signals.finished.emit(self.gid, bool(result), "")
        except Exception as exc:
            self.signals.finished.emit(self.gid, False, str(exc))

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
        
        # Separar torrents de otras descargas
        self.torrent_entries = []
        self.regular_entries = []
        self.downloaders = []
        self.active_file_downloads = {}
        self._aria2_checked = False
        self._empty_state_container = None
        self._closing = False

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.inner_widget = QWidget()
        self.inner_layout = QVBoxLayout(self.inner_widget)
        self.inner_layout.setContentsMargins(8, 8, 8, 8)
        self.inner_layout.setSpacing(12)
        self.scroll.setWidget(self.inner_widget)
        self.layout.addWidget(self.scroll)
        self.inner_layout.addStretch(1)
        self.actions_row = QHBoxLayout()
        self.actions_row.addStretch(1)
        self.add_links_button = QPushButton("Agregar enlaces")
        self.add_links_button.clicked.connect(self.open_link_input)
        self.actions_row.addWidget(self.add_links_button)
        self.settings_button = QPushButton("Configuración ⚙")
        self.settings_button.clicked.connect(self.open_settings_dialog)
        self.actions_row.addWidget(self.settings_button)
        self.layout.addLayout(self.actions_row)

        self.torrent_hashes = {}
        self.completed_torrents = set()
        self.torrent_timer = QTimer()
        self.torrent_timer.timeout.connect(self.start_torrent_update)
        self.external_entries_timer = QTimer(self)
        self.external_entries_timer.setSingleShot(True)
        self.external_entries_timer.timeout.connect(self.process_external_entries)
        self.pending_external_entries = []

        self.download_groups = {}
        self.file_items = {}
        self.torrent_items = {}
        self.temp_progress_bars = []
        self.temp_labels = []
        self.saved_password_hints = set()
        
        if download_entries:
            self.load_entries(download_entries)
        else:
            self.show_empty_state()
        
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
            
    def process_torrents_parallel(self, torrents=None):
        if self._closing:
            return
        target = torrents or self.torrent_entries
        if not target:
            return
            
        print(f"⚡ Procesando {len(target)} torrents en paralelo...")
        self.ensure_torrent_timer_running()
        
        # Crear un procesador de torrents en un hilo separado
        processor = TorrentProcessor(target, self.folder_path)
        processor.signals.finished.connect(self.on_torrents_processed)
        QThreadPool.globalInstance().start(processor)
    
    def on_torrents_processed(self):
        print("✅ Todos los torrents han sido agregados a Aria2")

    def start_downloads(self, direct_links):
        if self._closing:
            return
        base_index = len(self.file_items)
        offset = 0
        for item in direct_links:
            if not item:
                continue
            if isinstance(item, dict):
                if item.get("type") == "direct":
                    relative_path = item["path"]
                    link = item["url"]
                    headers = item.get("headers")
                    cookies = item.get("cookies")
                    self.store_password_hint(relative_path, item.get("password"), item.get("title"))
                    index = base_index + offset
                    offset += 1
                    self._start_direct_download(relative_path, link, index, headers, cookies)
                continue

            relative_path, link = item
            if not relative_path or not link:
                continue
            index = base_index + offset
            offset += 1
            self._start_direct_download(relative_path, link, index, None, None)

        self.show()

    def _start_direct_download(self, relative_path, link, index, headers, cookies, on_finished=None):
        if not relative_path or not link:
            return
        full_path = os.path.normpath(os.path.join(self.folder_path, relative_path))

        # Los torrents ya fueron procesados en paralelo, solo manejar archivos regulares
        if link.startswith("magnet:?") or link.endswith(".torrent"):
            return  # Skip torrents, ya fueron procesados

        # Solo procesar descargas regulares
        dir_path = os.path.dirname(full_path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)

        _, item_display_name = self._split_grouped_path(relative_path)
        self.file_items[index] = self._create_download_item(
            relative_path,
            item_display_name,
            f"Descargando: {item_display_name}",
            lambda checked=False, idx=index: self.cancel_direct_download(idx),
        )

        signals = DownloadSignals()
        signals.progress.connect(self.update_progress)
        signals.cancelled.connect(self.on_direct_download_cancelled)
        if on_finished:
            signals.finished.connect(lambda idx=index, cb=on_finished: self.on_direct_download_finished(idx, cb))
        else:
            signals.finished.connect(lambda idx=index: self.on_direct_download_finished(idx))

        thread = FileDownloader(link, full_path, index, signals, headers=headers, cookies=cookies)
        self.active_file_downloads[index] = thread
        QThreadPool.globalInstance().start(thread)

    def load_entries(self, entries):
        if self._closing:
            return
        if not entries:
            return
        self.clear_empty_state()
        new_torrents = []
        new_regular = []
        for entry in entries:
            url = entry.get("url", "")
            if url.startswith("magnet:?") or url.endswith(".torrent"):
                new_torrents.append(entry)
            else:
                new_regular.append(entry)
            self.store_password_hint(entry.get("path", ""), entry.get("password"), entry.get("title") or url)

        if new_torrents:
            if not self._aria2_checked:
                ensure_aria2_running(self.folder_path, background=True)
                self._aria2_checked = True
            self.torrent_entries.extend(new_torrents)
            self.process_torrents_parallel(new_torrents)

        if new_regular:
            self.regular_entries.extend(new_regular)
            downloader = UniversalDownloader(new_regular)
            downloader.direct_link_found.connect(lambda item: self.start_downloads([item]))
            self.downloaders.append(downloader)
            downloader.start()

    def enqueue_external_entries(self, entries):
        if self._closing or not entries:
            return
        self.pending_external_entries.extend(entries)
        if not self.external_entries_timer.isActive():
            # Give Qt one paint cycle to focus/redraw the existing window
            self.external_entries_timer.start(75)

    def process_external_entries(self):
        if self._closing or not self.pending_external_entries:
            return
        entries = self.pending_external_entries
        self.pending_external_entries = []
        self.load_entries(entries)

    def ensure_torrent_timer_running(self):
        if not self.torrent_timer.isActive():
            self.torrent_timer.start(3000)

    def on_direct_download_finished(self, index, on_finished=None):
        self.active_file_downloads.pop(index, None)
        item = self.file_items.get(index)
        if not item or item.get("status") == "cancelled":
            return
        if on_finished:
            self.mark_finished(index)
            on_finished()
            return
        self.mark_finished(index)

    def on_direct_download_cancelled(self, index):
        item = self.file_items.get(index)
        if not item or item.get("status") == "cancelled":
            return
        self.active_file_downloads.pop(index, None)
        item["status"] = "cancelled"
        item["label"].setText(f"⏹ Cancelado: {item['name']}")
        self._finalize_item(item)

    def cancel_direct_download(self, index):
        downloader = self.active_file_downloads.pop(index, None)
        item = self.file_items.get(index)
        if not item or item.get("status") in ("finished", "cancelled"):
            return
        item["status"] = "cancelled"
        if downloader is not None:
            try:
                downloader.cancel()
            except Exception:
                pass
        item["label"].setText(f"⏹ Cancelado: {item['name']}")
        self._finalize_item(item)

    def cancel_torrent_download(self, gid):
        item = self.torrent_items.get(gid)
        if item and item.get("status") not in ("finished", "cancelled"):
            item["status"] = "cancelled"
            item["label"].setText(f"⏹ Torrent cancelado: {item['name']}")
            self._finalize_item(item)
        self.torrent_hashes.pop(gid, None)

        worker = TorrentCancelWorker(gid)
        worker.signals.finished.connect(self.on_torrent_cancel_finished)
        QThreadPool.globalInstance().start(worker)

    def on_torrent_cancel_finished(self, gid, ok, error_text):
        if ok:
            return
        if error_text:
            print(f"Error cancelando torrent {gid}: {error_text}")

    def clear_empty_state(self):
        if not self._empty_state_container:
            return
        self.inner_layout.removeWidget(self._empty_state_container)
        self._empty_state_container.deleteLater()
        self._empty_state_container = None

    def bring_to_front(self):
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    def show_empty_state(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        label = QLabel("No se pasaron enlaces. Usá el botón Agregar enlaces para empezar.")
        layout.addWidget(label)
        self._insert_content_widget(container)
        self._empty_state_container = container

    def _insert_content_widget(self, widget):
        self.inner_layout.insertWidget(self.inner_layout.count() - 1, widget)

    def store_password_hint(self, path_hint, password, title=""):
        if not password or not path_hint:
            return

        target_dir = os.path.normpath(path_hint)
        note_key = (target_dir, password.strip(), (title or "").strip())
        if note_key in self.saved_password_hints:
            return

        try:
            os.makedirs(target_dir, exist_ok=True)
            note_path = os.path.join(target_dir, "__passwords__.txt")
            entry_title = (title or "Archivo").strip()
            block = f"[{entry_title}]\n{password.strip()}\n\n"
            with open(note_path, "a", encoding="utf-8") as fh:
                fh.write(block)
            self.saved_password_hints.add(note_key)
        except Exception as exc:
            print(f"No se pudo guardar la contrasena para {title or path_hint}: {exc}")

    def _normalize_relative_path(self, relative_path):
        if not relative_path:
            return ""
        normalized = os.path.normpath(relative_path)
        folder_base = os.path.normpath(self.folder_path)

        try:
            rel_to_base = os.path.relpath(normalized, folder_base)
            if rel_to_base != "." and not rel_to_base.startswith(".."):
                return rel_to_base
            if rel_to_base == ".":
                return ""
        except ValueError:
            pass

        return "" if normalized == "." else normalized

    def _split_grouped_path(self, relative_path):
        normalized = self._normalize_relative_path(relative_path)
        if not normalized:
            return "", ""
        if not os.path.isabs(normalized):
            return "", normalized

        absolute_dir = os.path.dirname(normalized)
        absolute_name = os.path.basename(normalized)
        return absolute_dir, absolute_name

    def _group_title(self, group_key):
        if not group_key:
            return os.path.normpath(self.folder_path)
        if os.path.isabs(group_key):
            return os.path.normpath(group_key)
        return os.path.normpath(os.path.join(self.folder_path, group_key))

    def _get_or_create_group(self, group_key):
        group = self.download_groups.get(group_key)
        if group:
            return group

        box = QGroupBox(self._group_title(group_key))
        box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(2, 5, 2, 5)
        layout.setSpacing(5)
        self._insert_content_widget(box)
        group = {"box": box, "layout": layout}
        self.download_groups[group_key] = group
        return group

    def _create_download_item(self, relative_path, display_name, initial_text, cancel_callback):
        group_key, fallback_display_name = self._split_grouped_path(relative_path)
        item_name = display_name or fallback_display_name or os.path.basename(relative_path) or "Archivo"
        group = self._get_or_create_group(group_key)

        container = QFrame()
        container.setFrameShape(QFrame.StyledPanel)
        container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(2)

        header = QHBoxLayout()
        label = QLabel(initial_text)
        label.setWordWrap(True)
        cancel_button = QPushButton("Cancelar")
        cancel_button.clicked.connect(cancel_callback)
        header.addWidget(label, 1)
        header.addWidget(cancel_button)

        progress = QProgressBar()
        progress.setValue(0)

        layout.addLayout(header)
        layout.addWidget(progress)
        group["layout"].addWidget(container)

        return {
            "container": container,
            "label": label,
            "bar": progress,
            "cancel_button": cancel_button,
            "name": item_name,
            "status": "active",
        }

    def _finalize_item(self, item, keep_progress=False, disable_cancel=True):
        if disable_cancel:
            item["cancel_button"].setEnabled(False)
            item["cancel_button"].hide()
        if not keep_progress and item["bar"] is not None:
            item["bar"].hide()

    def open_link_input(self):
        self._link_input = LinkInputWindow()
        self._link_input.links_ready.connect(self.on_links_ready)
        self._link_input.show()
        self._link_input.raise_()
        self._link_input.activateWindow()

    def on_links_ready(self, links):
        if not links:
            return
        self.load_entries(links)

    def open_settings_dialog(self):
        dialog = SettingsDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            self.folder_path, self.open_on_finish, self.max_parallel_downloads = apply_settings()

    def start_torrent_update(self):
        if self._closing:
            return
        updater = TorrentUpdater()
        updater.signals.result.connect(self.on_torrent_data_received)
        updater.signals.error.connect(self.on_torrent_update_error)
        QThreadPool.globalInstance().start(updater)

    def on_torrent_update_error(self, message):
        if self._closing:
            return
        # Manejar errores específicos de Aria2
        if "No se pudo conectar a Aria2" in message:
            print("⚠️ Aria2 no está disponible - reintentando inicio...")
            # Intentar reiniciar Aria2
            if ensure_aria2_running(self.folder_path, background=True):
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
        if self._closing:
            return
        for t in torrents:
            # Mapear estados de Aria2 - omitir descargas pausadas o en cola
            if t.state in ("pausedDL", "pausedUP", "checkingUP", "checkingDL", "queuedDL", "waiting", "paused"):
                continue
            
            # Omitir descargas con error
            if t.state == "error":
                if t.hash in self.torrent_hashes:
                    _, name, _ = self.torrent_hashes[t.hash]
                    item = self.torrent_items.get(t.hash)
                    if item:
                        clean_name = self.get_clean_torrent_name(name)
                        item["label"].setText(f"❌ Error: {clean_name}")
                        item["status"] = "error"
                        self._finalize_item(item)
                    self.torrent_hashes.pop(t.hash, None)
                continue
            
            # Verificar si ya se completó este torrent para evitar spam
            if (t.state == "complete" or t.progress >= 1.0) and t.hash in self.completed_torrents:
                continue
                
            # Verificar duplicados por nombre (para manejar [METADATA] vs nombre real)
            if t.hash not in self.torrent_hashes and self.is_duplicate_torrent(t):
                continue
                
            if t.hash in self.torrent_hashes:
                _, stored_name, completed = self.torrent_hashes[t.hash]
                item = self.torrent_items.get(t.hash)
                if item:
                    percent = int(t.progress * 100)
                    item["bar"].setValue(percent)
                    
                    # Actualizar nombre si cambió (de METADATA a nombre real)
                    current_name = t.name
                    if current_name != stored_name and not current_name.startswith("[METADATA]"):
                        # Actualizar con el nombre real
                        self.torrent_hashes[t.hash] = (t.hash, current_name, completed)
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
                    
                    item["name"] = display_name
                    item["label"].setText(f"Descargando torrent: {display_name}{speed_text}")
                    
                    # Marcar como completado si el progreso es 100% o el estado es 'complete'
                    if (percent >= 100 or t.state == "complete") and not completed:
                        # Marcar como completado y agregar al set para evitar spam
                        self.completed_torrents.add(t.hash)
                        self.torrent_hashes[t.hash] = (t.hash, stored_name, True)
                        self.mark_finished(t.hash, DownloadType.TORRENT, display_name)
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
                        
                torrent_path = ""
                if t.save_path:
                    try:
                        torrent_path = os.path.relpath(t.save_path, self.folder_path)
                    except ValueError:
                        torrent_path = t.save_path
                item = self._create_download_item(
                    torrent_path,
                    clean_name,
                    f"Descargando torrent: {clean_name}{speed_text}",
                    lambda checked=False, gid=t.gid: self.cancel_torrent_download(gid),
                )
                item["bar"].setValue(int(t.progress * 100))
                self.torrent_items[t.hash] = item
                self.torrent_hashes[t.hash] = (t.hash, t.name, False)

    def update_progress(self, index, percent):
        item = self.file_items.get(index)
        if not item or item.get("status") == "cancelled":
            return
        item["bar"].setValue(percent)

    def mark_finished(self, index, download_type=DownloadType.NORMAL, custom_name=None):
        if download_type == DownloadType.TORRENT:
            item = self.torrent_items.get(index)
            if not item:
                return
            lb = item["label"]
        elif download_type == DownloadType.TEMPORAL:
            if index >= len(self.temp_labels):
                return  # Índice inválido
            lb = self.temp_labels[index]
            pb = self.temp_progress_bars[index]
            item = None
        else:
            item = self.file_items.get(index)
            if not item:
                return
            lb = item["label"]

        if download_type == DownloadType.TORRENT:
            if custom_name:
                done = f"✅ Torrent completado: {custom_name}"
            else:
                torrent_name = lb.text().replace("Descargando torrent: ", "").split(" - ")[0]
                done = f"✅ Torrent completado: {torrent_name}"
        elif download_type == DownloadType.TEMPORAL:
            done = lb.text()
        else:
            done = f"✅ Completado: {item['name']}"
        
        print(done)
        lb.setText(done)
        if item is not None:
            item["status"] = "finished"
            self._finalize_item(item)
        else:
            self.inner_layout.removeWidget(pb)
            pb.deleteLater()
        
        if download_type == DownloadType.TEMPORAL:
            self.inner_layout.removeWidget(lb)
            lb.deleteLater()

    def has_active_work(self):
        if self.active_file_downloads:
            return True

        if any(item.get("status") == "active" for item in self.file_items.values()):
            return True

        if any(item.get("status") == "active" for item in self.torrent_items.values()):
            return True

        if any(not completed for _, _, completed in self.torrent_hashes.values()):
            return True

        for downloader in self.downloaders:
            if downloader is None:
                continue
            if getattr(downloader, "_gdrive_waiting_download", False):
                return True
            current_index = getattr(downloader, "current_index", 0)
            urls = getattr(downloader, "urls", [])
            if current_index < len(urls):
                return True
            try:
                if downloader.isVisible():
                    return True
            except RuntimeError:
                continue

        return False

    def confirm_close_if_needed(self):
        if not self.has_active_work():
            return True

        answer = QMessageBox.question(
            self,
            "Cerrar descargas",
            "Hay descargas en curso. Si cerras ahora, se cancelara el trabajo activo.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return answer == QMessageBox.Yes

    def shutdown_app(self):
        self._closing = True
        if self.torrent_timer.isActive():
            self.torrent_timer.stop()

        for downloader in list(self.active_file_downloads.values()):
            try:
                downloader.cancel()
            except Exception:
                pass
        self.active_file_downloads.clear()

        for downloader in list(self.downloaders):
            try:
                downloader.direct_link_found.disconnect()
            except Exception:
                pass
            try:
                downloader.close()
                downloader.deleteLater()
            except Exception:
                pass
        self.downloaders.clear()

        if hasattr(self, "_link_input") and self._link_input:
            try:
                self._link_input.close()
            except Exception:
                pass

        QThreadPool.globalInstance().clear()

    def closeEvent(self, event):
        if not self._closing and not self.confirm_close_if_needed():
            event.ignore()
            return

        self.shutdown_app()
        event.accept()

        app = QApplication.instance()
        if app is not None:
            app.quit()

