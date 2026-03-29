from functools import partial
import requests
from bs4 import BeautifulSoup


ANITECA_BASE_URL = "https://aniteca.net/aniapi/api"
ANITECA_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Origin": "https://aniteca.net",
    "Referer": "https://aniteca.net/",
}


def _post_aniteca_json(endpoint, payload, session=None, timeout=10):
    client = session or requests
    response = client.post(f"{ANITECA_BASE_URL}/{endpoint}", json=payload, headers=ANITECA_HEADERS, timeout=timeout)
    response.raise_for_status()
    return response.json()


def search_aniteca(query, session=None):
    results = []
    try:
        animes = search_aniteca_api(query, session=session)
        for anime in animes:
            episodios = get_chapter_links(anime["id"], anime["numepisodios"], session=session)
            for ep in episodios:
                deferred_link = partial(extract_direct_link, ep["servername"], ep["online_id"], session=session)
                results.append({
                    "title": anime["nombre"],
                    "chapter": ep["capitulo"],
                    "chapters": anime["numepisodios"],
                    "url_type": ep["servername"],
                    "url": deferred_link,
                    "resolucion": ep["resolucion"],
                    "idioma": ep["idioma"],
                    "subtitulo": ep["subtitulo"],
                    "fansub": ep["fansub"],
                    "format": ep["format"],
                    "password": ep["password"],
                })
    except Exception as exc:
        print(f"[Aniteca] Error: {exc}")
    return results


def search_aniteca_api(query, session=None):
    payload = {
        "perpage": 100,
        "page": 1,
        "orden": "ASC",
        "ordenby": "nombre",
        "maxcap": 1000,
        "mincap": 0,
        "maxyear": 2050,
        "minyear": 1800,
        "animename": query,
    }

    try:
        data = _post_aniteca_json("search", payload, session=session)
    except Exception as exc:
        print(f"[Aniteca API] Error: {exc}")
        return []

    resultados = []
    for anime in data.get("data", []):
        resultados.append({
            "id": str(anime.get("anime_id")),
            "nombre": anime.get("nombre"),
            "numepisodios": int(anime.get("numepisodios", 0)),
        })
    return resultados


def get_chapter_links(anime_id, ultimocap, session=None):
    payload = {
        "access": 3,
        "accounts": [],
        "animeid": anime_id,
        "base_number": 1,
        "cap": 1,
        "fansub": [],
        "id": anime_id,
        "last_number": ultimocap,
        "mbsize": 0,
        "resol": 144,
    }

    try:
        data = _post_aniteca_json("getchapters", payload, session=session)
    except Exception as exc:
        print(f"[GetChapters] Error: {exc}")
        return []

    links = []
    for entry in data.get("data", []):
        idioma = entry.get("idiomas", [{}])[0].get("idioma", {}).get("idioma") if entry.get("idiomas") else None
        subtitulo = entry.get("subtitulos", [{}])[0].get("subs", {}).get("subtitulo") if entry.get("subtitulos") else None
        fansub = entry.get("fansubs", [{}])[0].get("fansub", {}).get("nombrefansub") if entry.get("fansubs") else None
        links.append({
            "capitulo": entry["numcap"],
            "servername": entry["servername"],
            "online_id": entry["online_id"],
            "password": entry["password"],
            "format": entry["format"],
            "resolucion": entry["resol"],
            "idioma": idioma,
            "subtitulo": subtitulo,
            "fansub": fansub,
        })
    return links


def extract_direct_link(server, online_id, session=None):
    payload = {
        "server": server,
        "id": online_id,
        "noevent": True,
    }

    try:
        data = _post_aniteca_json("extractkey", payload, session=session)
    except Exception as exc:
        print(f"[ExtractKey] Error: {exc}")
        return None

    if server == "1fichier" and data.get("data"):
        return data["data"]
    if server == "mediafire" and data.get("data2"):
        return data["data2"]

    print(f"[ExtractKey] Sin enlace directo para server '{server}' y ID '{online_id}'")
    return None


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
