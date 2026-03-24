import requests
from bs4 import BeautifulSoup
from urllib.parse import parse_qs, urlparse


TMDB_API_KEY = "TU_API_KEY_AQUI"
RAWG_API_KEY = "aa29f7a40ca3431ea2b3352ac0e223cc"


def normalize_trailer_url(url):
    if not url:
        return ""

    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path or ""

    if "youtu.be" in host:
        video_id = path.strip("/")
        return f"https://www.youtube.com/watch?v={video_id}" if video_id else url

    if "youtube.com" in host or "youtube-nocookie.com" in host:
        if path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
            return f"https://www.youtube.com/watch?v={video_id}" if video_id else url
        if path.startswith("/embed/"):
            video_id = path.split("/embed/", 1)[1].split("/", 1)[0]
            return f"https://www.youtube.com/watch?v={video_id}" if video_id else url

    return url


def search_tmdb(query):
    url = "https://api.themoviedb.org/3/search/multi"
    params = {
        "api_key": TMDB_API_KEY,
        "query": query,
        "language": "es-ES",
        "include_adult": False,
    }
    response = requests.get(url, params=params)
    items = response.json().get("results", [])
    return [{
        "source": "TMDb",
        "title": item.get("title") or item.get("name"),
        "year": (item.get("release_date") or item.get("first_air_date") or "")[:4],
        "type": item.get("media_type"),
        "description": item.get("overview", ""),
        "image": f"https://image.tmdb.org/t/p/w185{item['poster_path']}" if item.get("poster_path") else None,
    } for item in items]


def search_nyaa(query):
    results = []
    try:
        url = f"https://nyaa.si/?f=0&c=1_0&q={query.replace(' ', '+')}&s=seeders&o=desc"
        response = requests.get(url, timeout=10)
        soup = BeautifulSoup(response.text, "html.parser")
        rows = soup.select("tr.success") + soup.select("tr.default")
        for row in rows:
            title_tag = None
            for anchor in row.select("td:nth-child(2) a"):
                if anchor.has_attr("href") and "/view/" in anchor["href"] and "#comments" not in anchor["href"]:
                    if not anchor.find("i"):
                        title_tag = anchor
                        break
            magnet_tag = row.select_one("td.text-center a[href^='magnet:?']")

            if title_tag and magnet_tag:
                results.append({
                    "title": title_tag["title"],
                    "chapter": None,
                    "chapters": None,
                    "url_type": "torrent",
                    "url": magnet_tag["href"],
                    "resolucion": None,
                    "idioma": None,
                    "subtitulo": None,
                    "fansub": None,
                    "format": None,
                    "password": None,
                })
    except Exception as exc:
        print(f"[Nyaa] Error: {exc}")
    return results


def search_1337x(query):
    results = []
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        url = f"https://1337x.to/search/{query.replace(' ', '%20')}/1/"
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, "html.parser")
        entries = soup.select("td.coll-1.name")

        for entry in entries[:100]:
            link = entry.select_one("a:nth-of-type(2)")
            if not link:
                continue

            title = link.text.strip()
            detail_url = "https://1337x.to" + link["href"]
            try:
                detail_response = requests.get(detail_url, headers=headers, timeout=10)
                detail_soup = BeautifulSoup(detail_response.text, "html.parser")
                magnet_tag = detail_soup.select_one("a[href^='magnet:?']")
                if not magnet_tag:
                    continue

                results.append({
                    "title": title,
                    "chapter": None,
                    "chapters": None,
                    "url_type": "torrent",
                    "url": magnet_tag["href"],
                    "resolucion": None,
                    "idioma": None,
                    "subtitulo": None,
                    "fansub": None,
                    "format": None,
                    "password": None,
                })
            except Exception as exc:
                print(f"[1337x detail] Error: {exc}")
    except Exception as exc:
        print(f"[1337x] Error: {exc}")
    return results
