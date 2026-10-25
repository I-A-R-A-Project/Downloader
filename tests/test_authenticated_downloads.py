import importlib.util
import json
from pathlib import Path

from download_manager import browser as browser_module
from download_manager.browser import DirectFileResolveWorker, find_moodle_pluginfile_url
from download_manager import direct_file
from download_manager.tui import TuiDownloadManager
from download_manager.window import DownloadWindow
from download_manager import workers
from web_common.downloads.browser_downloads import request_cookie_jar


class FakeResponse:
    def __init__(self, url, headers=None):
        self.url = url
        self.headers = headers or {}
        self.closed = False

    def raise_for_status(self):
        return None

    def close(self):
        self.closed = True


def test_moodle_view_routes_never_count_as_direct_files():
    assert not direct_file.is_direct_file_url(
        "https://aulasvirtuales.bue.edu.ar/course/format/tiles/mod_view.php?cmid=1"
    )
    assert not direct_file.is_direct_file_url(
        "https://aulasvirtuales.bue.edu.ar/course/format/tiles/mod_view.php?cmid=1&forcedownload=1"
    )
    assert direct_file.is_direct_file_url(
        "https://aulasvirtuales.bue.edu.ar/pluginfile.php/1/mod_resource/content/1/file.pdf"
    )
    assert direct_file.is_direct_file_url(
        "https://downloads.example.org/download.php?id=42"
    )


def test_moodle_resource_page_is_not_direct_file():
    assert browser_module.UniversalDownloader.is_moodle_mod_view_url(
        "https://aulasvirtuales.bue.edu.ar/course/format/tiles/mod_view.php?cmid=943520&forcedownload=1"
    )


def test_moodle_page_extracts_actual_pluginfile_url():
    html = (
        '<a href="/pluginfile.php/3107167/mod_resource/content/2/'
        'Introduccion%20al%20proceso%20de%20control.pdf">'
        "Descargar archivo</a>"
    )
    assert find_moodle_pluginfile_url(
        html,
        "https://aulasvirtuales.bue.edu.ar/course/format/tiles/mod_view.php?cmid=943520",
    ) == (
        "https://aulasvirtuales.bue.edu.ar/pluginfile.php/3107167/mod_resource/"
        "content/2/Introduccion%20al%20proceso%20de%20control.pdf"
    )


def test_downloader_rejects_login_html_instead_of_saving_pdf(tmp_path, monkeypatch):
    body = b"<!doctype html><html><body>Login required</body></html>"

    class Response:
        status_code = 200
        headers = {"content-type": "application/pdf"}
        reason = "OK"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield body

    monkeypatch.setattr(workers.requests, "get", lambda *_args, **_kwargs: Response())
    signals = workers.DownloadSignals()
    results = []
    signals.finished.connect(lambda index, success: results.append((index, success)))
    filename = tmp_path / "Introduccion al proceso de control.pdf"
    worker = workers.FileDownloader(
        "https://aulasvirtuales.bue.edu.ar/pluginfile.php/file.pdf",
        str(filename),
        7,
        signals,
    )

    worker.run()

    assert results == [(7, False)]
    assert not filename.exists()
    assert "página HTML" in worker.error_text


def test_filename_resolution_sends_auth_cookies_to_requests(monkeypatch):
    jar = request_cookie_jar(
        [
            {
                "name": "MoodleSession",
                "value": "secret",
                "domain": "aulasvirtuales.bue.edu.ar",
                "path": "/",
                "secure": True,
            }
        ],
        "https://aulasvirtuales.bue.edu.ar/pluginfile.php/file.pdf",
    )
    response = FakeResponse(
        "https://aulasvirtuales.bue.edu.ar/pluginfile.php/file.pdf",
        {"Content-Disposition": 'attachment; filename="course.pdf"'},
    )
    calls = {}

    def fake_head(url, **kwargs):
        calls.update(kwargs)
        return response

    monkeypatch.setattr(direct_file.requests, "head", fake_head)

    assert direct_file.resolve_direct_filename(
        "https://aulasvirtuales.bue.edu.ar/pluginfile.php/file.pdf",
        cookies=jar,
    ) == "course.pdf"
    assert calls["cookies"] is jar


def test_browser_resolution_worker_forwards_auth_context(monkeypatch):
    cookies = {"MoodleSession": "secret"}
    headers = {"Referer": "https://aulasvirtuales.bue.edu.ar/course/view.php"}
    calls = {}

    def fake_resolve(url, **kwargs):
        calls.update(kwargs)
        return "course.pdf"

    monkeypatch.setattr(browser_module, "resolve_direct_filename", fake_resolve)
    worker = DirectFileResolveWorker(
        3,
        "https://aulasvirtuales.bue.edu.ar/pluginfile.php/file.pdf",
        "C:\\Downloads",
        headers=headers,
        cookies=cookies,
    )
    results = []
    worker.signals.finished.connect(lambda *args: results.append(args))
    worker.run()

    assert calls == {"headers": headers, "cookies": cookies}
    assert results == [
        (
            3,
            "https://aulasvirtuales.bue.edu.ar/pluginfile.php/file.pdf",
            "C:\\Downloads",
            "course.pdf",
        )
    ]


def test_download_session_serializer_never_persists_auth_cookies():
    entry = {
        "id": "entry",
        "title": "course.pdf",
        "path": "C:\\Downloads",
        "url_original": "https://example.edu/file.php",
        "password": "",
        "download_type": "regular",
        "youtube_mode": "video",
        "status": "waiting",
        "progress": 0,
        "direct_url": "",
        "direct_links": [],
        "torrent_gid": "",
        "torrent_hash": "",
        "speed_text": "",
        "error_text": "",
        "extract_status": "",
        "extract_error": "",
        "resolution_retry_count": 0,
        "archive_retry_count": 0,
        "auth_cookies": [{"name": "MoodleSession", "value": "must-not-persist"}],
        "auth_headers": {"Referer": "https://example.edu"},
    }

    serialized = DownloadWindow.serialize_entry(None, entry)

    assert "auth_cookies" not in serialized
    assert "must-not-persist" not in json.dumps(serialized)
    assert "auth_cookies" not in TuiDownloadManager.serialize_entry(None, entry)


def test_transient_handoff_json_is_deleted_after_parse(tmp_path):
    entrypoint = Path(__file__).parents[1] / "download_manager.py"
    spec = importlib.util.spec_from_file_location("downloader_entrypoint", entrypoint)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    handoff_file = tmp_path / "iara-download-handoff-test.json"
    handoff_file.write_text(
        json.dumps([{"url": "https://example.edu/file.pdf"}]),
        encoding="utf-8",
    )

    parsed = module.parse_input([str(handoff_file)])

    assert parsed[0]["url"] == "https://example.edu/file.pdf"
    assert not handoff_file.exists()
