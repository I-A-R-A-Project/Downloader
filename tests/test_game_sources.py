from media_search import game_sources


def test_search_elamigos_uses_best_match(monkeypatch):
    monkeypatch.setattr(game_sources, "_load_elamigos_raw_index", lambda force_refresh=False: "Test Game ElAmigos\nOther Game ElAmigos")
    monkeypatch.setattr(
        game_sources,
        "_load_elamigos_index_html",
        lambda force_refresh=False: """
        <h3><a href="/data/test-game">Test Game Download</a></h3>
        <h3><a href="/data/other-game">Other Game Download</a></h3>
        """,
    )
    monkeypatch.setattr(
        game_sources,
        "_extract_elamigos_detail_links",
        lambda detail_url, game_title: [game_sources._build_download_result(game_title, "DDOWNLOAD", f"{detail_url}/mirror", "ddownload.com")],
    )

    results = game_sources.search_elamigos("test game")

    assert len(results) == 1
    assert results[0]["title"] == "Test Game ElAmigos"
    assert results[0]["url"] == "https://elamigos.site/data/test-game/mirror"


def test_search_steamrip_uses_index_and_detail(monkeypatch):
    monkeypatch.setattr(
        game_sources,
        "_load_steamrip_games_list",
        lambda force_refresh=False: """
        <a href="https://steamrip.com/test-game-free-download/">Test Game</a>
        <a href="https://steamrip.com/other-game-free-download/">Other Game</a>
        """,
    )
    monkeypatch.setattr(
        game_sources,
        "_extract_steamrip_detail_links",
        lambda detail_url, game_title: [game_sources._build_download_result(game_title, "GOFILE", f"{detail_url}download", "gofile.io")],
    )

    results = game_sources.search_steamrip("test game")

    assert len(results) == 1
    assert results[0]["title"] == "Test Game"
    assert results[0]["url_type"] == "GOFILE"
    assert results[0]["url"] == "https://steamrip.com/test-game-free-download/download"


def test_search_fitgirl_uses_search_page_and_detail(monkeypatch):
    monkeypatch.setattr(
        game_sources,
        "_fetch_fitgirl_search_page",
        lambda query: """
        <article class="post category-lossless-repack">
          <h1 class="entry-title"><a href="https://fitgirl-repacks.site/test-game/">Test Game</a></h1>
        </article>
        """,
    )
    monkeypatch.setattr(
        game_sources,
        "_extract_fitgirl_detail_links",
        lambda detail_url, game_title: [game_sources._build_download_result(game_title, "FuckingFast", f"{detail_url}download", "fuckingfast.co")],
    )

    results = game_sources.search_fitgirl("test game")

    assert len(results) == 1
    assert results[0]["title"] == "Test Game"
    assert results[0]["url_type"] == "FuckingFast"
    assert results[0]["url"] == "https://fitgirl-repacks.site/test-game/download"
