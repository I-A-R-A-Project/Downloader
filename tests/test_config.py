import os

import config


def test_default_config_includes_factorio_version_and_log_path():
    assert config.DEFAULT_CONFIG["factorio_target_version"] == "2.0"
    assert config.DEFAULT_CONFIG["factorio_log_path"].endswith(
        os.path.join("Factorio", "factorio-current.log")
    )


def test_normalize_path_normalizes_windows_style_paths():
    raw = os.path.join("C:\\Users", "Nexxus", "AppData", "Roaming", "Factorio", "..", "Factorio", "factorio-current.log")

    normalized = config.normalize_path(raw)

    assert normalized.endswith(os.path.join("Factorio", "factorio-current.log"))
