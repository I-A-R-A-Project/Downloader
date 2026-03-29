from media_search.anime_sources import search_1337x, search_nyaa


class FakeResponse:
    def __init__(self, text):
        self.text = text


def test_search_nyaa_extracts_torrent_rows(monkeypatch):
    html = """
    <table>
      <tr class="success">
        <td></td>
        <td>
          <a href="/view/1" title="Anime Pack">Anime Pack</a>
        </td>
        <td class="text-center">
          <a href="magnet:?xt=urn:btih:nyaa1">magnet</a>
        </td>
      </tr>
    </table>
    """

    def fake_get(url, timeout):
        assert "nyaa.si" in url
        return FakeResponse(html)

    monkeypatch.setattr("media_search.anime_sources.requests.get", fake_get)

    results = search_nyaa("anime pack")

    assert results == [
        {
            "title": "Anime Pack",
            "chapter": None,
            "chapters": None,
            "url_type": "torrent",
            "url": "magnet:?xt=urn:btih:nyaa1",
            "resolucion": None,
            "idioma": None,
            "subtitulo": None,
            "fansub": None,
            "format": None,
            "password": None,
        }
    ]


def test_search_1337x_extracts_magnets(monkeypatch):
    search_html = """
    <table>
      <tr>
        <td class="coll-1 name">
          <a href="/other">cat</a>
          <a href="/torrent/1/test-game">Test Game</a>
        </td>
      </tr>
    </table>
    """
    detail_html = '<a href="magnet:?xt=urn:btih:1337x1">magnet</a>'

    def fake_get(url, headers, timeout):
        if "search" in url:
            return FakeResponse(search_html)
        return FakeResponse(detail_html)

    monkeypatch.setattr("media_search.anime_sources.requests.get", fake_get)

    results = search_1337x("test game")

    assert results == [
        {
            "title": "Test Game",
            "chapter": None,
            "chapters": None,
            "url_type": "torrent",
            "url": "magnet:?xt=urn:btih:1337x1",
            "resolucion": None,
            "idioma": None,
            "subtitulo": None,
            "fansub": None,
            "format": None,
            "password": None,
        }
    ]
