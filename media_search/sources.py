from urllib.parse import parse_qs, urlparse

import requests


TMDB_API_KEY = "TU_API_KEY_AQUI"


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


__all__ = [
    "TMDB_API_KEY",
    "normalize_trailer_url",
    "search_tmdb",
]
