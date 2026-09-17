from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException

_TYPE_CHECKS = {
    "string": lambda value: isinstance(value, str),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "boolean": lambda value: isinstance(value, bool),
    "array": lambda value: isinstance(value, list),
    "object": lambda value: isinstance(value, dict),
}


class ConfigValidationError(ValueError):
    pass


class UnknownPluginConfigError(KeyError):
    pass


@dataclass(frozen=True)
class ConfigField:
    """A single field declared by a plugin's configuration schema."""

    name: str
    type: str
    required: bool = False
    default: Any = None

    def __post_init__(self) -> None:
        if self.type not in _TYPE_CHECKS:
            raise ValueError(f"unsupported config field type '{self.type}'")

    def to_dict(self) -> dict:
        return {"name": self.name, "type": self.type, "required": self.required, "default": self.default}

    @classmethod
    def from_dict(cls, payload: dict) -> "ConfigField":
        return cls(
            name=payload["name"],
            type=payload["type"],
            required=payload.get("required", False),
            default=payload.get("default"),
        )


@dataclass(frozen=True)
class ConfigSchema:
    """A plugin's declared configuration shape."""

    plugin: str
    fields: tuple

    def to_dict(self) -> dict:
        return {"plugin": self.plugin, "fields": [field.to_dict() for field in self.fields]}


@dataclass(frozen=True)
class PluginConfig:
    """A concrete, versioned set of configuration values for a plugin."""

    plugin: str
    values: dict
    version: int
    updated_at: datetime

    def to_dict(self) -> dict:
        return {
            "plugin": self.plugin,
            "values": dict(self.values),
            "version": self.version,
            "updated_at": self.updated_at.isoformat(),
        }


class PluginConfigurationManager:
    """Stores, validates, and versions per-plugin configuration."""

    def __init__(self) -> None:
        self._schemas: dict = {}
        self._configs: dict = {}
        self._history: dict = {}
        self._lock = Lock()

    def register_schema(self, plugin: str, fields) -> ConfigSchema:
        schema = ConfigSchema(plugin=plugin, fields=tuple(fields))
        with self._lock:
            self._schemas[plugin] = schema
        return schema

    def get_schema(self, plugin: str) -> Optional[ConfigSchema]:
        with self._lock:
            return self._schemas.get(plugin)

    def validate(self, plugin: str, values: dict) -> dict:
        """Validate `values` against the plugin's schema, filling in defaults.

        Returns the normalized values. A plugin with no registered schema
        accepts any JSON-serializable dict unvalidated.
        """
        with self._lock:
            schema = self._schemas.get(plugin)
        if schema is None:
            return dict(values)

        known_names = {field.name for field in schema.fields}
        unknown = set(values) - known_names
        if unknown:
            raise ConfigValidationError(f"unknown config field(s) for '{plugin}': {sorted(unknown)}")

        normalized = {}
        for field in schema.fields:
            if field.name in values:
                value = values[field.name]
                if not _TYPE_CHECKS[field.type](value):
                    raise ConfigValidationError(
                        f"field '{field.name}' must be of type '{field.type}'"
                    )
                normalized[field.name] = value
            elif field.default is not None:
                normalized[field.name] = field.default
            elif field.required:
                raise ConfigValidationError(
                    f"missing required config field '{field.name}' for '{plugin}'"
                )
        return normalized

    def save(self, plugin: str, values: dict, *, lifecycle=None) -> PluginConfig:
        normalized = self.validate(plugin, values)
        with self._lock:
            history = self._history.setdefault(plugin, [])
            config = PluginConfig(
                plugin=plugin,
                values=normalized,
                version=len(history) + 1,
                updated_at=datetime.now(timezone.utc),
            )
            history.append(config)
            self._configs[plugin] = config
        if lifecycle is not None:
            self._hot_reload_if_enabled(plugin, lifecycle)
        return config

    def _hot_reload_if_enabled(self, plugin: str, lifecycle) -> None:
        from .plugin_lifecycle import InvalidTransitionError
        from .plugin_registry import UnknownPluginError

        try:
            lifecycle.reload(plugin)
        except (UnknownPluginError, InvalidTransitionError):
            pass

    def load(self, plugin: str) -> PluginConfig:
        with self._lock:
            config = self._configs.get(plugin)
            schema = self._schemas.get(plugin)
        if config is not None:
            return config
        if schema is not None:
            missing_required = [
                field.name for field in schema.fields if field.required and field.default is None
            ]
            if missing_required:
                raise ConfigValidationError(
                    f"missing required config field(s) for '{plugin}': {missing_required}"
                )
            defaults = {field.name: field.default for field in schema.fields if field.default is not None}
            return PluginConfig(plugin=plugin, values=defaults, version=0, updated_at=datetime.now(timezone.utc))
        raise UnknownPluginConfigError(plugin)

    def get_history(self, plugin: str) -> list:
        with self._lock:
            return list(self._history.get(plugin, ()))


_plugin_configuration_manager = PluginConfigurationManager()


def get_plugin_configuration_manager() -> PluginConfigurationManager:
    return _plugin_configuration_manager


def _get_plugin_lifecycle_manager_dependency():
    from .plugin_lifecycle import get_plugin_lifecycle_manager

    return get_plugin_lifecycle_manager()


router = APIRouter(prefix="/plugins", tags=["plugins-config"])


@router.get("/{plugin}/config")
def get_config_endpoint(
    plugin: str,
    manager: PluginConfigurationManager = Depends(get_plugin_configuration_manager),
) -> dict:
    try:
        config = manager.load(plugin)
    except UnknownPluginConfigError:
        raise HTTPException(status_code=404, detail="no configuration found for plugin")
    except ConfigValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return config.to_dict()


@router.put("/{plugin}/config")
def put_config_endpoint(
    plugin: str,
    payload: dict = Body(default={}),
    manager: PluginConfigurationManager = Depends(get_plugin_configuration_manager),
    lifecycle=Depends(_get_plugin_lifecycle_manager_dependency),
) -> dict:
    try:
        config = manager.save(plugin, payload, lifecycle=lifecycle)
    except ConfigValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return config.to_dict()


@router.post("/{plugin}/config/validate")
def validate_config_endpoint(
    plugin: str,
    payload: dict = Body(default={}),
    manager: PluginConfigurationManager = Depends(get_plugin_configuration_manager),
) -> dict:
    try:
        normalized = manager.validate(plugin, payload)
    except ConfigValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"valid": True, "values": normalized}
