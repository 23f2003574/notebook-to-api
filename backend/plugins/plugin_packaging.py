from __future__ import annotations

import hashlib
import io
import json
import tarfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, Response, UploadFile

from .plugin_loader import ManifestValidationError, PluginManifest

PACKAGE_FORMATS = ("zip", "tar.gz")


class UnsupportedPackageFormatError(ValueError):
    pass


class ChecksumMismatchError(ValueError):
    pass


class UnknownPackageError(KeyError):
    pass


def _compute_checksum(file_contents: dict) -> str:
    """A deterministic sha256 over a set of {relative_path: bytes} file contents."""
    hasher = hashlib.sha256()
    for rel_path in sorted(file_contents):
        hasher.update(rel_path.encode("utf-8"))
        hasher.update(file_contents[rel_path])
    return hasher.hexdigest()


@dataclass(frozen=True)
class PackageManifest:
    """The manifest embedded in a generated plugin package, including its integrity checksum."""

    name: str
    version: str
    entry_point: str
    description: str = ""
    author: str = ""
    tags: tuple = ()
    checksum: str = ""
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "entry_point": self.entry_point,
            "description": self.description,
            "author": self.author,
            "tags": list(self.tags),
            "checksum": self.checksum,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "PackageManifest":
        # Reuses PluginManifest's field validation (name/version/entry_point format)
        # rather than duplicating the same checks.
        validated = PluginManifest(
            name=payload.get("name", ""),
            version=payload.get("version", ""),
            entry_point=payload.get("entry_point", ""),
        )
        created_at = payload.get("created_at")
        return cls(
            name=validated.name,
            version=validated.version,
            entry_point=validated.entry_point,
            description=payload.get("description", ""),
            author=payload.get("author", ""),
            tags=tuple(payload.get("tags", ())),
            checksum=payload.get("checksum", ""),
            created_at=datetime.fromisoformat(created_at) if created_at else None,
        )


@dataclass(frozen=True)
class PluginPackage:
    """A generated, downloadable plugin archive."""

    manifest: PackageManifest
    format: str
    data: bytes

    def to_dict(self) -> dict:
        return {
            "manifest": self.manifest.to_dict(),
            "format": self.format,
            "size_bytes": len(self.data),
        }


class PluginPackagingService:
    """Builds, verifies, and reads distributable plugin archives (ZIP / TAR.GZ)."""

    def __init__(self) -> None:
        self._packages: dict = {}
        self._lock = Lock()

    def package(self, manifest: PluginManifest, source_dir: str, *, format: str = "zip") -> PluginPackage:
        if format not in PACKAGE_FORMATS:
            raise UnsupportedPackageFormatError(f"unsupported package format '{format}'")
        source_path = Path(source_dir)
        if not source_path.is_dir():
            raise FileNotFoundError(f"source directory '{source_dir}' does not exist")

        file_contents = {}
        for file_path in sorted(source_path.rglob("*")):
            if file_path.is_file():
                rel_path = file_path.relative_to(source_path).as_posix()
                file_contents[rel_path] = file_path.read_bytes()

        package_manifest = PackageManifest(
            name=manifest.name,
            version=manifest.version,
            entry_point=manifest.entry_point,
            description=manifest.description,
            author=manifest.author,
            tags=manifest.tags,
            checksum=_compute_checksum(file_contents),
            created_at=datetime.now(timezone.utc),
        )

        data = self._build_archive(package_manifest, file_contents, format)
        package = PluginPackage(manifest=package_manifest, format=format, data=data)
        with self._lock:
            self._packages[manifest.name] = package
        return package

    @staticmethod
    def _build_archive(manifest: PackageManifest, file_contents: dict, format: str) -> bytes:
        manifest_bytes = json.dumps(manifest.to_dict(), indent=2).encode("utf-8")
        buffer = io.BytesIO()
        if format == "zip":
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("manifest.json", manifest_bytes)
                for rel_path, content in file_contents.items():
                    archive.writestr(f"src/{rel_path}", content)
        else:
            with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
                for arcname, content in [("manifest.json", manifest_bytes)] + [
                    (f"src/{rel_path}", content) for rel_path, content in file_contents.items()
                ]:
                    info = tarfile.TarInfo(arcname)
                    info.size = len(content)
                    archive.addfile(info, io.BytesIO(content))
        return buffer.getvalue()

    @staticmethod
    def _extract_files(data: bytes, format: str) -> dict:
        if format not in PACKAGE_FORMATS:
            raise UnsupportedPackageFormatError(f"unsupported package format '{format}'")
        buffer = io.BytesIO(data)
        files = {}
        try:
            if format == "zip":
                with zipfile.ZipFile(buffer) as archive:
                    for name in archive.namelist():
                        if not name.endswith("/"):
                            files[name] = archive.read(name)
            else:
                with tarfile.open(fileobj=buffer, mode="r:gz") as archive:
                    for member in archive.getmembers():
                        if member.isfile():
                            extracted = archive.extractfile(member)
                            files[member.name] = extracted.read() if extracted else b""
        except (zipfile.BadZipFile, tarfile.TarError, EOFError) as exc:
            raise ManifestValidationError(f"not a valid '{format}' package: {exc}") from exc
        return files

    def export(self, name: str, *, format: Optional[str] = None):
        with self._lock:
            package = self._packages.get(name)
        if package is None:
            raise UnknownPackageError(name)
        if format is None or format == package.format:
            return package
        if format == "manifest-json":
            return json.dumps(package.manifest.to_dict(), indent=2)
        raise UnsupportedPackageFormatError(
            f"cannot export an existing '{package.format}' package as '{format}'; re-package with that format instead"
        )

    def verify(self, package: PluginPackage) -> bool:
        extracted = self._extract_files(package.data, package.format)
        if "manifest.json" not in extracted:
            raise ManifestValidationError("package is missing manifest.json")
        src_files = {name[4:]: content for name, content in extracted.items() if name.startswith("src/")}
        recomputed = _compute_checksum(src_files)
        if recomputed != package.manifest.checksum:
            raise ChecksumMismatchError(
                f"checksum mismatch for '{package.manifest.name}': "
                f"expected '{package.manifest.checksum}', got '{recomputed}'"
            )
        return True

    def import_package(self, data: bytes, format: str) -> PackageManifest:
        extracted = self._extract_files(data, format)
        manifest_bytes = extracted.get("manifest.json")
        if manifest_bytes is None:
            raise ManifestValidationError("package is missing manifest.json")
        try:
            payload = json.loads(manifest_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ManifestValidationError(f"manifest.json is not valid JSON: {exc}") from exc
        manifest = PackageManifest.from_dict(payload)

        src_files = {name[4:]: content for name, content in extracted.items() if name.startswith("src/")}
        recomputed = _compute_checksum(src_files)
        if recomputed != manifest.checksum:
            raise ChecksumMismatchError(
                f"checksum mismatch for '{manifest.name}': expected '{manifest.checksum}', got '{recomputed}'"
            )
        return manifest


_plugin_packaging_service = PluginPackagingService()


def get_plugin_packaging_service() -> PluginPackagingService:
    return _plugin_packaging_service


router = APIRouter(prefix="/plugins", tags=["plugins-packaging"])


@router.post("/package", status_code=201)
def package_plugin_endpoint(
    payload: dict = Body(default={}),
    service: PluginPackagingService = Depends(get_plugin_packaging_service),
) -> dict:
    try:
        manifest = PluginManifest.from_dict(payload)
    except ManifestValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    source_dir = payload.get("source_dir", "")
    if not source_dir:
        raise HTTPException(status_code=422, detail="source_dir is required")
    try:
        package = service.package(manifest, source_dir, format=payload.get("format", "zip"))
    except UnsupportedPackageFormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return package.to_dict()


@router.get("/package/{plugin}")
def download_package_endpoint(
    plugin: str,
    format: Optional[str] = Query(default=None),
    service: PluginPackagingService = Depends(get_plugin_packaging_service),
):
    try:
        result = service.export(plugin, format=format)
    except UnknownPackageError:
        raise HTTPException(status_code=404, detail="no package found for plugin")
    except UnsupportedPackageFormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if isinstance(result, str):
        return Response(content=result, media_type="application/json")
    media_type = "application/zip" if result.format == "zip" else "application/gzip"
    return Response(
        content=result.data,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{plugin}.{result.format}"'},
    )


@router.post("/import", status_code=201)
async def import_package_endpoint(
    file: UploadFile = File(...),
    format: str = Query(default="zip"),
    service: PluginPackagingService = Depends(get_plugin_packaging_service),
) -> dict:
    data = await file.read()
    try:
        manifest = service.import_package(data, format)
    except UnsupportedPackageFormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ManifestValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ChecksumMismatchError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return manifest.to_dict()
