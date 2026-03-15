import re
import html
from urllib.parse import urlparse, parse_qs

import requests


USER_AGENT = "Mozilla/5.0"


def is_gdrive_url(url):
    return "drive.google.com" in url


def parse_gdrive_file_id(url):
    patterns = [
        r"/file/d/([a-zA-Z0-9_-]+)",
        r"/uc\?id=([a-zA-Z0-9_-]+)",
        r"/open\?id=([a-zA-Z0-9_-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    try:
        parsed = urlparse(url)
        query_id = parse_qs(parsed.query).get("id", [None])[0]
        if query_id:
            return query_id
    except Exception:
        pass
    return None


def parse_gdrive_folder_id(url):
    patterns = [
        r"/folders/([a-zA-Z0-9_-]+)",
        r"/drive/folders/([a-zA-Z0-9_-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def gdown_available():
    try:
        import gdown  # noqa: F401
        return True
    except Exception:
        return False


def _get_confirm_token(response_text, response_cookies):
    for key, value in response_cookies.items():
        if key.startswith("download_warning"):
            return value
    match = re.search(r"confirm=([0-9A-Za-z_]+)", response_text or "")
    if match:
        return match.group(1)
    match = re.search(r'name="confirm"\s+value="([0-9A-Za-z_]+)"', response_text or "")
    if match:
        return match.group(1)
    return None


def _extract_filename_from_headers(headers):
    content_disposition = headers.get("content-disposition") or headers.get("Content-Disposition") or ""
    if not content_disposition:
        return None
    match = re.search(r"filename\*=UTF-8''([^;]+)", content_disposition)
    if match:
        return requests.utils.unquote(match.group(1))
    match = re.search(r'filename="?([^";]+)"?', content_disposition)
    if match:
        return match.group(1)
    return None


def _extract_title_from_html(text):
    if not text:
        return None
    match = re.search(r"<title>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    title = match.group(1).strip()
    if " - Google Drive" in title:
        title = title.replace(" - Google Drive", "").strip()
    return title or None


def resolve_gdrive_file(url, session=None):
    file_id = parse_gdrive_file_id(url)
    if not file_id:
        return None
    session = session or requests.Session()
    headers = {"User-Agent": USER_AGENT}

    download_url = f"https://drive.google.com/uc?export=download&id={file_id}"
    response = session.get(download_url, headers=headers, timeout=20, stream=True)
    content_type = response.headers.get("content-type", "")
    filename = _extract_filename_from_headers(response.headers)
    text = None

    if content_type.startswith("text/html"):
        text = response.text
        token = _get_confirm_token(text, response.cookies)
        if token:
            download_url = (
                f"https://drive.google.com/uc?export=download&confirm={token}&id={file_id}"
            )
            response = session.get(download_url, headers=headers, timeout=20, stream=True)
            filename = filename or _extract_filename_from_headers(response.headers)
    else:
        response.close()

    if not filename:
        filename = f"{file_id}.bin"

    cookies = session.cookies.get_dict()
    return {
        "filename": filename,
        "download_url": download_url,
        "cookies": cookies,
        "headers": headers,
    }


def resolve_gdrive_folder_zip(url, session=None):
    folder_id = parse_gdrive_folder_id(url)
    if not folder_id:
        return None
    session = session or requests.Session()
    headers = {"User-Agent": USER_AGENT}

    folder_url = f"https://drive.google.com/drive/folders/{folder_id}"
    response = session.get(folder_url, headers=headers, timeout=20)
    folder_name = _extract_title_from_html(response.text) or f"{folder_id}"

    download_url = None
    match = re.search(r'"downloadUrl":"(.*?)"', response.text or "")
    if match:
        raw = match.group(1)
        raw = raw.replace("\\u0026", "&").replace("\\u003d", "=").replace("\\/", "/")
        download_url = html.unescape(raw)

    if not download_url:
        download_url = f"https://drive.google.com/uc?export=download&id={folder_id}"

    response = session.get(download_url, headers=headers, timeout=20, stream=True)
    content_type = response.headers.get("content-type", "")
    text = response.text if content_type.startswith("text/html") else ""
    if not content_type.startswith("text/html"):
        response.close()
    token = _get_confirm_token(text, response.cookies)
    if token:
        download_url = (
            f"https://drive.google.com/uc?export=download&confirm={token}&id={folder_id}"
        )

    cookies = session.cookies.get_dict()
    filename = f"{folder_name}.zip"

    return {
        "filename": filename,
        "download_url": download_url,
        "cookies": cookies,
        "headers": headers,
    }


def gdown_download(url, output_path, is_folder=False):
    import gdown

    if is_folder:
        return gdown.download_folder(url, output=output_path, quiet=True, use_cookies=True)
    return gdown.download(url, output=output_path, quiet=True, fuzzy=True, use_cookies=True)
