from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from .model_registry import ModelRegistry, UnknownModelError, get_model_registry

_DEFAULT_METRICS = ("latency", "throughput", "accuracy", "memory", "cost")
_HIGHER_IS_BETTER = {"throughput_rps", "accuracy"}


class UnknownSuiteError(KeyError):
    pass


class UnknownBenchmarkError(KeyError):
    pass


@dataclass(frozen=True)
class BenchmarkSuite:
    """A standardized workload definition that models are benchmarked against."""

    name: str
    workload: str
    metrics: tuple
    created_at: datetime

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "workload": self.workload,
            "metrics": list(self.metrics),
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class BenchmarkResult:
    """The measured outcome of running a model against a benchmark suite."""

    benchmark_id: str
    suite_name: str
    model_name: str
    metrics: dict
    ran_at: datetime

    def to_dict(self) -> dict:
        return {
            "benchmark_id": self.benchmark_id,
            "suite_name": self.suite_name,
            "model_name": self.model_name,
            "metrics": self.metrics,
            "ran_at": self.ran_at.isoformat(),
        }


class ModelBenchmarkService:
    """Runs standardized benchmarks against registered models and ranks the results."""

    def __init__(self) -> None:
        self._suites: dict = {}
        self._results: dict = {}
        self._history: dict = {}
        self._lock = Lock()

    def run(
        self,
        suite_name: str,
        model_name: str,
        *,
        registry: ModelRegistry,
        workload: str = "default",
        metrics: Optional[list] = None,
    ) -> BenchmarkResult:
        if not suite_name:
            raise ValueError("suite_name is required")
        if not model_name:
            raise ValueError("model_name is required")

        model = registry.get(model_name)

        with self._lock:
            if suite_name not in self._suites:
                self._suites[suite_name] = BenchmarkSuite(
                    name=suite_name,
                    workload=workload,
                    metrics=tuple(metrics) if metrics else _DEFAULT_METRICS,
                    created_at=datetime.now(timezone.utc),
                )

        # No real model runtime backs this service, so metrics are derived deterministically
        # from the model's declared metadata rather than measured from a live execution.
        latency_ms = model.metadata.latency_ms if model.metadata.latency_ms > 0 else 100.0
        result_metrics = {
            "latency_ms": latency_ms,
            "throughput_rps": round(1000.0 / latency_ms, 4),
            "accuracy": round(min(1.0, 0.5 + model.metadata.weight / 10.0), 4),
            "memory_mb": 256.0 + len(model.metadata.capabilities) * 64.0,
            "cost_usd": round(latency_ms * 0.0001, 6),
        }

        result = BenchmarkResult(
            benchmark_id=uuid4().hex,
            suite_name=suite_name,
            model_name=model_name,
            metrics=result_metrics,
            ran_at=datetime.now(timezone.utc),
        )
        with self._lock:
            self._results[result.benchmark_id] = result
            self._history.setdefault((suite_name, model_name), []).append(result)
        return result

    def compare(
        self,
        suite_name: str,
        model_names: list,
        *,
        registry: ModelRegistry,
    ) -> list:
        if not model_names:
            raise ValueError("compare requires at least one model")
        return [self.run(suite_name, name, registry=registry) for name in model_names]

    def leaderboard(
        self,
        suite_name: str,
        *,
        metric: str = "latency_ms",
        ascending: Optional[bool] = None,
    ) -> list:
        with self._lock:
            latest_by_model = {
                name: results[-1]
                for (suite, name), results in self._history.items()
                if suite == suite_name
            }
        if not latest_by_model:
            raise UnknownSuiteError(suite_name)

        if ascending is None:
            ascending = metric not in _HIGHER_IS_BETTER
        return sorted(
            latest_by_model.values(),
            key=lambda result: result.metrics.get(metric, 0),
            reverse=not ascending,
        )

    def history(self, suite_name: str, model_name: Optional[str] = None) -> list:
        with self._lock:
            if model_name is not None:
                records = self._history.get((suite_name, model_name))
                if records is None:
                    raise UnknownBenchmarkError(f"{suite_name}/{model_name}")
                return list(records)
            records = [
                result
                for (suite, _name), results in self._history.items()
                if suite == suite_name
                for result in results
            ]
        if not records:
            raise UnknownSuiteError(suite_name)
        return sorted(records, key=lambda result: result.ran_at)

    def get_result(self, benchmark_id: str) -> BenchmarkResult:
        with self._lock:
            result = self._results.get(benchmark_id)
        if result is None:
            raise UnknownBenchmarkError(benchmark_id)
        return result

    def list_results(self) -> list:
        with self._lock:
            return sorted(self._results.values(), key=lambda result: result.ran_at)


_model_benchmark_service = ModelBenchmarkService()


def get_model_benchmark_service() -> ModelBenchmarkService:
    return _model_benchmark_service


router = APIRouter(prefix="/ai/benchmarks", tags=["model-benchmarks"])


@router.post("", status_code=201)
def run_benchmark_endpoint(
    payload: dict = Body(default={}),
    service: ModelBenchmarkService = Depends(get_model_benchmark_service),
    registry: ModelRegistry = Depends(get_model_registry),
):
    suite_name = payload.get("suite_name", "")
    try:
        if "model_names" in payload:
            results = service.compare(suite_name, payload.get("model_names", []), registry=registry)
            return [result.to_dict() for result in results]
        result = service.run(
            suite_name,
            payload.get("model_name", ""),
            registry=registry,
            workload=payload.get("workload", "default"),
            metrics=payload.get("metrics"),
        )
        return result.to_dict()
    except UnknownModelError:
        raise HTTPException(status_code=404, detail="unknown model")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/leaderboard")
def leaderboard_endpoint(
    suite: str = Query(...),
    metric: str = Query(default="latency_ms"),
    ascending: Optional[bool] = Query(default=None),
    service: ModelBenchmarkService = Depends(get_model_benchmark_service),
) -> list:
    try:
        ranked = service.leaderboard(suite, metric=metric, ascending=ascending)
    except UnknownSuiteError:
        raise HTTPException(status_code=404, detail="unknown benchmark suite")
    return [result.to_dict() for result in ranked]


@router.get("")
def list_benchmarks_endpoint(
    service: ModelBenchmarkService = Depends(get_model_benchmark_service),
) -> list:
    return [result.to_dict() for result in service.list_results()]


@router.get("/{benchmark}")
def get_benchmark_endpoint(
    benchmark: str,
    service: ModelBenchmarkService = Depends(get_model_benchmark_service),
) -> dict:
    try:
        return service.get_result(benchmark).to_dict()
    except UnknownBenchmarkError:
        raise HTTPException(status_code=404, detail="unknown benchmark")
