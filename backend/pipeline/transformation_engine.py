from __future__ import annotations

import operator as operator_module
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from time import perf_counter
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException

from .data_sources import DataSourceManager, UnknownDataSourceError, get_data_source_manager


class OperationType(str, Enum):
    MAP = "map"
    FILTER = "filter"
    JOIN = "join"
    AGGREGATE = "aggregate"
    SORT = "sort"


class UnknownColumnError(KeyError):
    pass


class UnsupportedOperationError(ValueError):
    pass


class InvalidTransformationStepError(ValueError):
    pass


_FILTER_OPERATORS = {
    "eq": operator_module.eq,
    "ne": operator_module.ne,
    "gt": operator_module.gt,
    "gte": operator_module.ge,
    "lt": operator_module.lt,
    "lte": operator_module.le,
    "contains": lambda field_value, target: target in field_value,
}

_AGGREGATE_FUNCS = {"sum", "avg", "min", "max", "count"}


@dataclass(frozen=True)
class TransformationStep:
    """A single operation applied while transforming a dataset."""

    operation: OperationType
    config: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"operation": self.operation.value, "config": dict(self.config)}

    @classmethod
    def from_dict(cls, payload: Optional[dict]) -> "TransformationStep":
        payload = payload or {}
        if "operation" not in payload:
            raise InvalidTransformationStepError("operation is required")
        return cls(
            operation=OperationType(payload["operation"]),
            config=dict(payload.get("config", {})),
        )


@dataclass(frozen=True)
class TransformationResult:
    """The outcome of running a chain of transformation steps."""

    rows: tuple
    row_count: int
    duration_ms: float
    executed_at: datetime
    steps: tuple

    def to_dict(self) -> dict:
        return {
            "rows": [dict(row) for row in self.rows],
            "row_count": self.row_count,
            "duration_ms": self.duration_ms,
            "executed_at": self.executed_at.isoformat(),
            "steps": [step.to_dict() for step in self.steps],
        }


class DataTransformationEngine:
    """Applies chained map/filter/join/aggregate/sort operations to datasets."""

    def __init__(self) -> None:
        self._history: list = []
        self._lock = Lock()

    def map_columns(self, rows: list, mapping: dict) -> list:
        result = []
        for row in rows:
            new_row = {}
            for target, source in mapping.items():
                if source not in row:
                    raise UnknownColumnError(source)
                new_row[target] = row[source]
            result.append(new_row)
        return result

    def filter_rows(self, rows: list, column: str, op: str, value) -> list:
        if op not in _FILTER_OPERATORS:
            raise UnsupportedOperationError(op)
        predicate = _FILTER_OPERATORS[op]
        return [row for row in rows if column in row and predicate(row[column], value)]

    def aggregate(self, rows: list, group_by: list, column: str, func: str) -> list:
        if func not in _AGGREGATE_FUNCS:
            raise UnsupportedOperationError(func)
        groups: dict = {}
        for row in rows:
            key = tuple(row.get(field_name) for field_name in group_by)
            groups.setdefault(key, []).append(row)
        result = []
        for key, group_rows in groups.items():
            values = [row[column] for row in group_rows if column in row]
            if func == "count":
                agg_value = len(group_rows)
            elif not values:
                agg_value = None
            elif func == "sum":
                agg_value = sum(values)
            elif func == "avg":
                agg_value = sum(values) / len(values)
            elif func == "min":
                agg_value = min(values)
            else:
                agg_value = max(values)
            entry = dict(zip(group_by, key))
            entry[f"{func}_{column}"] = agg_value
            result.append(entry)
        return result

    def sort_rows(self, rows: list, column: str, descending: bool = False) -> list:
        return sorted(rows, key=lambda row: row.get(column), reverse=descending)

    def join_rows(self, left_rows: list, right_rows: list, left_key: str, right_key: str) -> list:
        index: dict = {}
        for row in right_rows:
            index.setdefault(row.get(right_key), []).append(row)
        result = []
        for row in left_rows:
            for match in index.get(row.get(left_key), []):
                merged = dict(row)
                for key, value in match.items():
                    if key != right_key:
                        merged[key] = value
                result.append(merged)
        return result

    def _apply_step(self, rows: list, step: TransformationStep) -> list:
        config = step.config
        if step.operation == OperationType.MAP:
            return self.map_columns(rows, config.get("mapping", {}))
        if step.operation == OperationType.FILTER:
            return self.filter_rows(rows, config["column"], config.get("operator", "eq"), config.get("value"))
        if step.operation == OperationType.AGGREGATE:
            return self.aggregate(rows, config.get("group_by", []), config["column"], config.get("func", "sum"))
        if step.operation == OperationType.SORT:
            return self.sort_rows(rows, config["column"], config.get("descending", False))
        if step.operation == OperationType.JOIN:
            return self.join_rows(
                rows,
                config.get("right_rows", []),
                config["left_key"],
                config.get("right_key", config["left_key"]),
            )
        raise UnsupportedOperationError(step.operation)

    def _run(self, rows: list, steps: list) -> tuple:
        start = perf_counter()
        current = list(rows)
        for step in steps:
            current = self._apply_step(current, step)
        duration_ms = (perf_counter() - start) * 1000
        return current, duration_ms

    def transform(
        self,
        rows: list,
        steps: list,
        *,
        sources: Optional[DataSourceManager] = None,
        source_name: Optional[str] = None,
    ) -> TransformationResult:
        if sources is not None and source_name is not None:
            sources.mark_read(source_name)
        current, duration_ms = self._run(rows, steps)
        result = TransformationResult(
            rows=tuple(current),
            row_count=len(current),
            duration_ms=duration_ms,
            executed_at=datetime.now(timezone.utc),
            steps=tuple(steps),
        )
        with self._lock:
            self._history.append(result)
        return result

    def preview(self, rows: list, steps: list, *, limit: int = 50) -> TransformationResult:
        current, duration_ms = self._run(rows, steps)
        return TransformationResult(
            rows=tuple(current[:limit]),
            row_count=len(current),
            duration_ms=duration_ms,
            executed_at=datetime.now(timezone.utc),
            steps=tuple(steps),
        )

    def history(self, limit: Optional[int] = None) -> list:
        with self._lock:
            items = list(reversed(self._history))
        if limit is not None:
            items = items[:limit]
        return items


_data_transformation_engine = DataTransformationEngine()


def get_data_transformation_engine() -> DataTransformationEngine:
    return _data_transformation_engine


router = APIRouter(prefix="/pipelines/transform", tags=["pipeline-transform"])


def _parse_steps(payload: dict) -> list:
    try:
        return [TransformationStep.from_dict(step) for step in payload.get("steps", [])]
    except (ValueError, InvalidTransformationStepError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("")
def transform_endpoint(
    payload: dict = Body(default={}),
    engine: DataTransformationEngine = Depends(get_data_transformation_engine),
    sources: DataSourceManager = Depends(get_data_source_manager),
) -> dict:
    steps = _parse_steps(payload)
    try:
        result = engine.transform(
            payload.get("rows", []),
            steps,
            sources=sources,
            source_name=payload.get("source_name"),
        )
    except UnknownDataSourceError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except (UnknownColumnError, UnsupportedOperationError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return result.to_dict()


@router.post("/preview")
def preview_endpoint(
    payload: dict = Body(default={}),
    engine: DataTransformationEngine = Depends(get_data_transformation_engine),
) -> dict:
    steps = _parse_steps(payload)
    try:
        result = engine.preview(
            payload.get("rows", []),
            steps,
            limit=payload.get("limit", 50),
        )
    except (UnknownColumnError, UnsupportedOperationError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return result.to_dict()


@router.get("/history")
def history_endpoint(
    engine: DataTransformationEngine = Depends(get_data_transformation_engine),
) -> list:
    return [result.to_dict() for result in engine.history()]
