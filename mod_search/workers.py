import json
import random
import re
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup
from PyQt5.QtCore import QObject, QRunnable, pyqtSignal


FACTORIO_BASE = "https://mods.factorio.com"
MODINFO_URL = "https://re146.dev/factorio/mods/modinfo"
MODRINTH_API_BASE = "https://api.modrinth.com/v2"
MODRINTH_SITE_BASE = "https://modrinth.com"
MODRINTH_USER_AGENT = "MediaSearchPrototype/1.0 (Modrinth integration)"
MODRINTH_ALLOWED_PROJECT_TYPES = {"mod"}
MODRINTH_ALLOWED_DEPENDENCY_TYPES = {"required"}
MODRINTH_SEARCH_PAGE_LIMIT = 20
MODRINTH_INDEX_BY_MODE = {
    "popular": "downloads",
    "updated": "updated",
    "newest": "newest",
}
FACTORIO_VERSION_RE = re.compile(r"(\d+\.\d+(?:\.\d+)*)")
FACTORIO_DEPENDENCY_QUOTED_RE = re.compile(r'(?:Dependency|dependency|requires)\s+"([^"]+)"')
FACTORIO_DEPENDENCY_INLINE_RE = re.compile(
    r'\brequires\s+([A-Za-z0-9_.-]+(?:\s*(?:>=|<=|=|>|<)\s*[A-Za-z0-9_.+-]+)?)',
    re.IGNORECASE,
)
FACTORIO_DEPENDENCY_MISSING_ES_RE = re.compile(
    r'Falta la dependencia requerida\s+(.+?)(?:\s*\(.*\))?$',
    re.IGNORECASE,
)
FACTORIO_DEPENDENCY_UNSATISFIED_ES_RE = re.compile(
    r'Dependencia\s+(.+?)\s+no est[aá]\s+satisfecha(?:\s*\(.*\))?$',
    re.IGNORECASE,
)
FACTORIO_DEPENDENCY_UNSATISFIED_ACTIVE_ES_RE = re.compile(
    r'Dependencia\s+(.+?)\s+no est[aá]\s+satisfecha\s+\(activa:\s*([A-Za-z0-9_.-]+)\s+([A-Za-z0-9_.+-]+)\)$',
    re.IGNORECASE,
)
FACTORIO_SPECIAL_DEPENDENCIES = {"base", "core", "space-age"}


def modrinth_headers():
    return {"User-Agent": MODRINTH_USER_AGENT}


class FactorioSearchSignals(QObject):
    finished = pyqtSignal(object, str)


class FactorioSearchWorker(QRunnable):
    def __init__(self, mode=None, query=None, page=1, extra_params=None):
        super().__init__()
        self.mode = mode
        self.query = query
        self.params = {
            "page": page,
            "factorio_version": 2.0,
            "show_deprecated": False,
        }
        if isinstance(extra_params, dict):
            for key, value in extra_params.items():
                if value in (None, "", [], ()):
                    continue
                self.params[key] = value
        self.signals = FactorioSearchSignals()

    def run(self):
        try:
            if self.query:
                url = f"{FACTORIO_BASE}/search"
                self.params["query"] = self.query
            else:
                url = f"{FACTORIO_BASE}/browse/{self.mode}"
            response = requests.get(url, params=self.params, timeout=15)
            response.raise_for_status()
            parsed = parse_mod_list(response.text)
            parsed["request_url"] = build_factorio_request_url(url, self.params)
            self.signals.finished.emit(parsed, "")
        except Exception as exc:
            self.signals.finished.emit([], str(exc))


def build_factorio_request_url(base_url, params):
    clean_params = {}
    for key, value in (params or {}).items():
        if value in (None, ""):
            continue
        clean_params[key] = value
    if not clean_params:
        return base_url
    return f"{base_url}?{urlencode(clean_params, doseq=True)}"


class FactorioInfoSignals(QObject):
    finished = pyqtSignal(str, dict, str)


class FactorioPageSignals(QObject):
    finished = pyqtSignal(str, str, str, str, str)


class FactorioInfoWorker(QRunnable):
    def __init__(self, mod_id):
        super().__init__()
        self.mod_id = mod_id
        self.signals = FactorioInfoSignals()

    def run(self):
        try:
            params = {"rand": f"{random.random():.18f}", "id": self.mod_id}
            response = requests.get(MODINFO_URL, params=params, timeout=15)
            response.raise_for_status()
            self.signals.finished.emit(self.mod_id, response.json(), "")
        except Exception as exc:
            self.signals.finished.emit(self.mod_id, {}, str(exc))


class FactorioPageWorker(QRunnable):
    def __init__(self, mod_id, url, title):
        super().__init__()
        self.mod_id = mod_id
        self.url = url
        self.title = title
        self.signals = FactorioPageSignals()

    def run(self):
        try:
            response = requests.get(self.url, timeout=15)
            response.raise_for_status()
            html = sanitize_factorio_mod_page_html(response.text)
            self.signals.finished.emit(self.mod_id, self.url, self.title, html, "")
        except Exception as exc:
            self.signals.finished.emit(self.mod_id, self.url, self.title, "", str(exc))


def sanitize_factorio_mod_page_html(html):
    soup = BeautifulSoup(html, "html.parser")
    selectors = [
        "div.top-bar",
        "div.header",
        "div.footer",
        "div#tabs-header",
        "ul.tabs",
    ]
    for selector in selectors:
        for node in soup.select(selector):
            node.decompose()

    body = soup.body
    if body is not None:
        current_style = body.get("style", "").strip()
        additions = [
            "margin-top: 0",
            "padding-top: 0",
        ]
        style = "; ".join(filter(None, [current_style] + additions))
        body["style"] = style

    head = soup.head
    if head is not None:
        style_tag = soup.new_tag("style")
        style_tag.string = """
body {
    margin-top: 0 !important;
    padding-top: 0 !important;
}
.top-bar,
.header,
.footer,
#tabs-header,
ul.tabs {
    display: none !important;
}
"""
        head.append(style_tag)
    return str(soup)


class ModrinthSearchSignals(QObject):
    finished = pyqtSignal(object, str)


class ModrinthSearchWorker(QRunnable):
    def __init__(self, mode=None, query=None, page=1):
        super().__init__()
        self.mode = mode or "popular"
        self.query = query or ""
        self.page = max(page, 1)
        self.signals = ModrinthSearchSignals()

    def run(self):
        try:
            payload = fetch_modrinth_search_page(self.mode, self.query, self.page)
            self.signals.finished.emit(payload, "")
        except Exception as exc:
            self.signals.finished.emit([], str(exc))


class ModrinthProjectSignals(QObject):
    finished = pyqtSignal(str, dict, str)


class ModrinthProjectWorker(QRunnable):
    def __init__(self, project_id):
        super().__init__()
        self.project_id = project_id
        self.signals = ModrinthProjectSignals()

    def run(self):
        try:
            response = requests.get(
                f"{MODRINTH_API_BASE}/project/{self.project_id}",
                headers=modrinth_headers(),
                timeout=15,
            )
            response.raise_for_status()
            self.signals.finished.emit(self.project_id, response.json(), "")
        except Exception as exc:
            self.signals.finished.emit(self.project_id, {}, str(exc))


class ModrinthVersionsSignals(QObject):
    finished = pyqtSignal(str, list, str)


class ModrinthVersionsWorker(QRunnable):
    def __init__(self, project_id):
        super().__init__()
        self.project_id = project_id
        self.signals = ModrinthVersionsSignals()

    def run(self):
        try:
            versions = fetch_modrinth_project_versions(self.project_id)
            self.signals.finished.emit(self.project_id, versions, "")
        except Exception as exc:
            self.signals.finished.emit(self.project_id, [], str(exc))


class DependencyResolveSignals(QObject):
    progress = pyqtSignal(str)
    finished = pyqtSignal(list)


class FactorioLogSignals(QObject):
    finished = pyqtSignal(dict, str)


class FactorioLogWorker(QRunnable):
    def __init__(self, log_path):
        super().__init__()
        self.log_path = log_path
        self.signals = FactorioLogSignals()

    def run(self):
        try:
            if not self.log_path:
                raise FileNotFoundError("No hay ruta configurada para factorio-current.log.")
            with open(self.log_path, "r", encoding="utf-8", errors="replace") as handle:
                payload = parse_factorio_log(handle.read())
            payload["log_path"] = self.log_path
            self.signals.finished.emit(payload, "")
        except Exception as exc:
            self.signals.finished.emit({}, str(exc))


class DependencyResolveWorker(QRunnable):
    def __init__(self, window, dependencies, visited):
        super().__init__()
        self.window = window
        self.dependencies = dependencies
        self.visited = visited
        self.signals = DependencyResolveSignals()

    def run(self):
        items = self.window.resolve_dependencies(
            self.dependencies,
            visited=self.visited,
            progress_cb=self.signals.progress.emit,
        )
        self.signals.finished.emit(items)


def parse_page_bar(soup):
    total = None
    current_page = None
    last_page = None

    label = soup.select_one("div.grey")
    if label:
        match = re.search(r"Found\s+(\d+)\s+mods", label.get_text(strip=True))
        if match:
            total = int(match.group(1))

    for anchor in soup.select("a.button.square-sm"):
        href = anchor.get("href") or ""
        if "page=" not in href:
            continue
        num_match = re.search(r"[?&]page=(\d+)", href)
        if not num_match:
            continue
        page_num = int(num_match.group(1))
        last_page = max(last_page or page_num, page_num)
        if "active" in (anchor.get("class") or []):
            current_page = page_num

    return {
        "total": total,
        "current_page": current_page,
        "last_page": last_page,
    }


def parse_mod_list(html):
    soup = BeautifulSoup(html, "html.parser")
    items = []
    seen = set()
    mod_list = soup.select_one("div.mod-list")
    if not mod_list:
        mod_list = soup

    containers = mod_list.select("div.panel-inset-lighter.flex-column.p0")
    if not containers:
        containers = mod_list.select("div.panel-inset-lighter")

    for container in containers:
        name_tag = container.select_one("h2 a.result-field[href^='/mod/']")
        if not name_tag:
            continue
        href = (name_tag.get("href") or "").split("#")[0]
        if not href.startswith("/mod/"):
            continue
        clean_href = href.split("?")[0]
        mod_url = f"{FACTORIO_BASE}{clean_href}"
        mod_id = clean_href.split("/mod/")[-1].strip("/")
        if mod_url in seen:
            continue
        seen.add(mod_url)

        author = ""
        author_url = ""
        description = ""
        category = ""
        updated_text = ""
        updated_title = ""
        versions = ""
        downloads_text = ""
        downloads_exact = ""
        thumbnail = ""

        author_tag = container.select_one("a[href^='/user/']")
        if author_tag:
            author = author_tag.get_text(strip=True)
            author_url = f"{FACTORIO_BASE}{author_tag.get('href', '')}"

        desc_tag = container.select_one("p.result-field")
        if desc_tag:
            description = desc_tag.get_text(" ", strip=True)

        category_tag = container.select_one(".category-label")
        if category_tag:
            category = category_tag.get_text(" ", strip=True)

        updated_tag = container.select_one("div[title='Last updated'] span")
        if updated_tag:
            updated_text = updated_tag.get_text(" ", strip=True)
            updated_title = updated_tag.parent.get("title", "")

        versions_tag = container.select_one("div[title='Factorio version'] span")
        if versions_tag:
            versions = versions_tag.get_text(" ", strip=True)

        downloads_tag = container.select_one("div[title='Downloads']")
        if downloads_tag:
            downloads_exact = downloads_tag.get("title", "")
            downloads_text = downloads_tag.get_text(" ", strip=True)

        image_tag = container.select_one("img")
        if image_tag:
            thumbnail = image_tag.get("src", "")

        items.append({
            "source": "factorio",
            "id": mod_id,
            "slug": mod_id,
            "url": mod_url,
            "web_url": mod_url,
            "name": name_tag.get_text(strip=True) or "Sin título",
            "author": author,
            "author_url": author_url,
            "description": description,
            "category": category,
            "categories": [category] if category else [],
            "updated_text": updated_text,
            "updated_title": updated_title,
            "versions": versions,
            "downloads_text": downloads_text,
            "downloads_exact": downloads_exact,
            "thumbnail": thumbnail,
        })

    page_data = parse_page_bar(soup)
    return {
        "items": items,
        "total": page_data["total"],
        "page": page_data["current_page"] or 1,
        "last_page": page_data["last_page"],
    }


def fetch_modrinth_search_page(mode, query, page):
    page = max(page, 1)
    offset = (page - 1) * MODRINTH_SEARCH_PAGE_LIMIT
    index = MODRINTH_INDEX_BY_MODE.get(mode or "", "downloads")
    params = {
        "query": query or "",
        "limit": MODRINTH_SEARCH_PAGE_LIMIT,
        "offset": offset,
        "index": index,
        "facets": json.dumps([["project_type:mod"]]),
    }
    response = requests.get(
        f"{MODRINTH_API_BASE}/search",
        params=params,
        headers=modrinth_headers(),
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    hits = payload.get("hits") or []
    total = payload.get("total_hits")
    normalized = [normalize_modrinth_search_hit(hit) for hit in hits]
    items = [item for item in normalized if item]
    last_page = None
    if isinstance(total, int) and total >= 0:
        last_page = max(1, (total + MODRINTH_SEARCH_PAGE_LIMIT - 1) // MODRINTH_SEARCH_PAGE_LIMIT)
    return {
        "items": items,
        "total": total,
        "page": page,
        "last_page": last_page,
    }


def normalize_factorio_target_version(value):
    text = str(value or "").strip()
    match = FACTORIO_VERSION_RE.search(text)
    if not match:
        return ""
    parts = match.group(1).split(".")
    if len(parts) >= 2:
        return f"{parts[0]}.{parts[1]}"
    return match.group(1)


def extract_factorio_runtime_version_from_log_text(text):
    for line in (text or "").splitlines():
        lower = line.lower()
        if "factorio" not in lower:
            continue
        after_keyword = line[lower.index("factorio") + len("factorio"):]
        match = FACTORIO_VERSION_RE.search(after_keyword)
        if match:
            return match.group(1)
    return ""


def extract_release_factorio_version(release):
    if not isinstance(release, dict):
        return ""
    candidates = [release.get("factorio_version")]
    info_json = release.get("info_json") or {}
    if isinstance(info_json, dict):
        candidates.extend(
            [
                info_json.get("factorio_version"),
                info_json.get("game_version"),
                info_json.get("factorioVersion"),
            ]
        )
    for candidate in candidates:
        normalized = normalize_factorio_target_version(candidate)
        if normalized:
            return normalized
    return ""


def factorio_release_matches_target(release, target_version):
    target = normalize_factorio_target_version(target_version)
    if not target:
        return True
    release_version = extract_release_factorio_version(release)
    if not release_version:
        return False
    return release_version == target


def extract_factorio_version_from_log_text(text):
    return normalize_factorio_target_version(extract_factorio_runtime_version_from_log_text(text))


def parse_factorio_dependency_issues_from_log(text):
    dependencies = []
    seen = set()
    for line in (text or "").splitlines():
        stripped = line.strip()
        lower = stripped.lower()
        if (
            "depend" not in lower
            and "require" not in lower
            and "falta la dependencia requerida" not in lower
            and "no está satisfecha" not in lower
            and "no esta satisfecha" not in lower
        ):
            continue
        matches = []
        matches.extend(FACTORIO_DEPENDENCY_QUOTED_RE.findall(stripped))
        matches.extend(FACTORIO_DEPENDENCY_INLINE_RE.findall(stripped))
        missing_match = FACTORIO_DEPENDENCY_MISSING_ES_RE.search(stripped)
        if missing_match:
            matches.append(missing_match.group(1))
        unsatisfied_match = FACTORIO_DEPENDENCY_UNSATISFIED_ES_RE.search(stripped)
        if unsatisfied_match:
            matches.append(unsatisfied_match.group(1))

        for match in matches:
            dependency = " ".join(str(match).split()).strip(" .:")
            if not dependency:
                continue
            name = dependency.split()[0].strip().lower()
            if name in FACTORIO_SPECIAL_DEPENDENCIES:
                continue
            if dependency not in seen:
                seen.add(dependency)
                dependencies.append(dependency)
    return dependencies


def parse_factorio_log_component_versions(text):
    versions = {}
    runtime = extract_factorio_runtime_version_from_log_text(text)
    if runtime:
        versions["base"] = runtime

    current_mod = ""
    for line in (text or "").splitlines():
        raw = line.rstrip()
        stripped = raw.strip()
        if not stripped.startswith("•"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        content = stripped.lstrip("•").strip()
        if not content:
            continue
        lower = content.lower()
        if not lower.startswith(
            (
                "dependencia ",
                "falta la dependencia requerida ",
                "incompatible con ",
                "versión incompatible de factorio",
                "version incompatible de factorio",
            )
        ):
            current_mod = content
            continue

        match = FACTORIO_DEPENDENCY_UNSATISFIED_ACTIVE_ES_RE.search(content)
        if not match:
            continue
        active_name = (match.group(2) or "").strip().lower()
        active_version = (match.group(3) or "").strip()
        if active_name in FACTORIO_SPECIAL_DEPENDENCIES and active_version:
            versions[active_name] = active_version
    return versions


def parse_factorio_log_replacement_mods(text):
    replacements = []
    seen = set()
    current_mod = ""
    for line in (text or "").splitlines():
        raw = line.rstrip()
        stripped = raw.strip()
        if not stripped.startswith("•"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        content = stripped.lstrip("•").strip()
        if not content:
            continue
        lower = content.lower()
        if not lower.startswith(
            (
                "dependencia ",
                "falta la dependencia requerida ",
                "incompatible con ",
                "versión incompatible de factorio",
                "version incompatible de factorio",
            )
        ):
            current_mod = content
            continue
        if not current_mod:
            continue

        active_match = FACTORIO_DEPENDENCY_UNSATISFIED_ACTIVE_ES_RE.search(content)
        if active_match:
            dep_name = (active_match.group(2) or "").strip().lower()
            if dep_name in FACTORIO_SPECIAL_DEPENDENCIES and current_mod not in seen:
                seen.add(current_mod)
                replacements.append(current_mod)
            continue

        if lower.startswith(("versión incompatible de factorio", "version incompatible de factorio")):
            if current_mod not in seen:
                seen.add(current_mod)
                replacements.append(current_mod)
    return replacements


def parse_factorio_log(text):
    return {
        "factorio_version": extract_factorio_version_from_log_text(text),
        "factorio_runtime_version": extract_factorio_runtime_version_from_log_text(text),
        "dependencies": parse_factorio_dependency_issues_from_log(text),
        "component_versions": parse_factorio_log_component_versions(text),
        "replacement_mods": parse_factorio_log_replacement_mods(text),
    }


def build_factorio_dependency_candidates(name):
    base = str(name or "").strip()
    if not base:
        return []
    variants = [base]
    space_to_dash = re.sub(r"\s+", "-", base)
    space_to_underscore = re.sub(r"\s+", "_", base)
    dash_to_space = base.replace("-", " ")
    underscore_to_space = base.replace("_", " ")
    underscore_to_dash = base.replace("_", "-")
    dash_to_underscore = base.replace("-", "_")
    for candidate in (
        space_to_dash,
        space_to_underscore,
        dash_to_space,
        underscore_to_space,
        underscore_to_dash,
        dash_to_underscore,
    ):
        candidate = candidate.strip()
        if candidate and candidate not in variants:
            variants.append(candidate)
    return variants


def normalize_modrinth_search_hit(hit):
    if not isinstance(hit, dict):
        return None
    project_type = (hit.get("project_type") or "").strip().lower()
    if project_type not in MODRINTH_ALLOWED_PROJECT_TYPES:
        return None

    project_id = hit.get("project_id") or hit.get("slug") or hit.get("project_id")
    slug = hit.get("slug") or project_id or ""
    if not project_id and not slug:
        return None

    categories = hit.get("display_categories") or hit.get("categories") or []
    categories = [value for value in categories if isinstance(value, str) and value.strip()]
    loaders = hit.get("loaders") or []
    loaders = [value for value in loaders if isinstance(value, str) and value.strip()]
    game_versions = hit.get("versions") or []
    game_versions = [value for value in game_versions if isinstance(value, str) and value.strip()]

    category_parts = categories[:]
    for loader in loaders:
        if loader not in category_parts:
            category_parts.append(loader)

    updated_title = hit.get("date_modified") or ""
    return {
        "source": "modrinth",
        "id": project_id or slug,
        "project_id": project_id or slug,
        "slug": slug or project_id,
        "url": build_modrinth_project_url(hit),
        "web_url": build_modrinth_project_url(hit),
        "name": hit.get("title") or hit.get("slug") or "Sin título",
        "author": hit.get("author") or "Desconocido",
        "description": (hit.get("description") or "").strip(),
        "category": ", ".join(category_parts[:3]) if category_parts else "Mod",
        "categories": categories,
        "loaders": loaders,
        "game_versions": game_versions,
        "downloads": hit.get("downloads"),
        "downloads_text": format_download_count(hit.get("downloads")),
        "downloads_exact": str(hit.get("downloads") or ""),
        "updated_text": format_iso_datetime(updated_title),
        "updated_title": updated_title,
        "thumbnail": hit.get("icon_url") or "",
        "project_type": project_type,
    }


def build_modrinth_project_url(data):
    slug = data.get("slug") or data.get("project_id") or data.get("id") or ""
    project_type = (data.get("project_type") or "mod").strip().lower() or "mod"
    return f"{MODRINTH_SITE_BASE}/{project_type}/{slug}" if slug else ""


def fetch_modrinth_project_versions(project_id):
    response = requests.get(
        f"{MODRINTH_API_BASE}/project/{project_id}/version",
        headers=modrinth_headers(),
        timeout=15,
    )
    response.raise_for_status()
    versions = response.json()
    if not isinstance(versions, list):
        return []
    return [version for version in versions if isinstance(version, dict)]


def fetch_modrinth_version(version_id):
    response = requests.get(
        f"{MODRINTH_API_BASE}/version/{version_id}",
        headers=modrinth_headers(),
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


def pick_modrinth_primary_file(version):
    files = version.get("files") or []
    files = [file_info for file_info in files if is_valid_modrinth_file(file_info)]
    if not files:
        return None
    for file_info in files:
        if file_info.get("primary"):
            return file_info
    return files[0]


def is_valid_modrinth_file(file_info):
    if not isinstance(file_info, dict):
        return False
    url = file_info.get("url")
    filename = file_info.get("filename")
    return bool(url and filename)


def normalize_modrinth_version_option(version):
    if not isinstance(version, dict):
        return None
    primary_file = pick_modrinth_primary_file(version)
    if not primary_file:
        return None
    return {
        "id": version.get("id") or "",
        "name": version.get("name") or version.get("version_number") or "Versión",
        "version_number": version.get("version_number") or "",
        "version_type": version.get("version_type") or "",
        "published": version.get("date_published") or "",
        "published_text": format_iso_datetime(version.get("date_published") or ""),
        "downloads": version.get("downloads"),
        "downloads_text": format_download_count(version.get("downloads")),
        "loaders": [value for value in (version.get("loaders") or []) if isinstance(value, str)],
        "game_versions": [value for value in (version.get("game_versions") or []) if isinstance(value, str)],
        "files": [file_info for file_info in (version.get("files") or []) if is_valid_modrinth_file(file_info)],
        "primary_file": primary_file,
        "dependencies": [dep for dep in (version.get("dependencies") or []) if isinstance(dep, dict)],
        "raw": version,
    }


def filter_required_modrinth_dependencies(dependencies):
    return [
        dependency
        for dependency in dependencies
        if isinstance(dependency, dict)
        and (dependency.get("dependency_type") or "").lower() in MODRINTH_ALLOWED_DEPENDENCY_TYPES
    ]


def format_iso_datetime(value):
    if not value:
        return ""
    text = value.replace("T", " ")
    text = text.replace("Z", " UTC")
    if "." in text:
        prefix, suffix = text.split(".", 1)
        if " " in suffix:
            suffix = suffix[suffix.find(" "):]
        else:
            suffix = ""
        text = prefix + suffix
    return text.strip()


def format_download_count(value):
    if not isinstance(value, int):
        return ""
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return str(value)
