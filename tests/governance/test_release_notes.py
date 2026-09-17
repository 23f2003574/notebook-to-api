from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.governance.artifact_registry import (
    ArtifactMetadata,
    ArtifactRegistry,
    router as artifact_registry_router,
)
from backend.governance.artifact_versioning import (
    ArtifactVersionManager,
    router as artifact_versioning_router,
)
from backend.governance.artifact_promotion import (
    ArtifactPromotionEngine,
    router as artifact_promotion_router,
)
from backend.governance.release_manager import (
    ReleaseManager,
    UnknownReleaseError,
    router as release_manager_router,
)
from backend.governance.release_notes import (
    NoReleaseNotesError,
    ReleaseNotes,
    ReleaseNotesGenerator,
    router as release_notes_router,
)

BASE_TIME = datetime(2026, 7, 27, 13, 0, 0, tzinfo=timezone.utc)


def _metadata() -> ArtifactMetadata:
    return ArtifactMetadata(
        content_type="application/octet-stream",
        size_bytes=1024,
        checksum="a" * 64,
        checksum_algorithm="sha256",
    )


@pytest.fixture
def registry() -> ArtifactRegistry:
    return ArtifactRegistry()


@pytest.fixture
def version_manager(registry: ArtifactRegistry) -> ArtifactVersionManager:
    return ArtifactVersionManager(registry=registry)


@pytest.fixture
def promotion_engine(version_manager: ArtifactVersionManager) -> ArtifactPromotionEngine:
    return ArtifactPromotionEngine(version_manager=version_manager)


@pytest.fixture
def release_manager(promotion_engine: ArtifactPromotionEngine) -> ReleaseManager:
    return ReleaseManager(promotion_engine=promotion_engine)


@pytest.fixture
def generator(release_manager: ReleaseManager) -> ReleaseNotesGenerator:
    return ReleaseNotesGenerator(release_manager=release_manager)


def _make_release(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    name: str = "svc-a",
    version: str = "1.0.0",
) -> str:
    registry.publish(
        name, version, location=f"loc-{name}-{version}", metadata=_metadata(), timestamp=BASE_TIME
    )
    version_manager.create(name, version, timestamp=BASE_TIME)
    promotion_engine.promote(name, version, "Staging", timestamp=BASE_TIME)
    promotion_engine.promote(name, version, "Production", timestamp=BASE_TIME)
    release = release_manager.create(
        "release-1", [{"name": name, "version": version}], timestamp=BASE_TIME
    )
    return release.release_id


def test_generate_categorizes_commits(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    generator: ReleaseNotesGenerator,
):
    release_id = _make_release(registry, version_manager, promotion_engine, release_manager)

    notes = generator.generate(
        release_id,
        commits=["feat: add search", "fix: null pointer", "chore(deps): bump requests"],
        timestamp=BASE_TIME,
    )

    assert isinstance(notes, ReleaseNotes)
    sections = {section.title: section.entries for section in notes.sections}
    assert sections["Features"] == ("feat: add search",)
    assert sections["Bug Fixes"] == ("fix: null pointer",)
    assert sections["Dependencies"] == ("chore(deps): bump requests",)


def test_generate_requires_known_release(generator: ReleaseNotesGenerator):
    with pytest.raises(UnknownReleaseError):
        generator.generate("does-not-exist")


def test_generate_records_history(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    generator: ReleaseNotesGenerator,
):
    release_id = _make_release(registry, version_manager, promotion_engine, release_manager)

    generator.generate(release_id, commits=["feat: a"], timestamp=BASE_TIME)
    generator.generate(release_id, commits=["feat: a", "feat: b"], timestamp=BASE_TIME)

    history = generator.history(release_id)

    assert len(history) == 2


def test_generate_attaches_notes_id_to_release(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    generator: ReleaseNotesGenerator,
):
    release_id = _make_release(registry, version_manager, promotion_engine, release_manager)

    notes = generator.generate(release_id, commits=["feat: a"], timestamp=BASE_TIME)

    assert release_manager.get(release_id).notes_id == notes.notes_id


def test_generate_applies_manual_overrides(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    generator: ReleaseNotesGenerator,
):
    release_id = _make_release(registry, version_manager, promotion_engine, release_manager)

    notes = generator.generate(
        release_id,
        commits=["feat: a"],
        overrides={"Known Issues": ["dashboard is slow under load"]},
        timestamp=BASE_TIME,
    )

    sections = {section.title: section.entries for section in notes.sections}
    assert sections["Known Issues"] == ("dashboard is slow under load",)


def test_generate_rejects_unknown_override_section(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    generator: ReleaseNotesGenerator,
):
    release_id = _make_release(registry, version_manager, promotion_engine, release_manager)

    with pytest.raises(ValueError):
        generator.generate(release_id, overrides={"Nonexistent": ["x"]})


def test_generate_uses_configurable_template(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    generator: ReleaseNotesGenerator,
):
    release_id = _make_release(registry, version_manager, promotion_engine, release_manager)

    notes = generator.generate(
        release_id, template="Changelog :: {release_id}", timestamp=BASE_TIME
    )

    assert notes.title == f"Changelog :: {release_id}"


def test_preview_does_not_persist_or_attach(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    generator: ReleaseNotesGenerator,
):
    release_id = _make_release(registry, version_manager, promotion_engine, release_manager)

    preview = generator.preview(release_id, commits=["feat: a"])

    assert preview.notes_id is None
    assert generator.history(release_id) == []
    assert release_manager.get(release_id).notes_id is None


def test_preview_requires_known_release(generator: ReleaseNotesGenerator):
    with pytest.raises(UnknownReleaseError):
        generator.preview("does-not-exist")


def test_export_returns_markdown(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    generator: ReleaseNotesGenerator,
):
    release_id = _make_release(registry, version_manager, promotion_engine, release_manager)
    generator.generate(release_id, commits=["feat: add search"], timestamp=BASE_TIME)

    markdown = generator.export(release_id)

    assert markdown.startswith("# Release Notes")
    assert "## Features" in markdown
    assert "- feat: add search" in markdown


def test_export_omits_empty_sections(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    generator: ReleaseNotesGenerator,
):
    release_id = _make_release(registry, version_manager, promotion_engine, release_manager)
    generator.generate(release_id, commits=["feat: add search"], timestamp=BASE_TIME)

    markdown = generator.export(release_id)

    assert "## Bug Fixes" not in markdown


def test_export_without_generation_raises(
    registry: ArtifactRegistry,
    version_manager: ArtifactVersionManager,
    promotion_engine: ArtifactPromotionEngine,
    release_manager: ReleaseManager,
    generator: ReleaseNotesGenerator,
):
    release_id = _make_release(registry, version_manager, promotion_engine, release_manager)

    with pytest.raises(NoReleaseNotesError):
        generator.export(release_id)


def test_history_empty_when_never_generated(generator: ReleaseNotesGenerator):
    assert generator.history("does-not-exist") == []


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(artifact_registry_router)
    app.include_router(artifact_versioning_router)
    app.include_router(artifact_promotion_router)
    app.include_router(release_manager_router)
    app.include_router(release_notes_router)
    return TestClient(app)


def _publish_promote_release_via_api(client: TestClient, name: str, version: str, release_name: str) -> str:
    client.post(
        "/governance/artifacts",
        json={
            "name": name,
            "version": version,
            "location": f"loc-{name}-{version}",
            "metadata": {
                "content_type": "application/octet-stream",
                "size_bytes": 1024,
                "checksum": "a" * 64,
                "checksum_algorithm": "sha256",
            },
        },
    )
    client.post(f"/governance/artifacts/{name}/versions", json={"version": version})
    client.post(
        f"/governance/artifacts/{name}/promote",
        json={"version": version, "target_environment": "Staging"},
    )
    client.post(
        f"/governance/artifacts/{name}/promote",
        json={"version": version, "target_environment": "Production"},
    )
    create_response = client.post(
        "/governance/releases",
        json={"name": release_name, "artifacts": [{"name": name, "version": version}]},
    )
    return create_response.json()["release_id"]


def test_api_generate_and_get_notes(client: TestClient):
    release_id = _publish_promote_release_via_api(client, "svc-notes-a", "1.0.0", "release-notes-api-1")

    generate_response = client.post(
        f"/governance/releases/{release_id}/notes",
        json={"commits": ["feat: add search", "fix: crash on startup"]},
    )
    get_response = client.get(f"/governance/releases/{release_id}/notes")

    assert generate_response.status_code == 200
    assert get_response.status_code == 200
    sections = {s["title"]: s["entries"] for s in get_response.json()["sections"]}
    assert sections["Features"] == ["feat: add search"]
    assert sections["Bug Fixes"] == ["fix: crash on startup"]


def test_api_generate_unknown_release_returns_404(client: TestClient):
    response = client.post("/governance/releases/does-not-exist/notes", json={})

    assert response.status_code == 404


def test_api_generate_invalid_override_returns_422(client: TestClient):
    release_id = _publish_promote_release_via_api(client, "svc-notes-b", "1.0.0", "release-notes-api-2")

    response = client.post(
        f"/governance/releases/{release_id}/notes",
        json={"overrides": {"Nonexistent": ["x"]}},
    )

    assert response.status_code == 422


def test_api_get_notes_before_generation_returns_404(client: TestClient):
    release_id = _publish_promote_release_via_api(client, "svc-notes-c", "1.0.0", "release-notes-api-3")

    response = client.get(f"/governance/releases/{release_id}/notes")

    assert response.status_code == 404
