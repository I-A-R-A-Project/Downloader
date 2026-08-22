import os
import re
import shutil
import subprocess
from urllib.parse import parse_qs, urlparse

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal


YOUTUBE_URL_RE = re.compile(r"^https?://(?:www\.)?(?:youtube\.com|youtu\.be)/", re.IGNORECASE)
YT_DLP_PROGRESS_RE = re.compile(r"\[download\]\s+(\d+(?:\.\d+)?)%")
YT_DLP_PLAYLIST_RE = re.compile(r"\[download\]\s+Downloading item\s+(\d+)\s+of\s+(\d+)", re.IGNORECASE)
YT_DLP_SPEED_RE = re.compile(r"\sat\s+([0-9A-Za-z._~/-]+(?:i?B/s)?)")
YT_DLP_DESTINATION_RE = re.compile(r"\[download\]\s+Destination:\s+(.+)")
YT_DLP_MERGE_RE = re.compile(r"\[Merger\]\s+Merging formats into\s+\"?(.+?)\"?$")
YT_DLP_MOVE_RE = re.compile(r"\[download\]\s+(.+?) has already been downloaded")
YT_DLP_ERROR_RE = re.compile(r"^ERROR:\s*(.+)$", re.IGNORECASE)


def is_youtube_url(url):
    return bool(YOUTUBE_URL_RE.match((url or "").strip()))


def detect_youtube_mode(url):
    parsed = urlparse((url or "").strip())
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").lower()
    query = parse_qs(parsed.query or "")

    if "youtube.com" in host and path == "/playlist" and query.get("list"):
        return "playlist"
    return "video"


def find_yt_dlp_executable():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        os.path.join(repo_root, "yt-dlp.exe"),
        shutil.which("yt-dlp.exe"),
        shutil.which("yt-dlp"),
    ]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return os.path.normpath(candidate)
    return ""


def find_ffmpeg_executable():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        os.path.join(repo_root, "ffmpeg.exe"),
        shutil.which("ffmpeg.exe"),
        shutil.which("ffmpeg"),
    ]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return os.path.normpath(candidate)
    return ""


def build_yt_dlp_command(exe_path, url, target_dir, mode, ffmpeg_path=""):
    playlist_mode = "playlist" if mode == "playlist" else "video"
    output_template = "%(title)s [%(id)s].%(ext)s"
    if playlist_mode == "playlist":
        output_template = "%(playlist_index)s - %(title)s [%(id)s].%(ext)s"

    format_selector = "best[ext=mp4]/best"
    command = [
        exe_path,
        "--newline",
        "--progress",
        "--no-warnings",
        "--ignore-errors",
        "--restrict-filenames",
        "--windows-filenames",
        "-P",
        target_dir,
        "-o",
        output_template,
        "-f",
        format_selector,
        "--yes-playlist" if playlist_mode == "playlist" else "--no-playlist",
    ]
    if ffmpeg_path:
        command.extend([
            "--ffmpeg-location",
            ffmpeg_path,
            "-f",
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/bestvideo*+bestaudio/best",
            "--merge-output-format",
            "mp4",
        ])
        del command[command.index("-f"):command.index("-f") + 2]
    command.append(url)
    return command


def _cleanup_title(raw_value):
    file_name = os.path.basename((raw_value or "").strip().strip('"'))
    if not file_name:
        return ""
    return os.path.splitext(file_name)[0].strip()


def _parse_progress_line(line, state):
    playlist_match = YT_DLP_PLAYLIST_RE.search(line)
    if playlist_match:
        state["item_index"] = max(1, int(playlist_match.group(1)))
        state["item_total"] = max(1, int(playlist_match.group(2)))

    for pattern in (YT_DLP_DESTINATION_RE, YT_DLP_MERGE_RE, YT_DLP_MOVE_RE):
        match = pattern.search(line)
        if match:
            title = _cleanup_title(match.group(1))
            if title:
                state["title"] = title

    progress_match = YT_DLP_PROGRESS_RE.search(line)
    if not progress_match:
        return None

    file_percent = max(0.0, min(100.0, float(progress_match.group(1))))
    item_total = max(1, int(state.get("item_total", 1) or 1))
    item_index = min(max(1, int(state.get("item_index", 1) or 1)), item_total)
    overall_percent = int(round((((item_index - 1) + (file_percent / 100.0)) / item_total) * 100))

    speed_text = ""
    speed_match = YT_DLP_SPEED_RE.search(line)
    if speed_match:
        speed_text = f" - {speed_match.group(1)}"

    state["progress"] = overall_percent
    return {
        "progress": overall_percent,
        "speed_text": speed_text,
        "title": state.get("title", ""),
    }


def run_yt_dlp_download(url, target_dir, mode="video", progress_callback=None, title_callback=None, cancel_check=None):
    exe_path = find_yt_dlp_executable()
    if not exe_path:
        return False, "No se encontró yt-dlp.exe"

    ffmpeg_path = find_ffmpeg_executable()
    os.makedirs(target_dir, exist_ok=True)
    command = build_yt_dlp_command(exe_path, url, target_dir, mode, ffmpeg_path=ffmpeg_path)
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        universal_newlines=True,
        creationflags=creationflags,
    )

    state = {"item_index": 1, "item_total": 1, "title": "", "progress": 0}
    error_lines = []

    try:
        for raw_line in iter(process.stdout.readline, ""):
            if cancel_check and cancel_check():
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                return False, "cancelled"

            line = (raw_line or "").strip()
            if not line:
                continue

            error_match = YT_DLP_ERROR_RE.search(line)
            if error_match:
                error_lines.append(error_match.group(1).strip())
                continue

            parsed = _parse_progress_line(line, state)
            current_title = state.get("title", "")
            if current_title and title_callback:
                title_callback(current_title)
            if parsed and progress_callback:
                progress_callback(parsed["progress"], parsed["speed_text"], parsed.get("title", ""))
    finally:
        if process.stdout:
            process.stdout.close()

    return_code = process.wait()
    if return_code == 0:
        if progress_callback:
            progress_callback(100, "", state.get("title", ""))
        return True, ""

    error_text = " | ".join(error_lines[-3:]).strip() or "La descarga de YouTube falló."
    return False, error_text


class YtDlpSignals(QObject):
    progress = pyqtSignal(str, int, str)
    title = pyqtSignal(str, str)
    finished = pyqtSignal(str, bool, str)
    cancelled = pyqtSignal(str)


class YtDlpDownloadWorker(QRunnable):
    def __init__(self, entry_id, url, target_dir, mode="video"):
        super().__init__()
        self.entry_id = entry_id
        self.url = url
        self.target_dir = target_dir
        self.mode = mode or "video"
        self.signals = YtDlpSignals()
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        def on_progress(percent, speed_text, _title):
            self.signals.progress.emit(self.entry_id, percent, speed_text)

        def on_title(title):
            self.signals.title.emit(self.entry_id, title)

        ok, error_text = run_yt_dlp_download(
            self.url,
            self.target_dir,
            mode=self.mode,
            progress_callback=on_progress,
            title_callback=on_title,
            cancel_check=lambda: self._cancelled,
        )
        if self._cancelled or error_text == "cancelled":
            self.signals.cancelled.emit(self.entry_id)
            return
        self.signals.finished.emit(self.entry_id, ok, error_text)
