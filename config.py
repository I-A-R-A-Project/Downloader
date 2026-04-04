import json
import os


APPDATA = os.environ.get("APPDATA", os.path.expanduser("~"))
USERPROFILE = os.environ.get("USERPROFILE", os.path.expanduser("~"))

CONFIG_PATH = os.path.join(APPDATA, "MediaSearchPrototype", "config.json")
DEFAULT_CONFIG = {
    "folder_path": os.path.join(USERPROFILE, "Downloads"),
    "general_folder_path": os.path.join(USERPROFILE, "Downloads"),
    "anime_folder_path": os.path.join(USERPROFILE, "Downloads", "Anime"),
    "manga_folder_path": os.path.join(USERPROFILE, "Downloads", "Manga"),
    "vn_folder_path": os.path.join(USERPROFILE, "Downloads", "Visual Novels"),
    "games_folder_path": os.path.join(USERPROFILE, "Downloads", "Games"),
    "open_on_finish": False,
    "auto_extract_archives": False,
    "delete_archive_after_extract": False,
    "max_parallel_downloads": 2,
    "factorio_mods_path": os.path.join(APPDATA, "Factorio", "mods"),
    "minecraft_mods_path": os.path.join(APPDATA, ".minecraft", "mods"),
}


def normalize_path(value):
    if not value:
        return ""
    return os.path.normpath(value)


def load_config():
    if not os.path.exists(CONFIG_PATH):
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    merged = DEFAULT_CONFIG.copy()
    merged.update(data)
    for key, value in list(merged.items()):
        if key.endswith("_path") and isinstance(value, str):
            merged[key] = normalize_path(value)
    return merged


def save_config(config):
    config = dict(config)
    for key, value in list(config.items()):
        if key.endswith("_path") and isinstance(value, str):
            config[key] = normalize_path(value)
    config_dir = os.path.dirname(CONFIG_PATH)
    os.makedirs(config_dir, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)
