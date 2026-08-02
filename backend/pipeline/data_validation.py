from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from time import perf_counter
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException


class ValidationType(str, Enum):
    SCHEMA = "schema"
    NULL_VALUES = "null_values"
    DATA_TYPES = "data_types"
    RANGE = "range"
    UNIQUENESS = "uniqueness"


_TYPE_MAP = {"str": str, "int": int, "float": (int, float), "bool": bool}


class UnsupportedValidationTypeError(ValueError):
    pass


class InvalidValidationRuleError(ValueError):
    pass


class UnknownValidationReportError(KeyError):
    pass


class ValidationFailedError(ValueError):
    pass


@dataclass(frozen=True)
class ValidationRule:
    """A single check to run against a dataset."""

    rule_type: ValidationType
    column: str = ""
    config: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"rule_type": self.rule_type.value, "column": self.column, "config": dict(self.config)}

    @classmethod
    def from_dict(cls, payload: Optional[dict]) -> "ValidationRule":
        payload = payload or {}
        if "rule_type" not in payload:
            raise InvalidValidationRuleError("rule_type is required")
        return cls(
            rule_type=ValidationType(payload["rule_type"]),
            column=payload.get("column", ""),
            config=dict(payload.get("config", {})),
        )


@dataclass(frozen=True)
class ValidationReport:
    """The aggregated outcome of running a set of validation rules."""

    report_id: str
    rule_count: int
    row_count: int
    passed: bool
    issues: tuple
    executed_at: datetime
    duration_ms: float
    schema_name: Optional[str] = None
    schema_version: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "report_id": self.report_id,
            "rule_count": self.rule_count,
            "row_count": self.row_count,
            "passed": self.passed,
            "issues": [dict(issue) for issue in self.issues],
            "executed_at": self.executed_at.isoformat(),
            "duration_ms": self.duration_ms,
            "schema_name": self.schema_name,
            "schema_version": self.schema_version,
        }


class DataValidationEngine:
    """Runs rule-based schema and quality checks against datasets."""

    def __init__(self) -> None:
        self._reports: dict = {}
        self._lock = Lock()

    def check_schema(self, rows: list, expected_columns: list) -> list:
        actual = set(rows[0].keys()) if rows else set()
        missing = sorted(set(expected_columns) - actual)
        return [
            {"rule_type": ValidationType.SCHEMA.value, "column": column, "message": f"missing column '{column}'"}
            for column in missing
        ]

    def _check_null_values(self, rows: list, column: str) -> list:
        return [
            {
                "rule_type": ValidationType.NULL_VALUES.value,
                "column": column,
                "row_index": index,
                "message": f"null value in column '{column}'",
            }
            for index, row in enumerate(rows)
            if row.get(column) is None
        ]

    def _check_data_type(self, rows: list, column: str, expected_type: str) -> list:
        python_type = _TYPE_MAP.get(expected_type, str)
        issues = []
        for index, row in enumerate(rows):
            value = row.get(column)
            if value is not None and not isinstance(value, python_type):
                issues.append(
                    {
                        "rule_type": ValidationType.DATA_TYPES.value,
                        "column": column,
                        "row_index": index,
                        "message": f"expected {expected_type} in column '{column}'",
                    }
                )
        return issues

    def _check_range(self, rows: list, column: str, minimum, maximum) -> list:
        issues = []
        for index, row in enumerate(rows):
            value = row.get(column)
            if value is None:
                continue
            if minimum is not None and value < minimum:
                issues.append(
                    {
                        "rule_type": ValidationType.RANGE.value,
                        "column": column,
                        "row_index": index,
                        "message": f"{value} below minimum {minimum}",
                    }
                )
            elif maximum is not None and value > maximum:
                issues.append(
                    {
                        "rule_type": ValidationType.RANGE.value,
                        "column": column,
                        "row_index": index,
                        "message": f"{value} above maximum {maximum}",
                    }
                )
        return issues

    def _check_uniqueness(self, rows: list, column: str) -> list:
        seen: set = set()
        issues = []
        for index, row in enumerate(rows):
            value = row.get(column)
            if value in seen:
                issues.append(
                    {
                        "rule_type": ValidationType.UNIQUENESS.value,
                        "column": column,
                        "row_index": index,
                        "message": f"duplicate value '{value}' in column '{column}'",
                    }
                )
            else:
                seen.add(value)
        return issues

    def check_quality(self, rows: list, rules: list) -> list:
        issues = []
        for rule in rules:
            if rule.rule_type == ValidationType.SCHEMA:
                issues.extend(self.check_schema(rows, rule.config.get("expected_columns", [])))
            elif rule.rule_type == ValidationType.NULL_VALUES:
                issues.extend(self._check_null_values(rows, rule.column))
            elif rule.rule_type == ValidationType.DATA_TYPES:
                issues.extend(self._check_data_type(rows, rule.column, rule.config.get("expected_type", "str")))
            elif rule.rule_type == ValidationType.RANGE:
                issues.extend(self._check_range(rows, rule.column, rule.config.get("minimum"), rule.config.get("maximum")))
            elif rule.rule_type == ValidationType.UNIQUENESS:
                issues.extend(self._check_uniqueness(rows, rule.column))
            else:
                raise UnsupportedValidationTypeError(rule.rule_type)
        return issues

    def validate(
        self,
        rows: list,
        rules: list,
        *,
        schema_name: Optional[str] = None,
        schema_version: Optional[int] = None,
    ) -> ValidationReport:
        start = perf_counter()
        issues = self.check_quality(rows, rules)
        duration_ms = (perf_counter() - start) * 1000
        result = ValidationReport(
            report_id=uuid4().hex,
            rule_count=len(rules),
            row_count=len(rows),
            passed=len(issues) == 0,
            issues=tuple(issues),
            executed_at=datetime.now(timezone.utc),
            duration_ms=duration_ms,
            schema_name=schema_name,
            schema_version=schema_version,
        )
        with self._lock:
            self._reports[result.report_id] = result
        return result

    def report(self, report_id: str) -> ValidationReport:
        with self._lock:
            result = self._reports.get(report_id)
        if result is None:
            raise UnknownValidationReportError(report_id)
        return result


_data_validation_engine = DataValidationEngine()


def get_data_validation_engine() -> DataValidationEngine:
    return _data_validation_engine


router = APIRouter(tags=["pipeline-validation"])


def _parse_rules(payload: dict) -> list:
    try:
        return [ValidationRule.from_dict(rule) for rule in payload.get("rules", [])]
    except (ValueError, InvalidValidationRuleError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/pipelines/validate")
def validate_endpoint(
    payload: dict = Body(default={}),
    engine: DataValidationEngine = Depends(get_data_validation_engine),
) -> dict:
    rules = _parse_rules(payload)
    try:
        result = engine.validate(payload.get("rows", []), rules)
    except UnsupportedValidationTypeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return result.to_dict()


@router.post("/pipelines/validate/schema")
def validate_schema_endpoint(
    payload: dict = Body(default={}),
    engine: DataValidationEngine = Depends(get_data_validation_engine),
) -> dict:
    issues = engine.check_schema(payload.get("rows", []), payload.get("expected_columns", []))
    return {"passed": len(issues) == 0, "issues": issues}


@router.get("/pipelines/validation/{report}")
def get_validation_report_endpoint(
    report: str,
    engine: DataValidationEngine = Depends(get_data_validation_engine),
) -> dict:
    try:
        return engine.report(report).to_dict()
    except UnknownValidationReportError:
        raise HTTPException(status_code=404, detail="unknown validation report")
