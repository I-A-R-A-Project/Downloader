from mod_search import workers


def test_normalize_modrinth_search_hit_maps_core_fields():
    hit = {
        "project_id": "proj123",
        "slug": "cool-mod",
        "project_type": "mod",
        "title": "Cool Mod",
        "author": "Nexxus",
        "description": "Improves blocks",
        "display_categories": ["optimization", "utility"],
        "loaders": ["fabric", "quilt"],
        "versions": ["1.20.1", "1.20.4"],
        "downloads": 18234,
        "date_modified": "2026-04-01T10:20:30.000Z",
        "icon_url": "https://cdn.modrinth.com/icon.png",
    }

    item = workers.normalize_modrinth_search_hit(hit)

    assert item["id"] == "proj123"
    assert item["url"] == "https://modrinth.com/mod/cool-mod"
    assert item["name"] == "Cool Mod"
    assert item["author"] == "Nexxus"
    assert item["category"] == "optimization, utility, fabric"
    assert item["downloads_text"] == "18.2k"
    assert item["updated_text"] == "2026-04-01 10:20:30 UTC"


def test_normalize_modrinth_search_hit_ignores_non_mod_projects():
    hit = {
        "project_id": "pack123",
        "slug": "cool-pack",
        "project_type": "modpack",
    }

    assert workers.normalize_modrinth_search_hit(hit) is None


def test_pick_modrinth_primary_file_prefers_explicit_primary():
    version = {
        "files": [
            {"filename": "secondary.jar", "url": "https://cdn.example/secondary.jar", "primary": False},
            {"filename": "primary.jar", "url": "https://cdn.example/primary.jar", "primary": True},
        ]
    }

    picked = workers.pick_modrinth_primary_file(version)

    assert picked["filename"] == "primary.jar"


def test_pick_modrinth_primary_file_falls_back_to_first():
    version = {
        "files": [
            {"filename": "first.jar", "url": "https://cdn.example/first.jar"},
            {"filename": "second.jar", "url": "https://cdn.example/second.jar"},
        ]
    }

    picked = workers.pick_modrinth_primary_file(version)

    assert picked["filename"] == "first.jar"


def test_normalize_modrinth_version_option_keeps_dependencies_and_files():
    version = {
        "id": "ver123",
        "version_number": "1.2.3",
        "version_type": "release",
        "date_published": "2026-04-01T10:20:30.000Z",
        "loaders": ["fabric"],
        "game_versions": ["1.20.4"],
        "files": [
            {"filename": "cool-mod.jar", "url": "https://cdn.example/cool-mod.jar", "primary": True}
        ],
        "dependencies": [
            {"project_id": "dep123", "dependency_type": "required"},
            {"project_id": "dep456", "dependency_type": "optional"},
        ],
    }

    option = workers.normalize_modrinth_version_option(version)

    assert option["id"] == "ver123"
    assert option["primary_file"]["filename"] == "cool-mod.jar"
    assert option["published_text"] == "2026-04-01 10:20:30 UTC"
    assert len(option["dependencies"]) == 2


def test_filter_required_modrinth_dependencies_returns_only_required():
    deps = [
        {"project_id": "dep1", "dependency_type": "required"},
        {"project_id": "dep2", "dependency_type": "optional"},
        {"project_id": "dep3", "dependency_type": "embedded"},
    ]

    required = workers.filter_required_modrinth_dependencies(deps)

    assert required == [{"project_id": "dep1", "dependency_type": "required"}]


def test_normalize_factorio_target_version_keeps_major_minor():
    assert workers.normalize_factorio_target_version("Version: 2.0.72") == "2.0"


def test_factorio_release_matches_target_uses_info_json_version():
    release = {
        "version": "1.4.0",
        "info_json": {"factorio_version": "2.0"},
    }

    assert workers.factorio_release_matches_target(release, "2.0")
    assert not workers.factorio_release_matches_target(release, "1.1")


def test_parse_factorio_log_extracts_version_and_dependencies():
    log_text = """
    0.000 2026-04-07 10:11:12; Factorio 2.0.72 (build 12345)
    1.234 Error ModManager.cpp:123: Failed to load mod "space-age":
    Dependency "flib >= 0.16.0" is missing!
    1.235 Error ModManager.cpp:123: Mod portal-research requires "stdlib >= 2.1.0".
    """

    parsed = workers.parse_factorio_log(log_text)

    assert parsed["factorio_version"] == "2.0"
    assert parsed["dependencies"] == ["flib >= 0.16.0", "stdlib >= 2.1.0"]


def test_parse_factorio_log_supports_spanish_modmanager_format():
    log_text = """
       0.001 2026-04-07 02:07:56; Factorio 2.0.72 (build 84292, win64, steam, space-age)
       4.982 Error ModManager.cpp:1764: Error al cargar el mod "Arcanyx":
    • Arcanyx
        • Dependencia base >= 2.0.73 no está satisfecha (activa: base 2.0.72)
        • Dependencia space-age >= 2.0.73 no está satisfecha (activa: space-age 2.0.72)
    • Cerys-Moon-of-Fulgora
        • Falta la dependencia requerida Flare Stack >= 4.1.0
    • maraxsis
        • Falta la dependencia requerida FluidMustFlow >= 1.4.2
    • pelagos-autobarreling
        • Falta la dependencia requerida barreling_machines <= 0.1.2
    • Flare
        • Versión incompatible de Factorio (actual: 2.0, requerida: 0.17)
    """

    parsed = workers.parse_factorio_log(log_text)

    assert parsed["factorio_version"] == "2.0"
    assert parsed["factorio_runtime_version"] == "2.0.72"
    assert parsed["component_versions"]["base"] == "2.0.72"
    assert parsed["component_versions"]["space-age"] == "2.0.72"
    assert parsed["dependencies"] == [
        "Flare Stack >= 4.1.0",
        "FluidMustFlow >= 1.4.2",
        "barreling_machines <= 0.1.2",
    ]
    assert parsed["replacement_mods"] == ["Arcanyx", "Flare"]
