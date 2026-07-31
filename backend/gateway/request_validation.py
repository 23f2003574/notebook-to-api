from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException

_SCALAR_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "null": type(None),
}


class UnknownValidationRuleError(KeyError):
    pass


class ValidationRuleAlreadyRegisteredError(ValueError):
    pass


def _validate_value(value: Any, schema: dict, path: str = "") -> list:
    errors: list = []
    expected_type = schema.get("type")
    if expected_type is not None:
        if expected_type == "integer" and isinstance(value, bool):
            return [f"{path or 'body'}: expected type integer, got boolean"]
        py_type = _SCALAR_TYPES.get(expected_type)
        if py_type is not None and not isinstance(value, py_type):
            return [f"{path or 'body'}: expected type {expected_type}, got {type(value).__name__}"]

    if expected_type == "object" and isinstance(value, dict):
        for field_name in schema.get("required", []):
            if field_name not in value:
                errors.append(f"{path + '.' if path else ''}{field_name}: required field missing")
        for field_name, sub_schema in schema.get("properties", {}).items():
            if field_name in value:
                errors.extend(_validate_value(value[field_name], sub_schema, f"{path + '.' if path else ''}{field_name}"))

    if expected_type == "array" and isinstance(value, list):
        items_schema = schema.get("items")
        if items_schema:
            for index, item in enumerate(value):
                errors.extend(_validate_value(item, items_schema, f"{path}[{index}]"))

    return errors


@dataclass(frozen=True)
class ValidationRule:
    """Validation constraints applied to requests for a single route."""

    route: str
    required_headers: tuple = ()
    required_params: tuple = ()
    required_path_params: tuple = ()
    schema: Optional[dict] = None
    content_type: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "route": self.route,
            "required_headers": list(self.required_headers),
            "required_params": list(self.required_params),
            "required_path_params": list(self.required_path_params),
            "schema": self.schema,
            "content_type": self.content_type,
        }


@dataclass(frozen=True)
class ValidationResult:
    """The outcome of validating a single request."""

    route: Optional[str]
    valid: bool
    errors: tuple = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {"route": self.route, "valid": self.valid, "errors": list(self.errors)}


class RequestValidationEngine:
    """Validates incoming requests against registered per-route rules."""

    def __init__(self) -> None:
        self._rules: dict = {}
        self._lock = Lock()

    def register_rule(self, rule: ValidationRule) -> ValidationRule:
        with self._lock:
            if rule.route in self._rules:
                raise ValidationRuleAlreadyRegisteredError(f"{rule.route} already has a validation rule")
            self._rules[rule.route] = rule
        return rule

    def remove_rule(self, route: str) -> None:
        with self._lock:
            if route not in self._rules:
                raise UnknownValidationRuleError(route)
            del self._rules[route]

    def get_rule(self, route: str) -> ValidationRule:
        rule = self._rules.get(route)
        if rule is None:
            raise UnknownValidationRuleError(route)
        return rule

    def list_rules(self) -> list:
        with self._lock:
            return sorted(self._rules.values(), key=lambda rule: rule.route)

    def validate_headers(self, rule: ValidationRule, headers: Optional[dict] = None) -> list:
        headers = {key.lower(): value for key, value in (headers or {}).items()}
        errors = []
        for name in rule.required_headers:
            if name.lower() not in headers:
                errors.append(f"missing required header: {name}")
        if rule.content_type is not None:
            actual = headers.get("content-type")
            normalized = actual.split(";")[0].strip().lower() if actual else None
            if normalized != rule.content_type.lower():
                errors.append(f"invalid content type: expected {rule.content_type}, got {actual}")
        return errors

    def validate_params(
        self,
        rule: ValidationRule,
        params: Optional[dict] = None,
        path_params: Optional[dict] = None,
    ) -> list:
        params = params or {}
        path_params = path_params or {}
        errors = []
        for name in rule.required_params:
            if name not in params:
                errors.append(f"missing required query parameter: {name}")
        for name in rule.required_path_params:
            if name not in path_params:
                errors.append(f"missing required path parameter: {name}")
        return errors

    def validate_body(self, rule: ValidationRule, body: Any = None) -> list:
        if rule.schema is None:
            return []
        if body is None:
            return ["body is required"]
        return self.validate_schema(rule.schema, body)

    def validate_schema(self, schema: dict, value: Any) -> list:
        return _validate_value(value, schema, "")

    def validate_request(
        self,
        rule: ValidationRule,
        *,
        headers: Optional[dict] = None,
        params: Optional[dict] = None,
        path_params: Optional[dict] = None,
        body: Any = None,
    ) -> ValidationResult:
        errors: list = []
        errors.extend(self.validate_headers(rule, headers))
        errors.extend(self.validate_params(rule, params, path_params))
        errors.extend(self.validate_body(rule, body))
        return ValidationResult(route=rule.route, valid=not errors, errors=tuple(errors))


_validation_engine = RequestValidationEngine()


def get_validation_engine() -> RequestValidationEngine:
    return _validation_engine


router = APIRouter(prefix="/gateway", tags=["gateway-validation"])


@router.post("/validate")
def validate_request_endpoint(
    payload: dict = Body(default={}),
    engine: RequestValidationEngine = Depends(get_validation_engine),
) -> dict:
    route = payload.get("route", "")
    try:
        rule = engine.get_rule(route)
    except UnknownValidationRuleError:
        raise HTTPException(status_code=404, detail="no validation rule configured for route")
    result = engine.validate_request(
        rule,
        headers=payload.get("headers"),
        params=payload.get("params"),
        path_params=payload.get("path_params"),
        body=payload.get("body"),
    )
    return result.to_dict()


@router.post("/validate/schema")
def validate_schema_endpoint(
    payload: dict = Body(default={}),
    engine: RequestValidationEngine = Depends(get_validation_engine),
) -> dict:
    errors = engine.validate_schema(payload.get("schema", {}), payload.get("body"))
    result = ValidationResult(route=None, valid=not errors, errors=tuple(errors))
    return result.to_dict()


@router.get("/validation/rules")
def list_validation_rules_endpoint(
    engine: RequestValidationEngine = Depends(get_validation_engine),
) -> list:
    return [rule.to_dict() for rule in engine.list_rules()]
