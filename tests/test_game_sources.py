from media_search import game_sources
import json


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
    monkeypatch.setattr(game_sources, "_load_fitgirl_index", lambda force_refresh=False: (_ for _ in ()).throw(RuntimeError("cache unavailable")))
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


def test_extract_fitgirl_index_entries_reads_az_list():
    html = """
    <h1>All My Repacks, A-Z</h1>
    <ul>
      <li><a href="https://fitgirl-repacks.site/test-game/">Test Game</a></li>
      <li><a href="https://fitgirl-repacks.site/other-game/">Other Game</a></li>
      <li><a href="https://fitgirl-repacks.site/all-my-repacks-a-z/?lcp_page0=2">Next Page</a></li>
    </ul>
    """

    entries = game_sources._extract_fitgirl_index_entries(html)

    assert entries == [
        {
            "title": "Test Game",
            "normalized_title": "test game",
            "detail_url": "https://fitgirl-repacks.site/test-game/",
        },
        {
            "title": "Other Game",
            "normalized_title": "other game",
            "detail_url": "https://fitgirl-repacks.site/other-game/",
        },
    ]


def test_search_fitgirl_uses_index_cache_and_detail(monkeypatch):
    monkeypatch.setattr(
        game_sources,
        "_load_fitgirl_index",
        lambda force_refresh=False: [
            {
                "title": "Test Game",
                "normalized_title": "test game",
                "detail_url": "https://fitgirl-repacks.site/test-game/",
            },
            {
                "title": "Other Game",
                "normalized_title": "other game",
                "detail_url": "https://fitgirl-repacks.site/other-game/",
            },
        ],
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


def test_load_fitgirl_index_skips_empty_intermediate_pages(monkeypatch, tmp_path):
    cache_path = tmp_path / "fitgirl_index.json"
    page_html = {1: "page-1", 2: "page-2", 3: "page-3", 4: "page-4"}
    extracted = {
        "page-1": [
            {
                "title": "Test Game 1",
                "normalized_title": "test game 1",
                "detail_url": "https://fitgirl-repacks.site/test-game-1/",
            }
        ],
        "page-2": [],
        "page-3": [
            {
                "title": "Test Game 3",
                "normalized_title": "test game 3",
                "detail_url": "https://fitgirl-repacks.site/test-game-3/",
            }
        ],
        "page-4": [],
    }

    monkeypatch.setattr(game_sources, "FITGIRL_INDEX_CACHE_PATH", str(cache_path))
    monkeypatch.setattr(game_sources, "FITGIRL_INDEX_MAX_PAGES", 4)
    monkeypatch.setattr(game_sources, "FITGIRL_INDEX_BATCH_SIZE", 2)
    monkeypatch.setattr(game_sources, "_fetch_fitgirl_index_page", lambda page: page_html[page])
    monkeypatch.setattr(game_sources, "_extract_fitgirl_index_entries", lambda html: extracted[html])

    entries = game_sources._load_fitgirl_index(force_refresh=True)

    assert [entry["title"] for entry in entries] == ["Test Game 1", "Test Game 3"]
    assert json.loads(cache_path.read_text(encoding="utf-8")) == [
        ["Test Game 1", "https://fitgirl-repacks.site/test-game-1/"],
        ["Test Game 3", "https://fitgirl-repacks.site/test-game-3/"],
    ]
