from media_search import anime_sources


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, json, headers, timeout):
        self.calls.append({
            "url": url,
            "json": json,
            "headers": headers,
            "timeout": timeout,
        })
        return FakeResponse(self.responses.pop(0))


def test_search_aniteca_api_maps_results():
    session = FakeSession([{
        "data": [
            {"anime_id": 42, "nombre": "Test Anime", "numepisodios": "12"},
        ]
    }])

    results = anime_sources.search_aniteca_api("test anime", session=session)

    assert results == [{
        "id": "42",
        "nombre": "Test Anime",
        "numepisodios": 12,
    }]
    assert session.calls[0]["url"].endswith("/search")


def test_get_chapter_links_maps_nested_metadata():
    session = FakeSession([{
        "data": [{
            "numcap": 1,
            "servername": "mediafire",
            "online_id": "abc",
            "password": "pw",
            "format": "mp4",
            "resol": 720,
            "idiomas": [{"idioma": {"idioma": "Japones"}}],
            "subtitulos": [{"subs": {"subtitulo": "Español"}}],
            "fansubs": [{"fansub": {"nombrefansub": "Fansub Test"}}],
        }]
    }])

    results = anime_sources.get_chapter_links("42", 12, session=session)

    assert results == [{
        "capitulo": 1,
        "servername": "mediafire",
        "online_id": "abc",
        "password": "pw",
        "format": "mp4",
        "resolucion": 720,
        "idioma": "Japones",
        "subtitulo": "Español",
        "fansub": "Fansub Test",
    }]
    assert session.calls[0]["url"].endswith("/getchapters")


def test_extract_direct_link_uses_expected_payload():
    session = FakeSession([{"data2": "https://mediafire.test/file.zip"}])

    link = anime_sources.extract_direct_link("mediafire", "abc", session=session)

    assert link == "https://mediafire.test/file.zip"
    assert session.calls[0]["url"].endswith("/extractkey")
    assert session.calls[0]["json"] == {
        "server": "mediafire",
        "id": "abc",
        "noevent": True,
    }


def test_search_aniteca_builds_deferred_links():
    session = FakeSession([
        {"data": [{"anime_id": 42, "nombre": "Test Anime", "numepisodios": "1"}]},
        {"data": [{
            "numcap": 1,
            "servername": "1fichier",
            "online_id": "online-1",
            "password": "",
            "format": "mkv",
            "resol": 1080,
            "idiomas": [],
            "subtitulos": [],
            "fansubs": [],
        }]},
        {"data": "https://1fichier.test/file.mkv"},
    ])

    results = anime_sources.search_aniteca("test anime", session=session)

    assert len(results) == 1
    item = results[0]
    assert item["title"] == "Test Anime"
    assert item["chapter"] == 1
    assert item["chapters"] == 1
    assert item["url_type"] == "1fichier"
    assert callable(item["url"])
    assert item["url"]() == "https://1fichier.test/file.mkv"
