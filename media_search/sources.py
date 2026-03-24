import difflib
import os
import re
import time
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from config import CONFIG_PATH


TMDB_API_KEY = "TU_API_KEY_AQUI"
RAWG_API_KEY = "aa29f7a40ca3431ea2b3352ac0e223cc"
ELAMIGOS_HOST = "elamigos.site"
ELAMIGOS_HOME_URL = "https://elamigos.site/"
ELAMIGOS_RAW_INDEX_URL = urljoin(ELAMIGOS_HOME_URL, "raw/ElAmigosReleases-RAW.txt")
ELAMIGOS_USER_AGENT = "Mozilla/5.0"
ELAMIGOS_CACHE_DIR = os.path.join(os.path.dirname(CONFIG_PATH), "cache")
ELAMIGOS_INDEX_CACHE_PATH = os.path.join(ELAMIGOS_CACHE_DIR, "elamigos_home.html")
ELAMIGOS_RAW_INDEX_PATH = os.path.join(ELAMIGOS_CACHE_DIR, "ElAmigosReleases-RAW.txt")
ELAMIGOS_CACHE_MAX_AGE_SECONDS = 6 * 60 * 60
ELAMIGOS_HOST_LABELS = {
    "DDOWNLOAD",
    "RAPIDGATOR",
    "FILECRYPT",
    "KEEPLINKS",
}


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


def _normalize_search_text(text):
    text = (text or "").lower()
    text = re.sub(r"\+\[.*?\]", " ", text)
    text = re.sub(r"\[.*?\]", " ", text)
    text = text.replace("elamigos", " ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    tokens = [token for token in text.split() if token]
    return " ".join(tokens)


def _fetch_elamigos_homepage():
    response = requests.get(
        ELAMIGOS_HOME_URL,
        headers={"User-Agent": ELAMIGOS_USER_AGENT},
        timeout=20,
    )
    response.raise_for_status()
    return response.text


def _fetch_elamigos_raw_index():
    response = requests.get(
        ELAMIGOS_RAW_INDEX_URL,
        headers={"User-Agent": ELAMIGOS_USER_AGENT},
        timeout=20,
    )
    response.raise_for_status()
    return response.text


def _load_elamigos_raw_index(force_refresh=False):
    os.makedirs(ELAMIGOS_CACHE_DIR, exist_ok=True)

    if not force_refresh and os.path.exists(ELAMIGOS_RAW_INDEX_PATH):
        age = time.time() - os.path.getmtime(ELAMIGOS_RAW_INDEX_PATH)
        if age <= ELAMIGOS_CACHE_MAX_AGE_SECONDS:
            with open(ELAMIGOS_RAW_INDEX_PATH, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()

    try:
        raw_text = _fetch_elamigos_raw_index()
    except Exception:
        if os.path.exists(ELAMIGOS_RAW_INDEX_PATH):
            with open(ELAMIGOS_RAW_INDEX_PATH, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        raise

    with open(ELAMIGOS_RAW_INDEX_PATH, "w", encoding="utf-8", errors="ignore") as f:
        f.write(raw_text)
    return raw_text


def _load_elamigos_index_html(force_refresh=False):
    os.makedirs(ELAMIGOS_CACHE_DIR, exist_ok=True)

    if not force_refresh and os.path.exists(ELAMIGOS_INDEX_CACHE_PATH):
        age = time.time() - os.path.getmtime(ELAMIGOS_INDEX_CACHE_PATH)
        if age <= ELAMIGOS_CACHE_MAX_AGE_SECONDS:
            with open(ELAMIGOS_INDEX_CACHE_PATH, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()

    try:
        html = _fetch_elamigos_homepage()
    except Exception:
        if os.path.exists(ELAMIGOS_INDEX_CACHE_PATH):
            with open(ELAMIGOS_INDEX_CACHE_PATH, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        raise

    with open(ELAMIGOS_INDEX_CACHE_PATH, "w", encoding="utf-8", errors="ignore") as f:
        f.write(html)
    return html


def _extract_elamigos_raw_index_entries(raw_text):
    if not raw_text:
        return []

    entries = []
    seen = set()

    for raw_line in raw_text.splitlines():
        title = raw_line.strip()
        if not title:
            continue
        if title.startswith(("Use CTRL+F", "Download links here:", "- installer CRC")):
            continue
        if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", title):
            continue
        if "elamigos" not in title.lower():
            continue

        title = re.sub(r"\bDOWNLOAD\b", "", title, flags=re.IGNORECASE).strip()
        normalized_title = _normalize_search_text(title)
        if not title or not normalized_title or normalized_title in seen:
            continue

        seen.add(normalized_title)
        entries.append({
            "title": title,
            "normalized_title": normalized_title,
            "detail_url": None,
        })

    return entries


def _extract_elamigos_index_entries(html):
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    entries = []
    seen = set()

    for header in soup.find_all(["h3", "h5"]):
        link = header.find("a", href=True)
        if not link:
            continue
        href = urljoin(ELAMIGOS_HOME_URL, link["href"].strip())
        if "/data/" not in href:
            continue

        title = header.get_text(" ", strip=True)
        title = re.sub(r"\bDOWNLOAD\b", "", title, flags=re.IGNORECASE).strip()
        if not title or href in seen:
            continue
        seen.add(href)
        entries.append({
            "title": title,
            "normalized_title": _normalize_search_text(title),
            "detail_url": href,
        })

    return entries


def _score_elamigos_match(query, candidate_title, normalized_candidate=None):
    normalized_query = _normalize_search_text(query)
    normalized_title = normalized_candidate or _normalize_search_text(candidate_title)
    if not normalized_query or not normalized_title:
        return 0.0

    if normalized_query == normalized_title:
        return 1.0
    if normalized_query in normalized_title:
        return 0.95

    query_tokens = set(normalized_query.split())
    title_tokens = set(normalized_title.split())
    overlap = len(query_tokens & title_tokens) / max(len(query_tokens), 1)
    similarity = difflib.SequenceMatcher(None, normalized_query, normalized_title).ratio()
    return max(overlap * 0.9, similarity)


def _extract_external_url_from_internal_link(href):
    parsed = urlparse(href)
    if ELAMIGOS_HOST not in (parsed.netloc or "").lower():
        return None

    params = parse_qs(parsed.query)
    for key in ("url", "u", "link", "target", "go"):
        value = params.get(key, [None])[0]
        if value and value.startswith(("http://", "https://")):
            return value
    return None


def _is_elamigos_host_heading(text):
    cleaned = " ".join((text or "").split()).upper()
    return cleaned in ELAMIGOS_HOST_LABELS


def _clean_elamigos_release_title(text, fallback_title):
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return fallback_title
    return cleaned


def _extract_elamigos_detail_links(detail_url, game_title):
    headers = {"User-Agent": ELAMIGOS_USER_AGENT}
    response = requests.get(detail_url, headers=headers, timeout=20)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    results = []
    seen = set()
    current_release = game_title
    current_host = None

    for tag in soup.find_all(["h2", "h3", "a"]):
        text = " ".join(tag.get_text(" ", strip=True).split())

        if tag.name == "h2":
            if _is_elamigos_host_heading(text):
                current_host = text.upper()
            else:
                current_release = _clean_elamigos_release_title(text, game_title)
                current_host = None
            continue

        if tag.name != "a":
            continue

        href = tag.get("href", "").strip()
        if not href:
            continue
        href = urljoin(detail_url, href)
        href = _extract_external_url_from_internal_link(href) or href
        if not href.startswith(("http://", "https://")):
            continue

        parsed = urlparse(href)
        host = (parsed.netloc or "").lower()
        if not host or ELAMIGOS_HOST in host:
            continue
        if any(token in href.lower() for token in (".png", ".jpg", ".jpeg", ".gif", "twitter.com", "facebook.com")):
            continue

        external_host = host.replace("www.", "")
        type_label = current_host or external_host

        dedupe_key = (current_release, type_label, href)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        results.append({
            "title": current_release,
            "chapter": None,
            "chapters": None,
            "url_type": type_label,
            "url": href,
            "mirror_host": external_host,
            "resolucion": None,
            "idioma": None,
            "subtitulo": None,
            "fansub": None,
            "format": None,
            "password": None,
        })

    return results


def search_elamigos(query, force_refresh=False, max_candidates=6):
    try:
        raw_text = _load_elamigos_raw_index(force_refresh=force_refresh)
        entries = _extract_elamigos_raw_index_entries(raw_text)
    except Exception:
        html = _load_elamigos_index_html(force_refresh=force_refresh)
        entries = _extract_elamigos_index_entries(html)

    if not entries:
        print("[ElAmigos] Índice remoto/caché vacío.")
        return []

    normalized_query = _normalize_search_text(query)
    query_tokens = set(normalized_query.split())
    scored = []
    for entry in entries:
        score = _score_elamigos_match(query, entry["title"], entry["normalized_title"])
        if score >= 0.45:
            scored.append((score, entry))

    scored.sort(key=lambda item: item[0], reverse=True)

    strong_candidates = []
    for _, entry in scored:
        normalized_title = entry["normalized_title"]
        title_tokens = set(normalized_title.split())
        if normalized_query and normalized_query in normalized_title:
            strong_candidates.append(entry)
            continue
        if query_tokens and query_tokens.issubset(title_tokens):
            strong_candidates.append(entry)

    if strong_candidates:
        candidates = strong_candidates[:max_candidates]
    else:
        candidates = [entry for _, entry in scored[:max_candidates]]

    results = []
    for entry in candidates:
        try:
            detail_url = entry.get("detail_url")
            if not detail_url:
                html = _load_elamigos_index_html(force_refresh=force_refresh)
                homepage_entries = _extract_elamigos_index_entries(html)
                detail_candidates = []
                for homepage_entry in homepage_entries:
                    score = _score_elamigos_match(entry["title"], homepage_entry["title"], homepage_entry["normalized_title"])
                    if score >= 0.45:
                        detail_candidates.append((score, homepage_entry))
                detail_candidates.sort(key=lambda item: item[0], reverse=True)
                detail_url = detail_candidates[0][1]["detail_url"] if detail_candidates else None

            if not detail_url:
                continue

            results.extend(_extract_elamigos_detail_links(detail_url, entry["title"]))
        except Exception as exc:
            print(f"[ElAmigos detail] Error con {entry.get('detail_url') or entry['title']}: {exc}")

    unique = []
    seen_urls = set()
    for item in results:
        if item["url"] in seen_urls:
            continue
        seen_urls.add(item["url"])
        unique.append(item)
    return unique
