import io
import json
import tarfile
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.plugins.plugin_loader import ManifestValidationError, PluginManifest
from backend.plugins.plugin_packaging import (
    ChecksumMismatchError,
    PackageManifest,
    PluginPackage,
    PluginPackagingService,
    UnknownPackageError,
    UnsupportedPackageFormatError,
    get_plugin_packaging_service,
    router as plugin_packaging_router,
)


@pytest.fixture
def service() -> PluginPackagingService:
    return PluginPackagingService()


@pytest.fixture
def source_dir(tmp_path):
    (tmp_path / "main.py").write_text("VALUE = 1\n")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "helper.py").write_text("def helper():\n    return 2\n")
    return tmp_path


@pytest.fixture
def manifest() -> PluginManifest:
    return PluginManifest(
        name="csv-exporter",
        version="1.0.0",
        entry_point="csv_exporter_plugin.main",
        description="Exports notebooks to CSV",
        author="alice",
        tags=("export",),
    )


@pytest.fixture
def client(service: PluginPackagingService) -> TestClient:
    app = FastAPI()
    app.include_router(plugin_packaging_router)
    app.dependency_overrides[get_plugin_packaging_service] = lambda: service
    return TestClient(app)


def test_package_creates_zip_archive(service: PluginPackagingService, manifest: PluginManifest, source_dir):
    package = service.package(manifest, str(source_dir), format="zip")

    assert isinstance(package, PluginPackage)
    assert package.format == "zip"
    with zipfile.ZipFile(io.BytesIO(package.data)) as archive:
        names = set(archive.namelist())
        assert "manifest.json" in names
        assert "src/main.py" in names
        assert "src/nested/helper.py" in names


def test_package_creates_tar_gz_archive(service: PluginPackagingService, manifest: PluginManifest, source_dir):
    package = service.package(manifest, str(source_dir), format="tar.gz")

    assert package.format == "tar.gz"
    with tarfile.open(fileobj=io.BytesIO(package.data), mode="r:gz") as archive:
        names = set(archive.getnames())
        assert "manifest.json" in names
        assert "src/main.py" in names


def test_package_manifest_json_contains_checksum(service: PluginPackagingService, manifest: PluginManifest, source_dir):
    package = service.package(manifest, str(source_dir))

    with zipfile.ZipFile(io.BytesIO(package.data)) as archive:
        embedded = json.loads(archive.read("manifest.json"))

    assert embedded["checksum"] == package.manifest.checksum
    assert len(embedded["checksum"]) == 64  # sha256 hex digest


def test_package_rejects_unsupported_format(service: PluginPackagingService, manifest: PluginManifest, source_dir):
    with pytest.raises(UnsupportedPackageFormatError):
        service.package(manifest, str(source_dir), format="rar")


def test_package_rejects_missing_source_dir(service: PluginPackagingService, manifest: PluginManifest, tmp_path):
    with pytest.raises(FileNotFoundError):
        service.package(manifest, str(tmp_path / "does-not-exist"))


def test_package_is_deterministic_for_same_content(
    service: PluginPackagingService, manifest: PluginManifest, source_dir
):
    first = service.package(manifest, str(source_dir))
    second = service.package(manifest, str(source_dir))

    assert first.manifest.checksum == second.manifest.checksum


def test_verify_accepts_untampered_package(service: PluginPackagingService, manifest: PluginManifest, source_dir):
    package = service.package(manifest, str(source_dir))

    assert service.verify(package) is True


def test_verify_detects_tampered_content(service: PluginPackagingService, manifest: PluginManifest, source_dir):
    package = service.package(manifest, str(source_dir))

    with zipfile.ZipFile(io.BytesIO(package.data)) as original:
        manifest_bytes = original.read("manifest.json")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as tampered:
        tampered.writestr("manifest.json", manifest_bytes)
        tampered.writestr("src/main.py", "VALUE = 999\n")
    tampered_package = PluginPackage(manifest=package.manifest, format="zip", data=buffer.getvalue())

    with pytest.raises(ChecksumMismatchError):
        service.verify(tampered_package)


def test_import_package_round_trips_a_packaged_plugin(
    service: PluginPackagingService, manifest: PluginManifest, source_dir
):
    package = service.package(manifest, str(source_dir))

    imported = service.import_package(package.data, "zip")

    assert isinstance(imported, PackageManifest)
    assert imported.name == "csv-exporter"
    assert imported.version == "1.0.0"
    assert imported.checksum == package.manifest.checksum


def test_import_package_round_trips_tar_gz(service: PluginPackagingService, manifest: PluginManifest, source_dir):
    package = service.package(manifest, str(source_dir), format="tar.gz")

    imported = service.import_package(package.data, "tar.gz")

    assert imported.name == "csv-exporter"


def test_import_package_rejects_tampered_content(
    service: PluginPackagingService, manifest: PluginManifest, source_dir
):
    package = service.package(manifest, str(source_dir))
    with zipfile.ZipFile(io.BytesIO(package.data)) as original:
        manifest_bytes = original.read("manifest.json")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as tampered:
        tampered.writestr("manifest.json", manifest_bytes)
        tampered.writestr("src/main.py", "VALUE = 999\n")

    with pytest.raises(ChecksumMismatchError):
        service.import_package(buffer.getvalue(), "zip")


def test_import_package_rejects_missing_manifest(service: PluginPackagingService):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("src/main.py", "VALUE = 1\n")

    with pytest.raises(ManifestValidationError):
        service.import_package(buffer.getvalue(), "zip")


def test_import_package_rejects_invalid_manifest_fields(service: PluginPackagingService):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"name": "csv-exporter"}))

    with pytest.raises(ManifestValidationError):
        service.import_package(buffer.getvalue(), "zip")


def test_export_returns_cached_package(service: PluginPackagingService, manifest: PluginManifest, source_dir):
    service.package(manifest, str(source_dir))

    exported = service.export("csv-exporter")

    assert exported.manifest.name == "csv-exporter"


def test_export_unknown_plugin_raises(service: PluginPackagingService):
    with pytest.raises(UnknownPackageError):
        service.export("does-not-exist")


def test_export_as_manifest_json_returns_json_string(
    service: PluginPackagingService, manifest: PluginManifest, source_dir
):
    service.package(manifest, str(source_dir))

    exported = service.export("csv-exporter", format="manifest-json")

    parsed = json.loads(exported)
    assert parsed["name"] == "csv-exporter"


def test_export_incompatible_format_raises(service: PluginPackagingService, manifest: PluginManifest, source_dir):
    service.package(manifest, str(source_dir), format="zip")

    with pytest.raises(UnsupportedPackageFormatError):
        service.export("csv-exporter", format="tar.gz")


# --- API tests -------------------------------------------------------------


def test_api_package_then_download(client: TestClient, source_dir):
    response = client.post(
        "/plugins/package",
        json={
            "name": "csv-exporter",
            "version": "1.0.0",
            "entry_point": "csv_exporter_plugin.main",
            "source_dir": str(source_dir),
        },
    )
    assert response.status_code == 201
    assert response.json()["manifest"]["name"] == "csv-exporter"

    download = client.get("/plugins/package/csv-exporter")
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        assert "manifest.json" in archive.namelist()


def test_api_package_missing_source_dir_returns_422(client: TestClient):
    response = client.post(
        "/plugins/package",
        json={"name": "csv-exporter", "version": "1.0.0", "entry_point": "csv_exporter_plugin.main"},
    )

    assert response.status_code == 422


def test_api_package_invalid_manifest_returns_422(client: TestClient, source_dir):
    response = client.post(
        "/plugins/package", json={"name": "csv-exporter", "source_dir": str(source_dir)}
    )

    assert response.status_code == 422


def test_api_download_unknown_package_returns_404(client: TestClient):
    response = client.get("/plugins/package/does-not-exist")

    assert response.status_code == 404


def test_api_import_round_trips_packaged_plugin(client: TestClient, source_dir):
    package_response = client.post(
        "/plugins/package",
        json={
            "name": "csv-exporter",
            "version": "1.0.0",
            "entry_point": "csv_exporter_plugin.main",
            "source_dir": str(source_dir),
        },
    )
    archive_bytes = client.get("/plugins/package/csv-exporter").content

    response = client.post(
        "/plugins/import",
        files={"file": ("csv-exporter.zip", archive_bytes, "application/zip")},
    )

    assert response.status_code == 201
    assert response.json()["name"] == "csv-exporter"
    assert response.json()["checksum"] == package_response.json()["manifest"]["checksum"]


def test_api_import_tampered_package_returns_409(client: TestClient, source_dir):
    client.post(
        "/plugins/package",
        json={
            "name": "csv-exporter",
            "version": "1.0.0",
            "entry_point": "csv_exporter_plugin.main",
            "source_dir": str(source_dir),
        },
    )
    original_bytes = client.get("/plugins/package/csv-exporter").content
    with zipfile.ZipFile(io.BytesIO(original_bytes)) as original:
        manifest_bytes = original.read("manifest.json")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as tampered:
        tampered.writestr("manifest.json", manifest_bytes)
        tampered.writestr("src/main.py", "VALUE = 999\n")

    response = client.post(
        "/plugins/import",
        files={"file": ("csv-exporter.zip", buffer.getvalue(), "application/zip")},
    )

    assert response.status_code == 409


def test_api_import_non_archive_returns_422(client: TestClient):
    response = client.post(
        "/plugins/import",
        files={"file": ("not-a-zip.zip", b"just some bytes", "application/zip")},
    )

    assert response.status_code == 422
