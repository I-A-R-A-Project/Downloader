import json
import os
import re
import tempfile
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from urllib.parse import urlparse

import requests
from PyQt5.QtCore import QEventLoop

from config import APPDATA, DEFAULT_CONFIG, load_config, normalize_path
from download_manager.browser import UniversalDownloader
from download_manager.direct_file import build_download_path, resolve_direct_filename
from download_manager.torrent import Aria2Client, ensure_aria2_running
from download_manager.window import ArchiveExtractWorker

try:
    from tqdm import tqdm
except ImportError as exc:
    raise RuntimeError("Missing dependency: tqdm") from exc


CHUNK_SIZE = 8192
DIRECT_EXTENSIONS = {
    ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz",
    ".iso", ".exe", ".msi", ".apk", ".pdf", ".cbz", ".cbr",
}
ARCHIVE_EXTENSIONS = {".zip", ".rar", ".7z"}
SESSION_PATH = os.path.join(APPDATA, "MediaSearchPrototype", "download_state.json")


def run_tui_download_manager(app, entries):
    manager = TuiDownloadManager(app, entries)
    return manager.run()


class TuiDownloadManager:
    def __init__(self, app, entries):
        self.app = app
        self.config = load_config()
        self.folder_path = normalize_path(self.config.get("folder_path", DEFAULT_CONFIG["folder_path"]))
        self.max_parallel_downloads = max(
            1,
            int(self.config.get("max_parallel_downloads", DEFAULT_CONFIG["max_parallel_downloads"]) or 1),
        )
        self.auto_extract_archives = bool(
            self.config.get("auto_extract_archives", DEFAULT_CONFIG["auto_extract_archives"])
        )
        self.delete_archive_after_extract = bool(
            self.config.get("delete_archive_after_extract", DEFAULT_CONFIG["delete_archive_after_extract"])
        )
        self.password_hints_written = set()
        self._print_lock = threading.Lock()
        self.entries = []

        self.load_session()
        self.load_entries(entries or [])

    def run(self):
        if not self.entries:
            self.log("No entries.")
            return 0

        self.log(f"History entries: {len(self.entries)}")

        regular_entries = []
        torrent_entries = []
        for entry in self.entries:
            self.store_password_hint(entry)
            if entry["download_type"] == "torrent":
                torrent_entries.append(entry)
            else:
                regular_entries.append(entry)

        failed = False

        if regular_entries:
            pending_regular = [entry for entry in regular_entries if entry.get("status") not in {"finished", "cancelled", "error"}]
            if pending_regular:
                self.log(f"Resolve regular entries: {len(pending_regular)}")
            for entry in pending_regular:
                if not entry.get("direct_links") and not self.resolve_entry(entry):
                    failed = True
            if not self.download_regular_entries(regular_entries):
                failed = True

        if torrent_entries:
            pending_torrents = [entry for entry in torrent_entries if entry.get("status") != "finished"]
            if pending_torrents:
                self.log(f"Process torrent entries: {len(pending_torrents)}")
            if pending_torrents and not self.download_torrent_entries(torrent_entries):
                failed = True

        self.save_session_to_disk()
        return 1 if failed else 0

    def load_session(self):
        if not os.path.exists(SESSION_PATH):
            return

        try:
            with open(SESSION_PATH, "r", encoding="utf-8-sig") as fh:
                payload = json.load(fh)
        except Exception as exc:
            self.log(f"[error] session load failed: {exc}")
            return

        raw_entries = payload.get("entries", []) if isinstance(payload, dict) else []
        for raw_entry in raw_entries:
            self.entries.append(self.normalize_entry(raw_entry, from_session=True))

    def load_entries(self, raw_entries):
        for raw_entry in raw_entries:
            self.entries.append(self.normalize_entry(raw_entry, from_session=False))
        if raw_entries:
            self.save_session_to_disk()

    def normalize_entry(self, raw_entry, from_session=False):
        url = (raw_entry.get("url_original") or raw_entry.get("url") or "").strip()
        path = normalize_path((raw_entry.get("path") or "").strip()) or self.folder_path
        title = (raw_entry.get("title") or "").strip() or self.default_title(url, path)
        kind = raw_entry.get("download_type")
        if kind not in {"regular", "torrent"}:
            kind = "torrent" if self.is_torrent_url(url) else "regular"

        direct_links = []
        raw_direct_links = raw_entry.get("direct_links") or []
        if raw_entry.get("direct_url") and not raw_direct_links:
            raw_direct_links = [{
                "path": normalize_path(raw_entry.get("resolved_path") or path),
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
                "path": normalize_path(link.get("path") or path),
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
            "id": raw_entry.get("id") or uuid.uuid4().hex,
            "url_original": url,
            "path": path,
            "title": title,
            "password": (raw_entry.get("password") or "").strip(),
            "download_type": kind,
            "status": status,
            "progress": int(raw_entry.get("progress", 0) or 0),
            "direct_url": raw_entry.get("direct_url", "") or "",
            "direct_links": direct_links,
            "torrent_gid": raw_entry.get("torrent_gid", "") or "",
            "torrent_hash": raw_entry.get("torrent_hash", "") or "",
            "speed_text": raw_entry.get("speed_text", "") or "",
            "error_text": raw_entry.get("error_text", "") or "",
            "extract_status": raw_entry.get("extract_status", "") or "",
            "extract_error": raw_entry.get("extract_error", "") or "",
            "resolution_retry_count": int(raw_entry.get("resolution_retry_count", 0) or 0),
            "archive_retry_count": int(raw_entry.get("archive_retry_count", 0) or 0),
            "failed": False,
        }
        if from_session and kind == "regular":
            self.recompute_regular_status(entry)
        return entry

    def serialize_entry(self, entry):
        return {
            "id": entry["id"],
            "title": entry["title"],
            "path": entry["path"],
            "url_original": entry["url_original"],
            "password": entry.get("password", ""),
            "download_type": entry["download_type"],
            "status": entry.get("status", "waiting"),
            "progress": entry.get("progress", 0),
            "direct_url": entry.get("direct_url", ""),
            "direct_links": [
                {
                    "path": normalize_path(link.get("path", "")),
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
            "extract_status": entry.get("extract_status", ""),
            "extract_error": entry.get("extract_error", ""),
            "resolution_retry_count": entry.get("resolution_retry_count", 0),
            "archive_retry_count": entry.get("archive_retry_count", 0),
        }

    def save_session_to_disk(self):
        payload = {
            "version": 1,
            "entries": [self.serialize_entry(entry) for entry in self.entries],
        }
        session_dir = os.path.dirname(SESSION_PATH)
        os.makedirs(session_dir, exist_ok=True)
        tmp_path = ""
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
            self.log(f"[error] session save failed: {exc}")
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def default_title(self, url, path):
        filename = os.path.basename(urlparse(url).path.rstrip("/"))
        if filename:
            return filename
        if path:
            return os.path.basename(path.rstrip("\\/")) or path
        return "download"

    def is_torrent_url(self, url):
        lower_url = (url or "").lower()
        return lower_url.startswith("magnet:?") or lower_url.endswith(".torrent")

    def is_direct_file_url(self, url):
        ext = os.path.splitext(urlparse(url).path or "")[1].lower()
        return ext in DIRECT_EXTENSIONS

    def log(self, message):
        with self._print_lock:
            tqdm.write(message)

    def resolve_entry(self, entry):
        if not entry.get("url_original"):
            entry["failed"] = True
            entry["status"] = "error"
            entry["error_text"] = "Missing url."
            self.save_session_to_disk()
            self.log(f"[error] {entry['title']} missing url")
            return False

        if self.is_direct_file_url(entry["url_original"]):
            filename = resolve_direct_filename(entry["url_original"])
            entry["direct_links"] = [{
                "path": build_download_path(entry["path"], filename),
                "url": entry["url_original"],
                "headers": {},
                "cookies": {},
                "status": "waiting",
                "progress": 0,
            }]
            entry["direct_url"] = entry["url_original"]
            entry["status"] = "waiting"
            entry["error_text"] = ""
            self.save_session_to_disk()
            return True

        entry["status"] = "resolving"
        entry["error_text"] = ""
        self.save_session_to_disk()
        results = self.resolve_with_browser(entry)
        direct_links = self.convert_resolved_results(results)
        if not direct_links:
            entry["failed"] = True
            entry["status"] = "error"
            entry["error_text"] = "No se pudieron obtener los enlaces directos."
            self.save_session_to_disk()
            self.log(f"[error] resolve failed {entry['title']}")
            return False

        entry["direct_links"] = direct_links
        entry["direct_url"] = direct_links[0]["url"] if len(direct_links) == 1 else ""
        entry["status"] = "waiting"
        entry["progress"] = 0
        entry["error_text"] = ""
        self.save_session_to_disk()
        return True

    def resolve_with_browser(self, entry):
        loop = QEventLoop()
        holder = {"results": []}
        downloader = UniversalDownloader([{
            "url": entry["url_original"],
            "path": entry["path"],
            "password": entry["password"],
            "title": entry["title"],
        }])

        def _finish(results):
            holder["results"] = results or []
            loop.quit()

        downloader.direct_links_ready.connect(_finish)
        downloader.start()
        loop.exec_()
        try:
            downloader.close()
            downloader.deleteLater()
        except Exception:
            pass
        self.app.processEvents()
        return holder["results"]

    def convert_resolved_results(self, results):
        direct_links = []
        for item in results or []:
            if isinstance(item, dict) and item.get("type") == "direct":
                path = normalize_path(item.get("path") or "")
                url = item.get("url") or ""
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
                        "path": normalize_path(path),
                        "url": url,
                        "headers": {},
                        "cookies": {},
                        "status": "waiting",
                        "progress": 0,
                    })
        return direct_links

    def absolute_download_path(self, path):
        normalized = normalize_path(path or "")
        if not normalized:
            return self.folder_path
        if os.path.isabs(normalized):
            return normalized
        return normalize_path(os.path.join(self.folder_path, normalized))

    def download_regular_entries(self, regular_entries):
        tasks = []
        for entry in regular_entries:
            if entry.get("failed") or entry.get("status") in {"finished", "cancelled", "error"}:
                continue
            for link in entry.get("direct_links", []):
                if link.get("status") == "waiting":
                    tasks.append((entry, link))

        if not tasks:
            return not any(entry.get("failed") for entry in regular_entries)

        results = []
        with ThreadPoolExecutor(max_workers=self.max_parallel_downloads) as executor:
            pending = {}
            for position, (entry, link) in enumerate(tasks):
                future = executor.submit(self.download_direct_link, entry, link, position)
                pending[future] = entry

            while pending:
                done, _ = wait(pending.keys(), return_when=FIRST_COMPLETED)
                self.app.processEvents()
                for future in done:
                    entry = pending.pop(future)
                    ok = False
                    try:
                        ok = bool(future.result())
                    except Exception as exc:
                        self.log(f"[error] download crashed {entry['title']}: {exc}")
                    results.append(ok)

        if self.auto_extract_archives:
            for entry in regular_entries:
                if entry.get("failed") or entry.get("status") != "finished":
                    continue
                if entry.get("extract_status") == "done":
                    continue
                if not self.extract_entry(entry):
                    results.append(False)

        return all(results) if results else True

    def download_direct_link(self, entry, link, position):
        url = link.get("url") or ""
        target_path = self.absolute_download_path(link.get("path") or entry["path"])
        os.makedirs(os.path.dirname(target_path) or ".", exist_ok=True)
        headers = dict(link.get("headers") or {})
        cookies = link.get("cookies") or {}
        link["status"] = "downloading"
        entry["status"] = "downloading"
        entry["error_text"] = ""
        self.save_session_to_disk()

        existing_size = os.path.getsize(target_path) if os.path.exists(target_path) else 0
        if existing_size:
            headers["Range"] = f"bytes={existing_size}-"

        short_name = self.short_label(entry["title"])
        with tqdm(
            total=None,
            desc=short_name,
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
            position=position,
            leave=True,
            dynamic_ncols=True,
        ) as bar:
            if existing_size:
                bar.update(existing_size)

            try:
                with requests.get(
                    url,
                    stream=True,
                    headers=headers,
                    cookies=cookies,
                    timeout=30,
                ) as response:
                    response.raise_for_status()
                    total_size = self.compute_total_size(response, existing_size)
                    if total_size:
                        bar.total = total_size
                        bar.refresh()

                    mode = "ab" if existing_size else "wb"
                    with open(target_path, mode) as fh:
                        for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                            if not chunk:
                                continue
                            fh.write(chunk)
                            bar.update(len(chunk))
                            if bar.total:
                                link["progress"] = int((bar.n / bar.total) * 100)
                            entry["progress"] = self.entry_progress(entry)

                if bar.total and bar.n < bar.total:
                    bar.total = bar.n
                    bar.refresh()
                link["status"] = "finished"
                link["progress"] = 100
                entry["error_text"] = ""
                self.recompute_regular_status(entry)
                self.save_session_to_disk()
                return True
            except Exception as exc:
                bar.set_postfix_str("error")
                self.log(f"[error] {entry['title']}: {exc}")
                entry["failed"] = True
                link["status"] = "error"
                entry["status"] = "error"
                entry["error_text"] = "La descarga no se pudo completar."
                self.recompute_regular_status(entry)
                self.save_session_to_disk()
                return False

    def compute_total_size(self, response, existing_size):
        content_range = response.headers.get("Content-Range", "")
        match = re.search(r"/(\d+)$", content_range)
        if match:
            return int(match.group(1))
        content_length = response.headers.get("content-length")
        if not content_length:
            return 0
        return int(content_length) + existing_size

    def recompute_regular_status(self, entry):
        direct_links = entry.get("direct_links", [])
        if not direct_links:
            return
        statuses = {link.get("status", "waiting") for link in direct_links}
        if "downloading" in statuses:
            entry["status"] = "downloading"
        elif statuses == {"finished"}:
            entry["status"] = "finished"
        elif "waiting" in statuses:
            entry["status"] = "waiting"
        elif "error" in statuses and statuses.issubset({"finished", "error"}):
            entry["status"] = "error"
        else:
            entry["status"] = "waiting"
        entry["progress"] = self.entry_progress(entry)

    def entry_progress(self, entry):
        direct_links = entry.get("direct_links", [])
        if not direct_links:
            return int(entry.get("progress", 0) or 0)
        values = [int(link.get("progress", 0) or 0) for link in direct_links]
        return int(sum(values) / len(values)) if values else 0

    def extract_entry(self, entry):
        archive_path = self.find_extractable_archive(entry)
        if not archive_path:
            return True

        self.log(f"Extract {entry['title']}")
        entry["extract_status"] = "running"
        entry["extract_error"] = ""
        self.save_session_to_disk()
        worker = ArchiveExtractWorker(entry["id"], archive_path, os.path.dirname(archive_path), entry.get("password", ""))
        holder = {"ok": False, "error": ""}

        def _finish(_, ok, error_text):
            holder["ok"] = bool(ok)
            holder["error"] = error_text or ""

        worker.signals.finished.connect(_finish)
        worker.run()
        self.app.processEvents()
        if not holder["ok"]:
            self.log(f"[error] extract failed {entry['title']}: {holder['error'] or 'unknown'}")
            entry["failed"] = True
            entry["extract_status"] = "error"
            entry["extract_error"] = holder["error"] or "Unknown extract error."
            self.save_session_to_disk()
            return False

        entry["extract_status"] = "done"
        entry["extract_error"] = ""
        if self.delete_archive_after_extract:
            for path in self.archive_paths_for_entry(entry):
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError as exc:
                        self.log(f"[error] delete archive {os.path.basename(path)}: {exc}")
                        entry["failed"] = True
                        entry["extract_error"] = str(exc)
                        self.save_session_to_disk()
                        return False
        self.save_session_to_disk()
        return True

    def archive_paths_for_entry(self, entry):
        paths = []
        for link in entry.get("direct_links", []):
            full_path = self.absolute_download_path(link.get("path") or entry.get("path", ""))
            if full_path and os.path.exists(full_path):
                paths.append(full_path)
        return paths

    def find_extractable_archive(self, entry):
        paths = self.archive_paths_for_entry(entry)
        multipart = [path for path in paths if ".part" in os.path.basename(path).lower()]
        if multipart:
            return self.find_first_archive_part(multipart)
        for path in paths:
            if self.is_extractable_archive(path):
                return path
        return ""

    def is_extractable_archive(self, file_path):
        lower_path = file_path.lower()
        return os.path.splitext(lower_path)[1] in ARCHIVE_EXTENSIONS or bool(
            re.search(r"\.part\d+\.(rar|7z|zip)$", lower_path)
        )

    def find_first_archive_part(self, paths):
        patterns = (
            r"\.part0*1\.(rar|7z|zip)$",
            r"\.part1\.(rar|7z|zip)$",
        )
        for pattern in patterns:
            for path in sorted(paths):
                if re.search(pattern, path.lower()):
                    return path
        return sorted(paths)[0] if paths else ""

    def download_torrent_entries(self, entries):
        if not ensure_aria2_running(self.folder_path, background=False):
            for entry in entries:
                entry["failed"] = True
                entry["status"] = "error"
                entry["error_text"] = "No se pudo iniciar Aria2."
            self.save_session_to_disk()
            self.log("[error] aria2 not running")
            return False

        client = Aria2Client()
        gids = {}
        for entry in entries:
            if entry.get("status") == "finished":
                continue
            gid = entry.get("torrent_gid") or self.add_torrent_entry(client, entry)
            if not gid:
                entry["failed"] = True
                entry["status"] = "error"
                entry["error_text"] = "No se pudo agregar el torrent."
                continue
            entry["torrent_gid"] = gid
            entry["status"] = "waiting"
            entry["error_text"] = ""
            gids[entry["id"]] = gid
        self.save_session_to_disk()

        if not gids:
            return False

        bars = {}
        positions = {entry_id: idx for idx, entry_id in enumerate(gids.keys())}
        try:
            for entry_id in gids:
                entry = self.entry_by_id(entry_id)
                bars[entry_id] = tqdm(
                    total=100,
                    desc=self.short_label(entry["title"]),
                    unit="%",
                    position=positions[entry_id],
                    leave=True,
                    dynamic_ncols=True,
                )

            pending = set(gids.keys())
            while pending:
                self.app.processEvents()
                time.sleep(1)
                finished_now = []
                for entry_id in list(pending):
                    entry = self.entry_by_id(entry_id)
                    status = client.get_download_status(gids[entry_id])
                    if not status:
                        bars[entry_id].set_postfix_str("missing")
                        entry["failed"] = True
                        entry["status"] = "error"
                        entry["error_text"] = "Torrent no encontrado en Aria2."
                        finished_now.append(entry_id)
                        continue

                    percent = int(status.progress * 100) if status.total_size else 0
                    bar = bars[entry_id]
                    bar.n = max(0, min(100, percent))
                    postfix = self.format_speed(status.dlspeed)
                    if postfix:
                        bar.set_postfix_str(postfix)
                    bar.refresh()

                    entry["progress"] = percent
                    entry["speed_text"] = postfix
                    if status.state in {"downloading", "queuedDL", "pausedDL"}:
                        entry["status"] = "downloading"

                    if status.state == "error":
                        entry["failed"] = True
                        entry["status"] = "error"
                        entry["error_text"] = "Torrent con error."
                        finished_now.append(entry_id)
                    elif status.state == "uploading":
                        bar.n = 100
                        bar.refresh()
                        entry["status"] = "finished"
                        entry["progress"] = 100
                        entry["speed_text"] = ""
                        entry["error_text"] = ""
                        finished_now.append(entry_id)

                if finished_now:
                    self.save_session_to_disk()
                for entry_id in finished_now:
                    pending.discard(entry_id)
        finally:
            for bar in bars.values():
                bar.close()

        self.save_session_to_disk()
        return not any(entry.get("failed") for entry in entries)

    def add_torrent_entry(self, client, entry):
        target_dir = self.absolute_download_path(entry["path"])
        os.makedirs(target_dir, exist_ok=True)
        url = entry["url_original"]

        try:
            if url.startswith("magnet:?"):
                return client.add_magnet(url, target_dir)

            response = requests.get(url, timeout=30)
            response.raise_for_status()
            with tempfile.NamedTemporaryFile(suffix=".torrent", delete=False) as fh:
                fh.write(response.content)
                temp_path = fh.name
            try:
                return client.add_torrent_file(temp_path, target_dir)
            finally:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
        except Exception as exc:
            self.log(f"[error] torrent add failed {entry['title']}: {exc}")
            return None

    def format_speed(self, speed):
        if not speed:
            return ""
        units = ["B/s", "KB/s", "MB/s", "GB/s"]
        value = float(speed)
        unit_index = 0
        while value >= 1024 and unit_index < len(units) - 1:
            value /= 1024.0
            unit_index += 1
        return f"{value:.1f}{units[unit_index]}"

    def store_password_hint(self, entry):
        path_hint = self.absolute_download_path(entry.get("path", ""))
        password = entry.get("password", "")
        if not password or not path_hint:
            return

        note_key = (path_hint, password, entry.get("title", ""))
        if note_key in self.password_hints_written:
            return

        try:
            os.makedirs(path_hint, exist_ok=True)
            note_path = os.path.join(path_hint, "__passwords__.txt")
            with open(note_path, "a", encoding="utf-8") as fh:
                fh.write(f"[{entry['title']}]\n{password}\n\n")
            self.password_hints_written.add(note_key)
        except Exception as exc:
            self.log(f"[error] password hint failed {entry['title']}: {exc}")

    def short_label(self, value, limit=40):
        text = (value or "download").replace("\n", " ").strip()
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."

    def entry_by_id(self, entry_id):
        for entry in self.entries:
            if entry["id"] == entry_id:
                return entry
        raise KeyError(entry_id)
