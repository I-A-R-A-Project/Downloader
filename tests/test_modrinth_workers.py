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
