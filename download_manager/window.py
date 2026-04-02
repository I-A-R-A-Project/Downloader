import json
import os
import tempfile
import uuid
from enum import Enum

from PyQt5.QtCore import QObject, QRunnable, QTimer, QThreadPool, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication, QDialog, QFrame, QGroupBox, QHBoxLayout, QLabel, QMessageBox,
    QProgressBar, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from config import APPDATA, DEFAULT_CONFIG, load_config
from download_manager.browser import UniversalDownloader
from download_manager.dialogs import LinkInputWindow, SettingsDialog, apply_settings
from download_manager.torrent import Aria2Client, TorrentUpdater, ensure_aria2_running
from download_manager.torrent_queue import TorrentProcessor
from download_manager.workers import DownloadSignals, FileDownloader


SESSION_PATH = os.path.join(APPDATA, "MediaSearchPrototype", "download_state.json")


class DownloadType(Enum):
    NORMAL = 0
    TORRENT = 1


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
        QThreadPool.globalInstance().setMaxThreadCount(self.max_parallel_downloads)

        self.downloaders = []
        self.active_resolutions = {}
        self.active_file_downloads = {}
        self.worker_context = {}
        self.pending_torrent_entries = set()
        self.saved_password_hints = set()
        self._aria2_checked = False
        self._empty_state_container = None
        self._closing = False
        self._scheduler_queued = False
        self._next_worker_index = 0

        self.entries = {}
        self.entry_order = []
        self.entry_items = {}
        self.torrent_gid_to_entry = {}
        self.download_groups = {}

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

        self.torrent_timer = QTimer()
        self.torrent_timer.timeout.connect(self.start_torrent_update)
        self.external_entries_timer = QTimer(self)
        self.external_entries_timer.setSingleShot(True)
        self.external_entries_timer.timeout.connect(self.process_external_entries)
        self.pending_external_entries = []
        self.session_save_timer = QTimer(self)
        self.session_save_timer.setSingleShot(True)
        self.session_save_timer.timeout.connect(self.save_session_to_disk)

        self.load_session()
        if download_entries:
            self.load_entries(download_entries)

        if not self.entry_order:
            self.show_empty_state()
        else:
            self.clear_empty_state()
            self.reconcile_saved_torrents()
            self.queue_scheduler()

    def load_session(self):
        if not os.path.exists(SESSION_PATH):
            return

        try:
            with open(SESSION_PATH, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except Exception as exc:
            print(f"No se pudo cargar la sesión de descargas: {exc}")
            return

        raw_entries = payload.get("entries", []) if isinstance(payload, dict) else []
        for raw_entry in raw_entries:
            entry = self.normalize_entry(raw_entry, from_session=True)
            self.entries[entry["id"]] = entry
            self.entry_order.append(entry["id"])
            self.store_password_hint(entry.get("path", ""), entry.get("password"), entry.get("title"))
            self.ensure_entry_widget(entry)
            self.update_entry_visual(entry)

    def normalize_entry(self, raw_entry, from_session=False):
        entry_id = raw_entry.get("id") or uuid.uuid4().hex
        url_original = (raw_entry.get("url_original") or raw_entry.get("url") or "").strip()
        path = (raw_entry.get("path") or "").strip()
        title = (raw_entry.get("title") or "").strip() or self.default_entry_title(raw_entry, url_original, path)
        kind = raw_entry.get("download_type")
        if kind not in {"regular", "torrent"}:
            kind = "torrent" if self.is_torrent_url(url_original) else "regular"

        direct_links = []
        raw_direct_links = raw_entry.get("direct_links") or []
        if raw_entry.get("direct_url") and not raw_direct_links:
            raw_direct_links = [{
                "path": raw_entry.get("resolved_path") or path,
                "url": raw_entry.get("direct_url"),
                "headers": raw_entry.get("headers") or {},
                "cookies": raw_entry.get("cookies") or {},
                "status": raw_entry.get("status") or "waiting",
                "progress": raw_entry.get("progress", 0),
            }]

        for link in raw_direct_links:
            child_status = link.get("status", "waiting")
            if from_session and child_status in {"downloading", "resolving"}:
                child_status = "waiting"
            direct_links.append({
                "path": link.get("path") or path,
                "url": (link.get("url") or "").strip(),
                "headers": link.get("headers") or {},
                "cookies": link.get("cookies") or {},
                "status": child_status,
                "progress": int(link.get("progress", 0) or 0),
            })

        status = raw_entry.get("status") or "waiting"
        if from_session and kind == "regular" and status in {"downloading", "resolving"}:
            status = "waiting"

        entry = {
            "id": entry_id,
            "title": title,
            "path": path,
            "url_original": url_original,
            "password": raw_entry.get("password", "") or "",
            "download_type": kind,
            "status": status,
            "progress": int(raw_entry.get("progress", 0) or 0),
            "direct_url": raw_entry.get("direct_url", "") or "",
            "direct_links": direct_links,
            "torrent_gid": raw_entry.get("torrent_gid", "") or "",
            "torrent_hash": raw_entry.get("torrent_hash", "") or "",
            "speed_text": raw_entry.get("speed_text", "") or "",
            "error_text": raw_entry.get("error_text", "") or "",
        }
        if from_session:
            self.recompute_regular_status(entry)
        return entry

    def save_session_to_disk(self):
        payload = {
            "version": 1,
            "entries": [self.serialize_entry(self.entries[entry_id]) for entry_id in self.entry_order],
        }

        session_dir = os.path.dirname(SESSION_PATH)
        os.makedirs(session_dir, exist_ok=True)
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                delete=False,
                suffix=".json",
                dir=session_dir,
                encoding="utf-8",
            ) as tmp:
                json.dump(payload, tmp, indent=2, ensure_ascii=False)
                tmp_path = tmp.name
            os.replace(tmp_path, SESSION_PATH)
        except Exception as exc:
            print(f"No se pudo guardar la sesión de descargas: {exc}")

    def serialize_entry(self, entry):
        return {
            "id": entry["id"],
            "title": entry["title"],
            "path": entry["path"],
            "url_original": entry["url_original"],
            "password": entry["password"],
            "download_type": entry["download_type"],
            "status": entry["status"],
            "progress": entry.get("progress", 0),
            "direct_url": entry.get("direct_url", ""),
            "direct_links": [
                {
                    "path": link.get("path", ""),
                    "url": link.get("url", ""),
                    "headers": link.get("headers") or {},
                    "cookies": link.get("cookies") or {},
                    "status": link.get("status", "waiting"),
                    "progress": link.get("progress", 0),
                }
                for link in entry.get("direct_links", [])
            ],
            "torrent_gid": entry.get("torrent_gid", ""),
            "torrent_hash": entry.get("torrent_hash", ""),
            "speed_text": entry.get("speed_text", ""),
            "error_text": entry.get("error_text", ""),
        }

    def request_session_save(self):
        self.session_save_timer.start(150)

    def queue_scheduler(self):
        if self._scheduler_queued or self._closing:
            return
        self._scheduler_queued = True
        QTimer.singleShot(0, self.run_scheduler)

    def run_scheduler(self):
        self._scheduler_queued = False
        if self._closing:
            return

        for entry_id in self.entry_order:
            entry = self.entries.get(entry_id)
            if not entry or entry["download_type"] != "torrent":
                continue
            if entry["status"] == "waiting" and not entry.get("torrent_gid") and entry_id not in self.pending_torrent_entries:
                self.enqueue_torrent_entry(entry)

        while self.count_regular_slots_in_use() < self.max_parallel_downloads:
            if not self.start_next_regular_work():
                break

    def count_regular_slots_in_use(self):
        return len(self.active_resolutions) + len(self.active_file_downloads)

    def start_next_regular_work(self):
        for entry_id in self.entry_order:
            entry = self.entries.get(entry_id)
            if not entry or entry["download_type"] != "regular":
                continue
            if entry["status"] in {"finished", "cancelled", "error"}:
                continue
            if entry_id in self.active_resolutions:
                continue

            link_index = self.next_waiting_direct_link(entry)
            if link_index is not None:
                self.start_direct_download(entry, link_index)
                return True

            if not entry.get("direct_links"):
                self.start_resolution(entry)
                return True

        return False

    def start_resolution(self, entry):
        entry["status"] = "resolving"
        entry["error_text"] = ""
        self.update_entry_visual(entry)
        self.request_session_save()

        downloader = UniversalDownloader([{
            "url": entry["url_original"],
            "path": entry["path"],
            "password": entry["password"],
            "title": entry["title"],
        }])
        downloader.direct_links_ready.connect(
            lambda results, entry_id=entry["id"], instance=downloader: self.on_resolution_finished(entry_id, results, instance)
        )
        self.active_resolutions[entry["id"]] = downloader
        self.downloaders.append(downloader)
        downloader.start()

    def on_resolution_finished(self, entry_id, results, downloader):
        active_downloader = self.active_resolutions.pop(entry_id, None)
        entry = self.entries.get(entry_id)

        if downloader in self.downloaders:
            self.downloaders.remove(downloader)
        try:
            downloader.close()
            downloader.deleteLater()
        except Exception:
            pass

        if not entry or entry["status"] == "cancelled":
            self.queue_scheduler()
            return
        if active_downloader is None and entry["status"] != "resolving":
            self.queue_scheduler()
            return

        direct_links = self.convert_resolved_results(results)
        if not direct_links:
            entry["status"] = "error"
            entry["error_text"] = "No se pudieron obtener los enlaces directos."
            self.update_entry_visual(entry)
            self.request_session_save()
            self.queue_scheduler()
            return

        entry["direct_links"] = direct_links
        entry["direct_url"] = direct_links[0]["url"] if len(direct_links) == 1 else ""
        entry["status"] = "waiting"
        entry["progress"] = 0
        entry["error_text"] = ""
        self.recompute_regular_status(entry)
        self.update_entry_visual(entry)
        self.request_session_save()
        self.queue_scheduler()

    def convert_resolved_results(self, results):
        direct_links = []
        for item in results or []:
            if isinstance(item, dict) and item.get("type") == "direct":
                path = item.get("path", "")
                url = item.get("url", "")
                if path and url:
                    direct_links.append({
                        "path": path,
                        "url": url,
                        "headers": item.get("headers") or {},
                        "cookies": item.get("cookies") or {},
                        "status": "waiting",
                        "progress": 0,
                    })
            elif isinstance(item, (tuple, list)) and len(item) >= 2:
                path, url = item[0], item[1]
                if path and url:
                    direct_links.append({
                        "path": path,
                        "url": url,
                        "headers": {},
                        "cookies": {},
                        "status": "waiting",
                        "progress": 0,
                    })
        return direct_links

    def next_waiting_direct_link(self, entry):
        for index, link in enumerate(entry.get("direct_links", [])):
            if link.get("status") == "waiting":
                return index
        return None

    def start_direct_download(self, entry, link_index):
        link = entry["direct_links"][link_index]
        full_path = self.absolute_download_path(link.get("path") or entry["path"])
        if not full_path or not link.get("url"):
            link["status"] = "error"
            self.recompute_regular_status(entry)
            self.update_entry_visual(entry)
            self.request_session_save()
            return

        worker_index = self._next_worker_index
        self._next_worker_index += 1

        link["status"] = "downloading"
        entry["status"] = "downloading"
        entry["error_text"] = ""
        self.update_entry_visual(entry)
        self.request_session_save()

        signals = DownloadSignals()
        signals.progress.connect(self.update_progress)
        signals.cancelled.connect(self.on_direct_download_cancelled)
        signals.finished.connect(self.on_direct_download_finished)

        thread = FileDownloader(
            link["url"],
            full_path,
            worker_index,
            signals,
            headers=link.get("headers") or {},
            cookies=link.get("cookies") or {},
        )
        self.active_file_downloads[worker_index] = thread
        self.worker_context[worker_index] = (entry["id"], link_index)
        QThreadPool.globalInstance().start(thread)

    def enqueue_torrent_entry(self, entry):
        self.clear_empty_state()
        if not self._aria2_checked:
            ensure_aria2_running(self.folder_path, background=True)
            self._aria2_checked = True

        self.pending_torrent_entries.add(entry["id"])
        processor = TorrentProcessor([{
            "id": entry["id"],
            "url": entry["url_original"],
            "path": entry["path"] or self.folder_path,
        }], self.folder_path)
        processor.signals.item_processed.connect(self.on_torrent_processed)
        processor.signals.finished.connect(self.on_torrents_processed)
        QThreadPool.globalInstance().start(processor)

    def on_torrent_processed(self, entry_id, gid, error_text):
        self.pending_torrent_entries.discard(entry_id)
        entry = self.entries.get(entry_id)
        if not entry or entry["status"] == "cancelled":
            return

        if not gid:
            entry["status"] = "error"
            entry["error_text"] = error_text or "No se pudo agregar el torrent."
            self.update_entry_visual(entry)
            self.request_session_save()
            return

        entry["torrent_gid"] = gid
        entry["status"] = "waiting"
        entry["error_text"] = ""
        self.torrent_gid_to_entry[gid] = entry_id
        self.update_entry_visual(entry)
        self.request_session_save()
        self.ensure_torrent_timer_running()

    def on_torrents_processed(self):
        print("✅ Todos los torrents han sido agregados a Aria2")

    def load_entries(self, entries):
        if self._closing or not entries:
            return

        self.clear_empty_state()
        for raw_entry in entries:
            entry = self.normalize_entry(raw_entry, from_session=False)
            self.entries[entry["id"]] = entry
            self.entry_order.append(entry["id"])
            self.store_password_hint(entry.get("path", ""), entry.get("password"), entry.get("title"))
            self.ensure_entry_widget(entry)
            self.update_entry_visual(entry)
        self.request_session_save()
        self.queue_scheduler()

    def enqueue_external_entries(self, entries):
        if self._closing or not entries:
            return
        self.pending_external_entries.extend(entries)
        if not self.external_entries_timer.isActive():
            self.external_entries_timer.start(75)

    def process_external_entries(self):
        if self._closing or not self.pending_external_entries:
            return
        entries = self.pending_external_entries
        self.pending_external_entries = []
        self.load_entries(entries)

    def update_progress(self, worker_index, percent):
        context = self.worker_context.get(worker_index)
        if not context:
            return
        entry_id, link_index = context
        entry = self.entries.get(entry_id)
        if not entry:
            return
        try:
            entry["direct_links"][link_index]["progress"] = percent
        except IndexError:
            return
        self.recompute_regular_status(entry)
        self.update_entry_visual(entry)
        self.request_session_save()

    def on_direct_download_finished(self, worker_index, success):
        thread = self.active_file_downloads.pop(worker_index, None)
        context = self.worker_context.pop(worker_index, None)
        if thread is None or context is None:
            self.queue_scheduler()
            return

        entry_id, link_index = context
        entry = self.entries.get(entry_id)
        if not entry:
            self.queue_scheduler()
            return

        try:
            link = entry["direct_links"][link_index]
        except IndexError:
            self.queue_scheduler()
            return

        if success:
            link["status"] = "finished"
            link["progress"] = 100
        else:
            link["status"] = "error"
            entry["error_text"] = "La descarga no se pudo completar."

        self.recompute_regular_status(entry)
        self.update_entry_visual(entry)
        self.request_session_save()
        self.queue_scheduler()

    def on_direct_download_cancelled(self, worker_index):
        self.active_file_downloads.pop(worker_index, None)
        context = self.worker_context.pop(worker_index, None)
        if not context:
            self.queue_scheduler()
            return

        entry_id, link_index = context
        entry = self.entries.get(entry_id)
        if not entry:
            self.queue_scheduler()
            return

        try:
            entry["direct_links"][link_index]["status"] = "cancelled"
        except IndexError:
            self.queue_scheduler()
            return

        self.recompute_regular_status(entry)
        self.update_entry_visual(entry)
        self.request_session_save()
        self.queue_scheduler()

    def cancel_entry(self, entry_id):
        entry = self.entries.get(entry_id)
        if not entry or entry["status"] in {"finished", "cancelled"}:
            return

        downloader = self.active_resolutions.pop(entry_id, None)
        if downloader is not None:
            if downloader in self.downloaders:
                self.downloaders.remove(downloader)
            try:
                downloader.close()
                downloader.deleteLater()
            except Exception:
                pass

        worker_ids = [
            worker_index
            for worker_index, context in self.worker_context.items()
            if context[0] == entry_id
        ]
        for worker_index in worker_ids:
            downloader_thread = self.active_file_downloads.get(worker_index)
            if downloader_thread is not None:
                try:
                    downloader_thread.cancel()
                except Exception:
                    pass

        if entry["download_type"] == "torrent":
            entry["status"] = "cancelled"
            if entry.get("torrent_gid"):
                worker = TorrentCancelWorker(entry["torrent_gid"])
                worker.signals.finished.connect(self.on_torrent_cancel_finished)
                QThreadPool.globalInstance().start(worker)
            else:
                self.update_entry_visual(entry)
                self.request_session_save()
            return

        for link in entry.get("direct_links", []):
            if link.get("status") not in {"finished", "cancelled"}:
                link["status"] = "cancelled"
        entry["status"] = "cancelled"
        self.recompute_regular_status(entry)
        self.update_entry_visual(entry)
        self.request_session_save()
        self.queue_scheduler()

    def resume_entry(self, entry_id):
        entry = self.entries.get(entry_id)
        if not entry or entry["status"] != "cancelled":
            return

        entry["error_text"] = ""
        entry["progress"] = 0

        if entry["download_type"] == "torrent":
            entry["torrent_gid"] = ""
            entry["torrent_hash"] = ""
            entry["speed_text"] = ""
            entry["status"] = "waiting"
        else:
            if entry.get("direct_links"):
                for link in entry["direct_links"]:
                    if link.get("status") == "finished":
                        continue
                    link["status"] = "waiting"
                    link["progress"] = 0
            entry["status"] = "waiting"
            self.recompute_regular_status(entry)

        self.update_entry_visual(entry)
        self.request_session_save()
        self.queue_scheduler()

    def delete_entry(self, entry_id):
        entry = self.entries.pop(entry_id, None)
        if not entry:
            return

        if entry_id in self.entry_order:
            self.entry_order.remove(entry_id)

        item = self.entry_items.pop(entry_id, None)
        if item:
            group_box = item["container"].parentWidget()
            layout = item["container"].parentWidget().layout() if group_box else None
            if layout:
                layout.removeWidget(item["container"])
            item["container"].deleteLater()
            if group_box and layout and layout.count() == 0:
                group_key_to_remove = None
                for group_key, group in self.download_groups.items():
                    if group.get("box") is group_box:
                        group_key_to_remove = group_key
                        break
                if group_key_to_remove is not None:
                    self.inner_layout.removeWidget(group_box)
                    group_box.deleteLater()
                    del self.download_groups[group_key_to_remove]

        gid = entry.get("torrent_gid")
        if gid:
            self.torrent_gid_to_entry.pop(gid, None)

        if not self.entry_order:
            self.show_empty_state()

        self.request_session_save()

    def on_torrent_cancel_finished(self, gid, ok, error_text):
        entry_id = self.torrent_gid_to_entry.pop(gid, "")
        entry = self.entries.get(entry_id)
        if entry:
            entry["status"] = "cancelled"
            entry["error_text"] = "" if ok else error_text
            self.update_entry_visual(entry)
            self.request_session_save()
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

    def _normalize_group_path(self, group_path):
        if not group_path:
            return ""
        normalized = os.path.normpath(group_path)
        folder_base = os.path.normpath(self.folder_path)
        try:
            rel_to_base = os.path.relpath(normalized, folder_base)
            if rel_to_base == ".":
                return ""
            if not rel_to_base.startswith(".."):
                return rel_to_base
        except ValueError:
            pass
        return normalized

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

    def _create_download_item(self, group_path, display_name, initial_text, cancel_callback):
        group_key = self._normalize_group_path(group_path)
        item_name = display_name or "Archivo"
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
        resume_button = QPushButton("Reanudar")
        resume_button.clicked.connect(lambda checked=False: None)
        delete_button = QPushButton("Eliminar")
        delete_button.clicked.connect(lambda checked=False: None)
        header.addWidget(label, 1)
        header.addWidget(resume_button)
        header.addWidget(delete_button)
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
            "resume_button": resume_button,
            "delete_button": delete_button,
            "name": item_name,
            "status": "waiting",
        }

    def ensure_entry_widget(self, entry):
        item = self.entry_items.get(entry["id"])
        if item:
            return item

        item = self._create_download_item(
            entry.get("path", ""),
            entry.get("title", ""),
            self.entry_label_text(entry),
            lambda checked=False, entry_id=entry["id"]: self.cancel_entry(entry_id),
        )
        try:
            item["resume_button"].clicked.disconnect()
        except Exception:
            pass
        item["resume_button"].clicked.connect(lambda checked=False, entry_id=entry["id"]: self.resume_entry(entry_id))
        try:
            item["delete_button"].clicked.disconnect()
        except Exception:
            pass
        item["delete_button"].clicked.connect(lambda checked=False, entry_id=entry["id"]: self.delete_entry(entry_id))
        self.entry_items[entry["id"]] = item
        return item

    def update_entry_visual(self, entry):
        item = self.ensure_entry_widget(entry)
        item["name"] = entry["title"]
        item["status"] = entry["status"]
        item["label"].setText(self.entry_label_text(entry))

        progress = self.entry_progress(entry)
        entry["progress"] = progress
        item["bar"].setValue(progress)

        if entry["status"] == "cancelled":
            item["cancel_button"].setEnabled(False)
            item["cancel_button"].hide()
            item["resume_button"].setEnabled(True)
            item["resume_button"].show()
            item["delete_button"].setEnabled(True)
            item["delete_button"].show()
            item["bar"].hide()
        elif entry["status"] in {"finished", "error"}:
            item["cancel_button"].setEnabled(False)
            item["cancel_button"].hide()
            item["resume_button"].setEnabled(False)
            item["resume_button"].hide()
            item["delete_button"].setEnabled(False)
            item["delete_button"].hide()
            item["bar"].setVisible(entry["status"] == "finished")
        else:
            item["cancel_button"].setEnabled(True)
            item["cancel_button"].show()
            item["resume_button"].setEnabled(False)
            item["resume_button"].hide()
            item["delete_button"].setEnabled(False)
            item["delete_button"].hide()
            item["bar"].setVisible(entry["status"] not in {"waiting"})

    def entry_label_text(self, entry):
        status = entry.get("status", "waiting")
        title = entry.get("title", "Archivo")
        speed_text = entry.get("speed_text", "")

        if entry["download_type"] == "torrent":
            if status == "finished":
                return f"✅ Torrent completado: {title}"
            if status == "cancelled":
                return f"⏹ Torrent cancelado: {title}"
            if status == "error":
                return f"❌ Error: {title}"
            if status == "downloading":
                return f"Descargando torrent: {title}{speed_text}"
            return f"En espera: {title}"

        if status == "finished":
            return f"✅ Completado: {title}"
        if status == "cancelled":
            return f"⏹ Cancelado: {title}"
        if status == "error":
            return f"❌ Error: {title}"
        if status == "resolving":
            return f"Resolviendo: {title}"
        if status == "downloading":
            return f"Descargando: {title}"
        return f"En espera: {title}"

    def entry_progress(self, entry):
        if entry["download_type"] == "torrent":
            return int(entry.get("progress", 0) or 0)

        direct_links = entry.get("direct_links", [])
        if not direct_links:
            return 0
        total = sum(int(link.get("progress", 0) or 0) for link in direct_links)
        return int(total / len(direct_links))

    def recompute_regular_status(self, entry):
        if entry["download_type"] != "regular":
            return

        if entry["id"] in self.active_resolutions:
            entry["status"] = "resolving"
            return

        direct_links = entry.get("direct_links", [])
        if not direct_links:
            if entry["status"] not in {"finished", "cancelled", "error"}:
                entry["status"] = "waiting"
            return

        statuses = {link.get("status", "waiting") for link in direct_links}
        if "downloading" in statuses:
            entry["status"] = "downloading"
        elif statuses == {"finished"}:
            entry["status"] = "finished"
        elif statuses <= {"cancelled"}:
            entry["status"] = "cancelled"
        elif "waiting" in statuses:
            entry["status"] = "waiting"
        elif "error" in statuses:
            entry["status"] = "error"
        else:
            entry["status"] = "waiting"

    def absolute_download_path(self, relative_path):
        if not relative_path:
            return ""
        if os.path.isabs(relative_path):
            return os.path.normpath(relative_path)
        return os.path.normpath(os.path.join(self.folder_path, relative_path))

    def default_entry_title(self, raw_entry, url, path):
        if path:
            return os.path.basename(os.path.normpath(path))
        if url:
            return os.path.basename(url.rstrip("/").split("?")[0]) or url
        return "Archivo"

    def is_torrent_url(self, url):
        return bool(url) and (url.startswith("magnet:?") or url.endswith(".torrent"))

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
            QThreadPool.globalInstance().setMaxThreadCount(self.max_parallel_downloads)
            self.queue_scheduler()

    def ensure_torrent_timer_running(self):
        if not self.torrent_timer.isActive():
            self.torrent_timer.start(3000)

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
        if "No se pudo conectar a Aria2" in message:
            print("⚠️ Aria2 no está disponible - reintentando inicio...")
            if ensure_aria2_running(self.folder_path, background=True):
                print("✅ Aria2 reiniciado exitosamente")
        elif "Connection refused" not in message and "timeout" not in message:
            print(f"Error al actualizar progreso de torrents: {message}")

    def reconcile_saved_torrents(self):
        pending = [
            entry for entry in self.entries.values()
            if entry["download_type"] == "torrent" and entry["status"] not in {"finished", "cancelled"}
        ]
        if not pending:
            return

        if not self._aria2_checked:
            ensure_aria2_running(self.folder_path, background=True)
            self._aria2_checked = True
        self.ensure_torrent_timer_running()

        client = Aria2Client()
        try:
            downloads = client.get_active_downloads() + client.get_stopped_downloads(100)
        except Exception as exc:
            print(f"No se pudo reconciliar torrents guardados: {exc}")
            self.queue_scheduler()
            return

        by_gid = {download.gid: download for download in downloads}
        for entry in pending:
            gid = entry.get("torrent_gid")
            if gid and gid in by_gid:
                self.apply_torrent_update(entry, by_gid[gid])
            else:
                entry["torrent_gid"] = ""
                entry["torrent_hash"] = ""
                if entry["status"] != "error":
                    entry["status"] = "waiting"
                entry["progress"] = 0
                entry["speed_text"] = ""
                self.update_entry_visual(entry)

        self.request_session_save()
        self.queue_scheduler()

    def on_torrent_data_received(self, torrents):
        if self._closing:
            return

        current_by_gid = {torrent.gid: torrent for torrent in torrents}
        for entry_id in self.entry_order:
            entry = self.entries.get(entry_id)
            if not entry or entry["download_type"] != "torrent":
                continue
            gid = entry.get("torrent_gid")
            if gid and gid in current_by_gid:
                self.apply_torrent_update(entry, current_by_gid[gid])
            elif gid and entry["status"] == "downloading":
                entry["status"] = "waiting"
                entry["progress"] = 0
                entry["speed_text"] = ""
                self.update_entry_visual(entry)

        self.request_session_save()
        self.queue_scheduler()

    def apply_torrent_update(self, entry, torrent):
        entry["torrent_gid"] = torrent.gid
        entry["torrent_hash"] = torrent.hash
        self.torrent_gid_to_entry[torrent.gid] = entry["id"]

        percent = int(torrent.progress * 100)
        entry["progress"] = percent
        title = self.get_clean_torrent_name(torrent.name) or entry["title"]
        if title:
            entry["title"] = title

        speed_text = ""
        if hasattr(torrent, "dlspeed") and torrent.dlspeed > 0:
            speed_mb = torrent.dlspeed / (1024 * 1024)
            if speed_mb >= 1:
                speed_text = f" - {speed_mb:.1f} MB/s"
            else:
                speed_text = f" - {torrent.dlspeed / 1024:.0f} KB/s"
        entry["speed_text"] = speed_text

        state = getattr(torrent, "state", "")
        if percent >= 100 or state in {"complete", "uploading"}:
            entry["status"] = "finished"
            entry["speed_text"] = ""
        elif state == "error":
            entry["status"] = "error"
        else:
            entry["status"] = "downloading"
        self.update_entry_visual(entry)

    def get_clean_torrent_name(self, name):
        if not name:
            return "Torrent desconocido"
        clean_name = name[10:] if name.startswith("[METADATA]") else name
        clean_name = clean_name.strip()
        return clean_name or name

    def has_active_work(self):
        if self.active_file_downloads or self.active_resolutions or self.pending_torrent_entries:
            return True
        return any(
            entry["status"] in {"downloading", "resolving"}
            for entry in self.entries.values()
        )

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

    def prepare_session_for_shutdown(self):
        for entry in self.entries.values():
            if entry["download_type"] != "regular":
                continue
            if entry["status"] in {"downloading", "resolving"}:
                entry["status"] = "waiting"
            for link in entry.get("direct_links", []):
                if link.get("status") == "downloading":
                    link["status"] = "waiting"
            self.recompute_regular_status(entry)
            self.update_entry_visual(entry)

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
        self.worker_context.clear()

        for _entry_id, downloader in list(self.active_resolutions.items()):
            try:
                downloader.close()
                downloader.deleteLater()
            except Exception:
                pass
        self.active_resolutions.clear()

        for downloader in list(self.downloaders):
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

        self.prepare_session_for_shutdown()
        self.save_session_to_disk()
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
