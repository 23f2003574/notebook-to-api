from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException

from .inference_engine import InferenceEngine, get_inference_engine
from .model_loader import ModelLoader, get_model_loader


class BatchState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class UnknownBatchError(KeyError):
    pass


class InvalidBatchStateError(ValueError):
    pass


@dataclass(frozen=True)
class BatchRequest:
    """A submission of multiple inputs to run against a single model."""

    batch_id: str
    model_name: str
    items: tuple
    mode: str
    priorities: tuple
    batch_size: int
    submitted_at: datetime


@dataclass(frozen=True)
class BatchItemResult:
    """The outcome of a single item within a batch."""

    index: int
    request_id: Optional[str]
    state: str
    output: Optional[object]
    error: Optional[str]

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "request_id": self.request_id,
            "state": self.state,
            "output": self.output,
            "error": self.error,
        }


@dataclass(frozen=True)
class BatchResult:
    """The lifecycle state and per-item outcomes of a submitted batch."""

    request: BatchRequest
    state: BatchState
    items: tuple
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    duration_ms: Optional[float]

    def to_summary_dict(self) -> dict:
        succeeded = sum(1 for item in self.items if item.state == "succeeded")
        failed = sum(1 for item in self.items if item.state == "failed")
        return {
            "batch_id": self.request.batch_id,
            "model_name": self.request.model_name,
            "mode": self.request.mode,
            "batch_size": self.request.batch_size,
            "state": self.state.value,
            "total_items": len(self.request.items),
            "completed_items": len(self.items),
            "succeeded_items": succeeded,
            "failed_items": failed,
            "submitted_at": self.request.submitted_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_ms": self.duration_ms,
        }

    def to_dict(self) -> dict:
        payload = self.to_summary_dict()
        payload["items"] = [item.to_dict() for item in self.items]
        return payload


class BatchInferenceEngine:
    """Submits and runs batches of inference requests, tracking progress and partial failures."""

    def __init__(self) -> None:
        self._batches: dict = {}
        self._lock = Lock()

    def submit(
        self,
        model_name: str,
        items: list,
        *,
        mode: str = "sequential",
        priorities: Optional[list] = None,
        batch_size: int = 4,
    ) -> BatchResult:
        if mode not in ("sequential", "parallel", "async", "priority"):
            raise ValueError(f"unsupported batch mode '{mode}'")
        if not items:
            raise ValueError("batch requires at least one item")
        priorities = tuple(priorities) if priorities is not None else tuple(0 for _ in items)
        if len(priorities) != len(items):
            raise ValueError("priorities length must match items length")

        request = BatchRequest(
            batch_id=uuid4().hex,
            model_name=model_name,
            items=tuple(items),
            mode=mode,
            priorities=priorities,
            batch_size=batch_size,
            submitted_at=datetime.now(timezone.utc),
        )
        result = BatchResult(
            request=request,
            state=BatchState.QUEUED,
            items=(),
            started_at=None,
            finished_at=None,
            duration_ms=None,
        )
        with self._lock:
            self._batches[request.batch_id] = result
        return result

    def _run_item(
        self,
        request: BatchRequest,
        index: int,
        *,
        engine: InferenceEngine,
        loader: ModelLoader,
    ) -> BatchItemResult:
        try:
            inference_result = engine.infer(
                request.model_name,
                request.items[index],
                loader=loader,
                priority=request.priorities[index],
            )
            return BatchItemResult(
                index=index,
                request_id=inference_result.request.request_id,
                state="succeeded",
                output=inference_result.output,
                error=None,
            )
        except Exception as exc:
            # A single bad item (unloaded model, bad input) must not sink the whole batch.
            return BatchItemResult(index=index, request_id=None, state="failed", output=None, error=str(exc))

    def execute(
        self,
        batch_id: str,
        *,
        engine: InferenceEngine,
        loader: ModelLoader,
    ) -> BatchResult:
        with self._lock:
            batch = self._batches.get(batch_id)
            if batch is None:
                raise UnknownBatchError(batch_id)
            if batch.state != BatchState.QUEUED:
                raise InvalidBatchStateError(f"cannot execute batch in state '{batch.state.value}'")
            started_at = datetime.now(timezone.utc)
            running = replace(batch, state=BatchState.RUNNING, started_at=started_at)
            self._batches[batch_id] = running

        request = running.request
        order = list(range(len(request.items)))
        if request.mode == "priority":
            order.sort(key=lambda index: request.priorities[index], reverse=True)

        completed: list = []

        def run_and_record(index: int) -> BatchItemResult:
            item_result = self._run_item(request, index, engine=engine, loader=loader)
            with self._lock:
                completed.append(item_result)
                current = self._batches[batch_id]
                self._batches[batch_id] = replace(current, items=tuple(completed))
            return item_result

        if request.mode == "parallel":
            with ThreadPoolExecutor(max_workers=max(1, request.batch_size)) as pool:
                futures = [pool.submit(run_and_record, index) for index in order]
                for future in as_completed(futures):
                    future.result()
        else:
            for index in order:
                run_and_record(index)

        with self._lock:
            current = self._batches[batch_id]
            succeeded = sum(1 for item in current.items if item.state == "succeeded")
            failed = len(current.items) - succeeded
            if failed == 0:
                final_state = BatchState.SUCCEEDED
            elif succeeded == 0:
                final_state = BatchState.FAILED
            else:
                final_state = BatchState.PARTIAL
            finished_at = datetime.now(timezone.utc)
            duration_ms = (finished_at - started_at).total_seconds() * 1000
            finished = replace(
                current, state=final_state, finished_at=finished_at, duration_ms=duration_ms
            )
            self._batches[batch_id] = finished
        return finished

    def cancel(self, batch_id: str) -> BatchResult:
        with self._lock:
            batch = self._batches.get(batch_id)
            if batch is None:
                raise UnknownBatchError(batch_id)
            if batch.state != BatchState.QUEUED:
                raise InvalidBatchStateError(f"cannot cancel batch in state '{batch.state.value}'")
            cancelled = replace(batch, state=BatchState.CANCELLED, finished_at=datetime.now(timezone.utc))
            self._batches[batch_id] = cancelled
            return cancelled

    def results(self, batch_id: str) -> BatchResult:
        with self._lock:
            batch = self._batches.get(batch_id)
        if batch is None:
            raise UnknownBatchError(batch_id)
        return batch


_batch_inference_engine = BatchInferenceEngine()


def get_batch_inference_engine() -> BatchInferenceEngine:
    return _batch_inference_engine


router = APIRouter(prefix="/ai/batch", tags=["batch-inference"])


@router.post("", status_code=201)
def submit_batch_endpoint(
    payload: dict = Body(default={}),
    batch_engine: BatchInferenceEngine = Depends(get_batch_inference_engine),
    inference_engine: InferenceEngine = Depends(get_inference_engine),
    loader: ModelLoader = Depends(get_model_loader),
) -> dict:
    try:
        batch = batch_engine.submit(
            payload.get("model_name", ""),
            payload.get("items", []),
            mode=payload.get("mode", "sequential"),
            priorities=payload.get("priorities"),
            batch_size=payload.get("batch_size", 4),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    finished = batch_engine.execute(batch.request.batch_id, engine=inference_engine, loader=loader)
    return finished.to_dict()


@router.get("/{batch}")
def get_batch_endpoint(
    batch: str,
    batch_engine: BatchInferenceEngine = Depends(get_batch_inference_engine),
) -> dict:
    try:
        return batch_engine.results(batch).to_summary_dict()
    except UnknownBatchError:
        raise HTTPException(status_code=404, detail="unknown batch")


@router.get("/{batch}/results")
def get_batch_results_endpoint(
    batch: str,
    batch_engine: BatchInferenceEngine = Depends(get_batch_inference_engine),
) -> list:
    try:
        result = batch_engine.results(batch)
    except UnknownBatchError:
        raise HTTPException(status_code=404, detail="unknown batch")
    return [item.to_dict() for item in result.items]


@router.delete("/{batch}")
def cancel_batch_endpoint(
    batch: str,
    batch_engine: BatchInferenceEngine = Depends(get_batch_inference_engine),
) -> dict:
    try:
        cancelled = batch_engine.cancel(batch)
    except UnknownBatchError:
        raise HTTPException(status_code=404, detail="unknown batch")
    except InvalidBatchStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return cancelled.to_summary_dict()
