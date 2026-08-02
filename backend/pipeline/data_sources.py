from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException


class SourceType(str, Enum):
    CSV = "csv"
    SQL = "sql"
    PARQUET = "parquet"
    REST_API = "rest_api"
    OBJECT_STORAGE = "object_storage"


class DataSourceAlreadyRegisteredError(ValueError):
    pass


class UnknownDataSourceError(KeyError):
    pass


class InvalidConnectionProfileError(ValueError):
    pass


@dataclass(frozen=True)
class ConnectionProfile:
    """Connection details for a data source, with credentials kept opaque."""

    source_type: SourceType
    uri: str
    credential_ref: str = ""
    options: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "source_type": self.source_type.value,
            "uri": self.uri,
            "credential_ref": self.credential_ref,
            "options": dict(self.options),
        }

    @classmethod
    def from_dict(cls, payload: Optional[dict]) -> "ConnectionProfile":
        payload = payload or {}
        return cls(
            source_type=SourceType(payload.get("source_type", SourceType.CSV.value)),
            uri=payload.get("uri", ""),
            credential_ref=payload.get("credential_ref", ""),
            options=dict(payload.get("options", {})),
        )


def _validate_connection(connection: ConnectionProfile) -> None:
    if not connection.uri:
        raise InvalidConnectionProfileError("connection uri is required")
    if connection.source_type == SourceType.REST_API and not connection.uri.startswith(
        ("http://", "https://")
    ):
        raise InvalidConnectionProfileError("rest_api sources require an http(s) uri")
    if connection.source_type in (SourceType.SQL, SourceType.OBJECT_STORAGE) and "://" not in connection.uri:
        raise InvalidConnectionProfileError(f"{connection.source_type.value} sources require a scheme in the uri")


@dataclass(frozen=True)
class DataSource:
    """A single registered data source and its current health state."""

    name: str
    connection: ConnectionProfile
    registered_at: datetime
    healthy: Optional[bool] = None
    last_checked_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "connection": self.connection.to_dict(),
            "registered_at": self.registered_at.isoformat(),
            "healthy": self.healthy,
            "last_checked_at": self.last_checked_at.isoformat() if self.last_checked_at else None,
        }


class DataSourceManager:
    """Registers pipeline data sources and tracks their connection health."""

    def __init__(self) -> None:
        self._sources: dict = {}
        self._lock = Lock()

    def register(self, name: str, connection: ConnectionProfile) -> DataSource:
        if not name:
            raise ValueError("source name is required")
        _validate_connection(connection)
        with self._lock:
            if name in self._sources:
                raise DataSourceAlreadyRegisteredError(name)
            source = DataSource(
                name=name,
                connection=connection,
                registered_at=datetime.now(timezone.utc),
            )
            self._sources[name] = source
            return source

    def connect(self, name: str) -> DataSource:
        with self._lock:
            source = self._sources.get(name)
            if source is None:
                raise UnknownDataSourceError(name)
            updated = replace(source, healthy=True, last_checked_at=datetime.now(timezone.utc))
            self._sources[name] = updated
            return updated

    def disconnect(self, name: str) -> DataSource:
        with self._lock:
            source = self._sources.get(name)
            if source is None:
                raise UnknownDataSourceError(name)
            updated = replace(source, healthy=False, last_checked_at=datetime.now(timezone.utc))
            self._sources[name] = updated
            return updated

    def get(self, name: str) -> DataSource:
        with self._lock:
            source = self._sources.get(name)
            if source is None:
                raise UnknownDataSourceError(name)
            return source

    def remove(self, name: str) -> None:
        with self._lock:
            if name not in self._sources:
                raise UnknownDataSourceError(name)
            del self._sources[name]

    def list_sources(self) -> list:
        with self._lock:
            return [self._sources[name] for name in sorted(self._sources)]


_data_source_manager = DataSourceManager()


def get_data_source_manager() -> DataSourceManager:
    return _data_source_manager


router = APIRouter(prefix="/pipelines/sources", tags=["pipeline-sources"])


@router.post("", status_code=201)
def register_source_endpoint(
    payload: dict = Body(default={}),
    manager: DataSourceManager = Depends(get_data_source_manager),
) -> dict:
    try:
        source = manager.register(
            payload.get("name", ""),
            ConnectionProfile.from_dict(payload.get("connection")),
        )
    except DataSourceAlreadyRegisteredError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except (ValueError, InvalidConnectionProfileError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return source.to_dict()


@router.get("")
def list_sources_endpoint(
    manager: DataSourceManager = Depends(get_data_source_manager),
) -> list:
    return [source.to_dict() for source in manager.list_sources()]


@router.get("/{source}")
def get_source_endpoint(
    source: str,
    manager: DataSourceManager = Depends(get_data_source_manager),
) -> dict:
    try:
        return manager.get(source).to_dict()
    except UnknownDataSourceError:
        raise HTTPException(status_code=404, detail="unknown data source")


@router.delete("/{source}", status_code=204)
def remove_source_endpoint(
    source: str,
    manager: DataSourceManager = Depends(get_data_source_manager),
) -> None:
    try:
        manager.remove(source)
    except UnknownDataSourceError:
        raise HTTPException(status_code=404, detail="unknown data source")
