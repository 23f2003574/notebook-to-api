from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException

from .data_validation import (
    DataValidationEngine,
    ValidationReport,
    ValidationRule,
    ValidationType,
    get_data_validation_engine,
)


class SchemaAlreadyExistsError(ValueError):
    pass


class UnknownSchemaError(KeyError):
    pass


class UnknownSchemaVersionError(KeyError):
    pass


class IncompatibleSchemaError(ValueError):
    pass


@dataclass(frozen=True)
class SchemaVersion:
    """A single point-in-time snapshot of a schema's fields."""

    version: int
    fields: tuple
    created_at: datetime
    compatible_with_previous: bool

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "fields": [dict(field) for field in self.fields],
            "created_at": self.created_at.isoformat(),
            "compatible_with_previous": self.compatible_with_previous,
        }


@dataclass(frozen=True)
class SchemaDefinition:
    """A named schema and its full version history."""

    name: str
    versions: tuple
    created_at: datetime

    @property
    def latest(self) -> SchemaVersion:
        return self.versions[-1]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "versions": [version.to_dict() for version in self.versions],
            "created_at": self.created_at.isoformat(),
        }


def _check_compatibility(old_fields: tuple, new_fields: tuple) -> list:
    old_by_name = {field["name"]: field for field in old_fields}
    new_by_name = {field["name"]: field for field in new_fields}
    violations = []
    for name, old_field in old_by_name.items():
        new_field = new_by_name.get(name)
        if new_field is None:
            violations.append(f"field '{name}' was removed")
            continue
        if new_field.get("type") != old_field.get("type"):
            violations.append(
                f"field '{name}' changed type from {old_field.get('type')} to {new_field.get('type')}"
            )
    for name, new_field in new_by_name.items():
        if name not in old_by_name and not new_field.get("nullable", True):
            violations.append(f"field '{name}' was added as required")
    return violations


def _build_rules(fields: tuple) -> list:
    rules = [ValidationRule(rule_type=ValidationType.SCHEMA, config={"expected_columns": [f["name"] for f in fields]})]
    for schema_field in fields:
        if not schema_field.get("nullable", True):
            rules.append(ValidationRule(rule_type=ValidationType.NULL_VALUES, column=schema_field["name"]))
        if schema_field.get("type"):
            rules.append(
                ValidationRule(
                    rule_type=ValidationType.DATA_TYPES,
                    column=schema_field["name"],
                    config={"expected_type": schema_field["type"]},
                )
            )
    return rules


class SchemaRegistry:
    """Tracks versioned dataset schemas and validates data against them."""

    def __init__(self) -> None:
        self._schemas: dict = {}
        self._lock = Lock()

    def register(self, name: str, fields: list) -> SchemaDefinition:
        if not name:
            raise ValueError("schema name is required")
        with self._lock:
            if name in self._schemas:
                raise SchemaAlreadyExistsError(name)
            version = SchemaVersion(
                version=1,
                fields=tuple(fields),
                created_at=datetime.now(timezone.utc),
                compatible_with_previous=True,
            )
            schema = SchemaDefinition(name=name, versions=(version,), created_at=version.created_at)
            self._schemas[name] = schema
            return schema

    def update(self, name: str, fields: list) -> SchemaVersion:
        with self._lock:
            schema = self._schemas.get(name)
            if schema is None:
                raise UnknownSchemaError(name)
            violations = _check_compatibility(schema.latest.fields, tuple(fields))
            if violations:
                raise IncompatibleSchemaError("; ".join(violations))
            version = SchemaVersion(
                version=schema.latest.version + 1,
                fields=tuple(fields),
                created_at=datetime.now(timezone.utc),
                compatible_with_previous=True,
            )
            updated = replace(schema, versions=schema.versions + (version,))
            self._schemas[name] = updated
            return version

    def get(self, name: str) -> SchemaDefinition:
        with self._lock:
            schema = self._schemas.get(name)
        if schema is None:
            raise UnknownSchemaError(name)
        return schema

    def list_schemas(self) -> list:
        with self._lock:
            return [self._schemas[name] for name in sorted(self._schemas)]

    def history(self, name: str) -> tuple:
        return self.get(name).versions

    def validate(
        self,
        name: str,
        rows: list,
        validation_engine: DataValidationEngine,
        *,
        version: Optional[int] = None,
    ) -> ValidationReport:
        schema = self.get(name)
        if version is None:
            schema_version = schema.latest
        else:
            matches = [v for v in schema.versions if v.version == version]
            if not matches:
                raise UnknownSchemaVersionError(f"{name}@{version}")
            schema_version = matches[0]
        rules = _build_rules(schema_version.fields)
        return validation_engine.validate(rows, rules, schema_name=name, schema_version=schema_version.version)


_schema_registry = SchemaRegistry()


def get_schema_registry() -> SchemaRegistry:
    return _schema_registry


router = APIRouter(prefix="/pipelines/schemas", tags=["pipeline-schemas"])


@router.post("", status_code=201)
def upsert_schema_endpoint(
    payload: dict = Body(default={}),
    registry: SchemaRegistry = Depends(get_schema_registry),
) -> dict:
    name = payload.get("name", "")
    fields = payload.get("fields", [])
    try:
        schema = registry.register(name, fields)
        return schema.to_dict()
    except SchemaAlreadyExistsError:
        pass
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    try:
        registry.update(name, fields)
    except IncompatibleSchemaError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return registry.get(name).to_dict()


@router.get("")
def list_schemas_endpoint(registry: SchemaRegistry = Depends(get_schema_registry)) -> list:
    return [schema.to_dict() for schema in registry.list_schemas()]


@router.get("/{schema}")
def get_schema_endpoint(schema: str, registry: SchemaRegistry = Depends(get_schema_registry)) -> dict:
    try:
        return registry.get(schema).to_dict()
    except UnknownSchemaError:
        raise HTTPException(status_code=404, detail="unknown schema")


@router.post("/{schema}/validate")
def validate_schema_endpoint(
    schema: str,
    payload: dict = Body(default={}),
    registry: SchemaRegistry = Depends(get_schema_registry),
    validation_engine: DataValidationEngine = Depends(get_data_validation_engine),
) -> dict:
    try:
        result = registry.validate(
            schema,
            payload.get("rows", []),
            validation_engine,
            version=payload.get("version"),
        )
    except UnknownSchemaError:
        raise HTTPException(status_code=404, detail="unknown schema")
    except UnknownSchemaVersionError:
        raise HTTPException(status_code=404, detail="unknown schema version")
    return result.to_dict()
