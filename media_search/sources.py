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
FITGIRL_HOME_URL = "https://fitgirl-repacks.site/"
FITGIRL_SEARCH_URL = FITGIRL_HOME_URL
FITGIRL_USER_AGENT = "Mozilla/5.0"
STEAMRIP_HOME_URL = "https://steamrip.com/"
STEAMRIP_GAMES_LIST_URL = urljoin(STEAMRIP_HOME_URL, "games-list/")
STEAMRIP_USER_AGENT = "Mozilla/5.0"
STEAMRIP_GAMES_LIST_CACHE_PATH = os.path.join(ELAMIGOS_CACHE_DIR, "steamrip_games_list.html")


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


def _clean_steamrip_title(text):
    cleaned = " ".join((text or "").split())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _fetch_steamrip_games_list():
    response = requests.get(
        STEAMRIP_GAMES_LIST_URL,
        headers={"User-Agent": STEAMRIP_USER_AGENT},
        timeout=20,
    )
    response.raise_for_status()
    return response.text


def _load_steamrip_games_list(force_refresh=False):
    os.makedirs(ELAMIGOS_CACHE_DIR, exist_ok=True)

    if not force_refresh and os.path.exists(STEAMRIP_GAMES_LIST_CACHE_PATH):
        age = time.time() - os.path.getmtime(STEAMRIP_GAMES_LIST_CACHE_PATH)
        if age <= ELAMIGOS_CACHE_MAX_AGE_SECONDS:
            with open(STEAMRIP_GAMES_LIST_CACHE_PATH, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()

    try:
        html = _fetch_steamrip_games_list()
    except Exception:
        if os.path.exists(STEAMRIP_GAMES_LIST_CACHE_PATH):
            with open(STEAMRIP_GAMES_LIST_CACHE_PATH, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        raise

    with open(STEAMRIP_GAMES_LIST_CACHE_PATH, "w", encoding="utf-8", errors="ignore") as f:
        f.write(html)
    return html


def _extract_steamrip_index_entries(html):
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    entries = []
    seen = set()

    for anchor in soup.find_all("a", href=True):
        href = urljoin(STEAMRIP_HOME_URL, anchor.get("href", "").strip())
        parsed = urlparse(href)
        host = (parsed.netloc or "").lower().replace("www.", "")
        if host != "steamrip.com":
            continue
        if not parsed.path or "/games-list" in parsed.path:
            continue
        if "free-download" not in parsed.path:
            continue

        title = _clean_steamrip_title(anchor.get_text(" ", strip=True))
        if not title or title.lower() in {"steamrip", "download here"}:
            continue

        normalized_title = _normalize_search_text(title)
        if not normalized_title or href in seen:
            continue

        seen.add(href)
        entries.append({
            "title": title,
            "normalized_title": normalized_title,
            "detail_url": href,
        })

    if entries:
        return entries

    bullet_blocks = []
    current_block = ""
    for raw_line in html.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped.startswith("*"):
            if current_block:
                bullet_blocks.append(current_block)
            current_block = stripped
        elif current_block:
            current_block += " " + stripped
    if current_block:
        bullet_blocks.append(current_block)

    pattern = r"^\*\s+([^<>]+?)\s*<https://steamrip\.com/([^>]*free-download[^>]*)>"
    for block in bullet_blocks:
        match = re.search(pattern, block, flags=re.IGNORECASE)
        if not match:
            continue
        title = _clean_steamrip_title(match.group(1)).strip("* ")
        href = "https://steamrip.com/" + re.sub(r"\s+", "", match.group(2).lstrip("/"))
        if not title or title.lower() in {"steamrip", "download here"}:
            continue
        normalized_title = _normalize_search_text(title)
        if not normalized_title or href in seen:
            continue
        seen.add(href)
        entries.append({
            "title": title,
            "normalized_title": normalized_title,
            "detail_url": href,
        })

    return entries


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


def _is_steamrip_external_download_href(href):
    if not href.startswith(("http://", "https://")):
        return False

    parsed = urlparse(href)
    host = (parsed.netloc or "").lower().replace("www.", "")
    if not host or host == "steamrip.com":
        return False
    if host in {
        "facebook.com",
        "x.com",
        "twitter.com",
        "pinterest.com",
        "reddit.com",
        "api.whatsapp.com",
        "telegram.me",
        "discord.gg",
    }:
        return False
    if href.startswith("mailto:"):
        return False
    if any(token in href.lower() for token in (".png", ".jpg", ".jpeg", ".gif", ".webp")):
        return False
    return True


def _steamrip_label_from_anchor(anchor, href):
    prev_text = ""
    for previous in anchor.previous_siblings:
        text = previous.get_text(" ", strip=True) if hasattr(previous, "get_text") else str(previous).strip()
        text = _clean_steamrip_title(text)
        if text:
            prev_text = re.sub(r"download here", "", text, flags=re.IGNORECASE).strip(" :-|*")
            break
    if prev_text:
        return prev_text

    for previous in anchor.parents:
        sibling = previous.previous_sibling
        while sibling is not None:
            text = sibling.get_text(" ", strip=True) if hasattr(sibling, "get_text") else str(sibling).strip()
            text = _clean_steamrip_title(text)
            text = re.sub(r"download here", "", text, flags=re.IGNORECASE).strip(" :-|*")
            if text and 1 <= len(text.split()) <= 4:
                return text
            sibling = getattr(sibling, "previous_sibling", None)

    return urlparse(href).netloc.lower().replace("www.", "") or "Direct"


def _extract_steamrip_detail_links(detail_url, game_title):
    response = requests.get(
        detail_url,
        headers={"User-Agent": STEAMRIP_USER_AGENT},
        timeout=20,
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    article = soup.select_one("article") or soup
    results = []
    seen = set()

    for anchor in article.find_all("a", href=True):
        href = anchor.get("href", "").strip()
        href = _extract_external_url_from_internal_link(href) or urljoin(detail_url, href)
        if not _is_steamrip_external_download_href(href):
            continue

        label = _steamrip_label_from_anchor(anchor, href)
        dedupe_key = (label.lower(), href)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        results.append({
            "title": game_title,
            "chapter": None,
            "chapters": None,
            "url_type": label,
            "url": href,
            "mirror_host": urlparse(href).netloc.lower().replace("www.", ""),
            "resolucion": None,
            "idioma": None,
            "subtitulo": None,
            "fansub": None,
            "format": None,
            "password": None,
        })

    if results:
        return results

    lines = [line.strip() for line in response.text.splitlines()]
    current_label = None
    for raw_line in lines:
        line = _clean_steamrip_title(raw_line).strip("* ")
        if not line:
            continue
        url_match = re.search(r"<(https?://[^>]+)>", line)
        upper_line = line.upper()

        if not url_match and 1 <= len(line.split()) <= 4 and any(ch.isalpha() for ch in line):
            if line.lower() not in {"download here", "related games", "popular games"}:
                current_label = line
            continue

        if not url_match:
            continue

        href = url_match.group(1).strip()
        if not _is_steamrip_external_download_href(href):
            continue

        label = current_label or _steamrip_label_from_anchor(anchor=soup.new_tag("a", href=href), href=href)
        if "DOWNLOAD HERE" in upper_line and current_label:
            label = current_label
        dedupe_key = (label.lower(), href)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        results.append({
            "title": game_title,
            "chapter": None,
            "chapters": None,
            "url_type": label,
            "url": href,
            "mirror_host": urlparse(href).netloc.lower().replace("www.", ""),
            "resolucion": None,
            "idioma": None,
            "subtitulo": None,
            "fansub": None,
            "format": None,
            "password": None,
        })

    return results


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


def warm_elamigos_cache(force_refresh=False):
    try:
        _load_elamigos_raw_index(force_refresh=force_refresh)
        return True
    except Exception:
        try:
            _load_elamigos_index_html(force_refresh=force_refresh)
            return True
        except Exception as exc:
            print(f"[ElAmigos preload] Error: {exc}")
            return False


def search_steamrip(query, force_refresh=False, max_candidates=6):
    try:
        html = _load_steamrip_games_list(force_refresh=force_refresh)
        entries = _extract_steamrip_index_entries(html)
    except Exception as exc:
        print(f"[SteamRIP] Error cargando índice: {exc}")
        return []

    if not entries:
        print("[SteamRIP] Índice remoto/caché vacío.")
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

    candidates = strong_candidates[:max_candidates] if strong_candidates else [entry for _, entry in scored[:max_candidates]]

    results = []
    for entry in candidates:
        try:
            results.extend(_extract_steamrip_detail_links(entry["detail_url"], entry["title"]))
        except Exception as exc:
            print(f"[SteamRIP detail] Error con {entry['detail_url']}: {exc}")

    unique = []
    seen_urls = set()
    for item in results:
        if item["url"] in seen_urls:
            continue
        seen_urls.add(item["url"])
        unique.append(item)
    return unique


def warm_steamrip_cache(force_refresh=False):
    try:
        _load_steamrip_games_list(force_refresh=force_refresh)
        return True
    except Exception as exc:
        print(f"[SteamRIP preload] Error: {exc}")
        return False


def _fetch_fitgirl_search_page(query):
    response = requests.get(
        FITGIRL_SEARCH_URL,
        params={"s": query},
        headers={"User-Agent": FITGIRL_USER_AGENT},
        timeout=20,
    )
    response.raise_for_status()
    return response.text


def _extract_fitgirl_search_entries(html):
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    entries = []
    seen = set()

    for article in soup.select("article.category-lossless-repack"):
        classes = set(article.get("class") or [])
        if "post" not in classes:
            continue

        title_link = article.select_one("h1.entry-title a[href], h2.entry-title a[href]")
        if not title_link:
            continue

        href = urljoin(FITGIRL_HOME_URL, title_link.get("href", "").strip())
        title = _clean_fitgirl_title(title_link.get_text(" ", strip=True))
        normalized_title = _normalize_search_text(title)
        if not href or not title or not normalized_title or href in seen:
            continue

        seen.add(href)
        entries.append({
            "title": title,
            "normalized_title": normalized_title,
            "detail_url": href,
        })

    return entries


def _clean_fitgirl_title(text):
    cleaned = " ".join((text or "").split())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _is_fitgirl_direct_download_href(href):
    if not href.startswith(("http://", "https://")):
        return False

    parsed = urlparse(href)
    host = (parsed.netloc or "").lower().replace("www.", "")
    if not host:
        return False
    if host.endswith("fitgirl-repacks.site") or host == "paste.fitgirl-repacks.site":
        return False
    if host in {"internetdownloadmanager.com", "jdownloader.org"}:
        return False
    return True


def _build_fitgirl_direct_result(game_title, href, link_text):
    link_text = re.sub(r"^Filehoster:\s*", "", link_text or "", flags=re.IGNORECASE).strip()
    if not link_text:
        link_text = urlparse(href).netloc.lower().replace("www.", "") or "Direct"

    return {
        "title": game_title,
        "chapter": None,
        "chapters": None,
        "url_type": link_text,
        "url": href,
        "mirror_host": urlparse(href).netloc.lower().replace("www.", ""),
        "resolucion": None,
        "idioma": None,
        "subtitulo": None,
        "fansub": None,
        "format": None,
        "password": None,
    }


def _fitgirl_file_label(anchor, href):
    anchor_text = _clean_fitgirl_title(anchor.get_text(" ", strip=True))
    if anchor_text:
        return anchor_text

    filename = os.path.basename(urlparse(href).path.rstrip("/"))
    return filename or href


def _iter_fitgirl_section_nodes(heading):
    node = heading.find_next_sibling()
    while node is not None:
        if getattr(node, "name", None) in {"h1", "h2", "h3"}:
            break
        yield node
        node = node.find_next_sibling()


def _extract_fitgirl_direct_links(soup, game_title):
    results = []
    seen = set()

    heading = next(
        (
            tag for tag in soup.find_all(["h2", "h3"])
            if "download mirrors (direct links)" in tag.get_text(" ", strip=True).lower()
        ),
        None,
    )
    if not heading:
        return results

    spoiler_results = []
    for node in _iter_fitgirl_section_nodes(heading):
        spoiler_blocks = node.select(".su-spoiler, .sp-wrap")
        for spoiler in spoiler_blocks:
            spoiler_title = spoiler.select_one(".su-spoiler-title, .sp-head")
            spoiler_label = _clean_fitgirl_title(spoiler_title.get_text(" ", strip=True)) if spoiler_title else ""
            spoiler_content = spoiler.select_one(".su-spoiler-content, .sp-body, .sp-content") or spoiler
            spoiler_group = None
            list_item = spoiler.find_parent("li")
            if list_item:
                paste_anchor = next(
                    (
                        anchor for anchor in list_item.find_all("a", href=True, recursive=False)
                        if "paste.fitgirl-repacks.site" in (anchor.get("href", "") or "")
                    ),
                    None,
                )
                if paste_anchor:
                    spoiler_group = _clean_fitgirl_title(paste_anchor.get_text(" ", strip=True))
                    spoiler_group = re.sub(r"^Filehoster:\s*", "", spoiler_group, flags=re.IGNORECASE).strip()

            for anchor in spoiler_content.find_all("a", href=True):
                href = anchor.get("href", "").strip()
                if not _is_fitgirl_direct_download_href(href):
                    continue

                anchor_text = _fitgirl_file_label(anchor, href)
                result = _build_fitgirl_direct_result(game_title, href, spoiler_group or spoiler_label or anchor_text)
                result["title"] = anchor_text
                result["group"] = f"{game_title} [{result['url_type']}]"
                dedupe_key = (result["url_type"].lower(), href)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                spoiler_results.append(result)

        if spoiler_results:
            continue

        for item in node.find_all("li"):
            anchors = item.find_all("a", href=True)
            if not anchors:
                continue

            primary_link = anchors[0]
            href = primary_link.get("href", "").strip()
            if not _is_fitgirl_direct_download_href(href):
                continue

            link_text = _clean_fitgirl_title(primary_link.get_text(" ", strip=True))
            result = _build_fitgirl_direct_result(game_title, href, link_text)
            dedupe_key = (result["url_type"].lower(), href)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            results.append(result)

    return spoiler_results or results


def _extract_fitgirl_torrent_links(soup, game_title):
    results = []
    seen = set()

    heading = next(
        (
            tag for tag in soup.find_all(["h2", "h3"])
            if "download mirrors (torrent)" in tag.get_text(" ", strip=True).lower()
        ),
        None,
    )
    if not heading:
        return results

    for node in _iter_fitgirl_section_nodes(heading):
        for item in node.find_all("li"):
            anchors = item.find_all("a", href=True)
            if not anchors:
                continue

            source_label = None
            for anchor in anchors:
                href = anchor.get("href", "").strip()
                text = _clean_fitgirl_title(anchor.get_text(" ", strip=True))
                if href.startswith(("http://", "https://")) and "torrent" not in text.lower():
                    source_label = text or urlparse(href).netloc.lower().replace("www.", "")
                    break

            for anchor in anchors:
                href = anchor.get("href", "").strip()
                if not href.startswith("magnet:?"):
                    continue

                label = source_label or "magnet"
                result_title = f"{game_title} [{label}]"
                dedupe_key = (result_title, href)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)

                results.append({
                    "title": result_title,
                    "chapter": None,
                    "chapters": None,
                    "url_type": "torrent",
                    "url": href,
                    "mirror_host": label,
                    "resolucion": None,
                    "idioma": None,
                    "subtitulo": None,
                    "fansub": None,
                    "format": None,
                    "password": None,
                })

    return results


def _extract_fitgirl_detail_links(detail_url, game_title):
    response = requests.get(
        detail_url,
        headers={"User-Agent": FITGIRL_USER_AGENT},
        timeout=20,
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    results = []
    results.extend(_extract_fitgirl_direct_links(soup, game_title))
    results.extend(_extract_fitgirl_torrent_links(soup, game_title))
    return results


def search_fitgirl(query, force_refresh=False, max_candidates=6):
    try:
        html = _fetch_fitgirl_search_page(query)
        entries = _extract_fitgirl_search_entries(html)
    except Exception as exc:
        print(f"[FitGirl] Error buscando '{query}': {exc}")
        return []

    if not entries:
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

    candidates = strong_candidates[:max_candidates] if strong_candidates else [entry for _, entry in scored[:max_candidates]]

    results = []
    for entry in candidates:
        try:
            results.extend(_extract_fitgirl_detail_links(entry["detail_url"], entry["title"]))
        except Exception as exc:
            print(f"[FitGirl detail] Error con {entry['detail_url']}: {exc}")

    unique = []
    seen_urls = set()
    for item in results:
        if item["url"] in seen_urls:
            continue
        seen_urls.add(item["url"])
        unique.append(item)
    return unique
