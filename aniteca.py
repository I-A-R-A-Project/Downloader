import requests

headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/123.0.0.0 Safari/537.36",
        "Origin": "https://aniteca.net",
        "Referer": "https://aniteca.net/"
    }

def search_aniteca(query):
    results = []
    try:
        animes = search_aniteca_api(query)
        for anime in animes:
            episodios = get_chapter_links(anime["id"], anime["numepisodios"])
            for ep in episodios:
                try:
                    link_directo = extract_direct_link(ep['servername'], ep['online_id'])
                    if link_directo:
                        results.append({
                            "title": anime['nombre'],
                            "chapter": ep['capitulo'],
                            "chapters": anime["numepisodios"],
                            "url_type": ep['servername'],
                            "url": link_directo
                        })
                except Exception as e:
                    print(f"[LinkError] {e}")
    except Exception as e:
        print(f"[Aniteca] Error: {e}")
    return results

# Paso 1: Obtener el animeid y número de capítulos
def search_aniteca_api(query):
    url = "https://aniteca.net/aniapi/api/search"
    payload = {
        "perpage": 100,
        "page": 1,
        "orden": "ASC",
        "ordenby": "nombre",
        "maxcap": 1000,
        "mincap": 0,
        "maxyear": 2030,
        "minyear": 1917,
        "animename": query
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()

        resultados = []
        for anime in data.get("data", []):
            resultados.append({
                "id": str(anime.get("anime_id")),
                "nombre": anime.get("nombre"),
                "numepisodios": int(anime.get("numepisodios", 0))
            })

        return resultados

    except Exception as e:
        print(f"[Aniteca API] Error: {e}")
        print("Respuesta recibida:", response.text[:200])
        return []

# Paso 2: Obtener capítulos y enlaces de descarga
def get_chapter_links(anime_id, ultimocap):
    url = "https://aniteca.net/aniapi/api/getchapters"
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
        "resol": 144
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()

        links = []
        for entry in data["data"]:
            links.append({
                "capitulo": entry["numcap"],
                "servername": entry["servername"],
                "online_id": entry["online_id"],
                "password": entry["password"],
                "format": entry["format"]
            })
        return links
    except Exception as e:
        print(f"[GetChapters] Error: {e}")
        print("Respuesta:", response.text[:200])
        return []

def extract_direct_link(server, online_id):
    url = "https://aniteca.net/aniapi/api/extractkey"
    payload = {
        "server": server,
        "id": online_id,
        "noevent": True
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()

        if server=="1fichier" and "data" in data and data["data"]:
            return data["data"]  # Enlace directo
        elif server=="mediafire" and "data2" in data and data["data2"]:
            return data["data2"]  # Enlace directo
        else:
            print(f"[ExtractKey] Sin enlace directo para server '{server}' y ID '{online_id}'")
            return None

    except Exception as e:
        print(f"[ExtractKey] Error: {e}")
        print("Respuesta:", response.text[:200])
        return None

# Ejemplo de uso
if __name__ == "__main__":
    animes = search_aniteca_api("kodomo no jikan")
    for anime in animes:
        print(f"\nAnime: {anime['nombre']} (ID: {anime['id']})")
        episodios = get_chapter_links(anime["id"], anime["numepisodios"])
        for ep in episodios:
            print(f"Cap {ep['capitulo']} - {ep['servername']} (ID: {ep['online_id']})")
            link_directo = extract_direct_link(ep['servername'], ep['online_id'])
            if link_directo:
                print(" ➤ Enlace directo:", link_directo)


