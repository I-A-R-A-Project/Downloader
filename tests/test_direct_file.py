from download_manager import direct_file


class FakeResponse:
    def __init__(self, url, headers=None, status_code=200):
        self.url = url
        self.headers = headers or {}
        self.status_code = status_code
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise direct_file.requests.HTTPError(response=self)

    def close(self):
        self.closed = True


def test_resolve_direct_filename_uses_source_name_when_redirect_is_generic(monkeypatch):
    response = FakeResponse("https://aulasvirtuales.bue.edu.ar/index.php")
    monkeypatch.setattr(direct_file.requests, "head", lambda *args, **kwargs: response)

    filename = direct_file.resolve_direct_filename(
        "https://aulasvirtuales.bue.edu.ar/pluginfile.php/3107183/mod_resource/content/1/Valvulas%20industriales.pdf"
    )

    assert filename == "Valvulas industriales.pdf"
    assert response.closed


def test_resolve_direct_filename_prefers_content_disposition(monkeypatch):
    response = FakeResponse(
        "https://example.com/index.php",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''Manual%20final.pdf"},
    )
    monkeypatch.setattr(direct_file.requests, "head", lambda *args, **kwargs: response)

    filename = direct_file.resolve_direct_filename("https://example.com/download?id=42")

    assert filename == "Manual final.pdf"
