from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import StreamingResponse

from .inference_analytics import InferenceAnalyticsService, get_inference_analytics_service
from .model_loader import LoadedModel, ModelLoader, ModelNotLoadedError, get_model_loader
from .prompt_templates import PromptTemplateManager, get_prompt_template_manager


class InferenceState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class UnknownRequestError(KeyError):
    pass


class InvalidStateTransitionError(ValueError):
    pass


@dataclass(frozen=True)
class InferenceRequest:
    """A single request to run a loaded model against some input."""

    request_id: str
    model_name: str
    input: object
    mode: str
    submitted_at: datetime
    priority: int = 0


@dataclass(frozen=True)
class InferenceResult:
    """The lifecycle state and outcome of an inference request."""

    request: InferenceRequest
    state: InferenceState
    output: Optional[object]
    error: Optional[str]
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    duration_ms: Optional[float]

    def to_dict(self) -> dict:
        return {
            "request_id": self.request.request_id,
            "model_name": self.request.model_name,
            "mode": self.request.mode,
            "priority": self.request.priority,
            "state": self.state.value,
            "output": self.output,
            "error": self.error,
            "submitted_at": self.request.submitted_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_ms": self.duration_ms,
        }


class InferenceEngine:
    """Dispatches inference requests to loaded models and tracks their execution."""

    def __init__(self) -> None:
        self._results: dict = {}
        self._lock = Lock()

    @staticmethod
    def _dispatch(loaded: LoadedModel, item: object) -> dict:
        return {
            "model": loaded.name,
            "version": loaded.version,
            "entry_point": loaded.manifest.entry_point,
            "output": item,
        }

    @staticmethod
    def _chunk_input(input: object, chunk_size: int) -> list:
        if isinstance(input, str):
            words = input.split()
            chunks = [" ".join(words[i:i + chunk_size]) for i in range(0, len(words), chunk_size)]
            return chunks or [""]
        return [input]

    @staticmethod
    def _resolve_input(
        input: object,
        *,
        templates: Optional[PromptTemplateManager],
        template_name: Optional[str],
        template_values: Optional[dict],
    ) -> object:
        if template_name is None:
            return input
        if templates is None:
            raise ValueError("a template manager is required when template_name is provided")
        return templates.render(template_name, template_values)

    @staticmethod
    def _estimate_tokens(input: object) -> int:
        if isinstance(input, str):
            return len(input.split())
        if isinstance(input, list):
            return sum(InferenceEngine._estimate_tokens(item) for item in input)
        return 0

    def infer(
        self,
        model_name: str,
        input: object,
        *,
        loader: ModelLoader,
        mode: str = "sync",
        priority: int = 0,
        templates: Optional[PromptTemplateManager] = None,
        template_name: Optional[str] = None,
        template_values: Optional[dict] = None,
        analytics: Optional[InferenceAnalyticsService] = None,
    ) -> InferenceResult:
        if mode not in ("sync", "batch", "async"):
            raise ValueError(f"unsupported mode '{mode}'; use stream() for streaming inference")
        input = self._resolve_input(
            input, templates=templates, template_name=template_name, template_values=template_values
        )
        if mode == "batch" and not isinstance(input, list):
            raise ValueError("batch mode requires input to be a list")

        loaded = loader.get(model_name)

        request = InferenceRequest(
            request_id=uuid4().hex,
            model_name=model_name,
            input=input,
            mode=mode,
            submitted_at=datetime.now(timezone.utc),
            priority=priority,
        )
        queued = InferenceResult(
            request=request,
            state=InferenceState.QUEUED,
            output=None,
            error=None,
            started_at=None,
            finished_at=None,
            duration_ms=None,
        )
        with self._lock:
            self._results[request.request_id] = queued

        started_at = datetime.now(timezone.utc)
        running = replace(queued, state=InferenceState.RUNNING, started_at=started_at)
        with self._lock:
            self._results[request.request_id] = running

        # "async" mode is tracked like any other request but this in-memory engine has no
        # background worker, so it still executes inline before returning.
        if mode == "batch":
            output = [self._dispatch(loaded, item) for item in input]
        else:
            output = self._dispatch(loaded, input)

        finished_at = datetime.now(timezone.utc)
        duration_ms = (finished_at - started_at).total_seconds() * 1000
        finished = replace(
            running,
            state=InferenceState.SUCCEEDED,
            output=output,
            finished_at=finished_at,
            duration_ms=duration_ms,
        )
        with self._lock:
            self._results[request.request_id] = finished
        if analytics is not None:
            analytics.record(model_name, "success", duration_ms, self._estimate_tokens(input))
        return finished

    def stream(
        self,
        model_name: str,
        input: object,
        *,
        loader: ModelLoader,
        chunk_size: int = 1,
        templates: Optional[PromptTemplateManager] = None,
        template_name: Optional[str] = None,
        template_values: Optional[dict] = None,
    ):
        input = self._resolve_input(
            input, templates=templates, template_name=template_name, template_values=template_values
        )
        loaded = loader.get(model_name)

        request = InferenceRequest(
            request_id=uuid4().hex,
            model_name=model_name,
            input=input,
            mode="stream",
            submitted_at=datetime.now(timezone.utc),
        )
        started_at = request.submitted_at
        running = InferenceResult(
            request=request,
            state=InferenceState.RUNNING,
            output=None,
            error=None,
            started_at=started_at,
            finished_at=None,
            duration_ms=None,
        )
        with self._lock:
            self._results[request.request_id] = running

        chunks = self._chunk_input(input, chunk_size)

        def generator():
            collected = []
            for chunk in chunks:
                with self._lock:
                    current = self._results[request.request_id]
                    if current.state == InferenceState.CANCELLED:
                        return
                collected.append(chunk)
                yield chunk
            finished_at = datetime.now(timezone.utc)
            with self._lock:
                current = self._results[request.request_id]
                if current.state == InferenceState.CANCELLED:
                    return
                duration_ms = (finished_at - started_at).total_seconds() * 1000
                self._results[request.request_id] = replace(
                    current,
                    state=InferenceState.SUCCEEDED,
                    output=self._dispatch(loaded, collected),
                    finished_at=finished_at,
                    duration_ms=duration_ms,
                )

        return request.request_id, generator()

    def cancel(self, request_id: str) -> InferenceResult:
        with self._lock:
            result = self._results.get(request_id)
            if result is None:
                raise UnknownRequestError(request_id)
            if result.state not in (InferenceState.QUEUED, InferenceState.RUNNING):
                raise InvalidStateTransitionError(f"cannot cancel request in state '{result.state.value}'")
            cancelled = replace(result, state=InferenceState.CANCELLED, finished_at=datetime.now(timezone.utc))
            self._results[request_id] = cancelled
            return cancelled

    def status(self, request_id: str) -> InferenceResult:
        with self._lock:
            result = self._results.get(request_id)
        if result is None:
            raise UnknownRequestError(request_id)
        return result


_inference_engine = InferenceEngine()


def get_inference_engine() -> InferenceEngine:
    return _inference_engine


router = APIRouter(prefix="/ai/inference", tags=["inference"])


@router.post("")
def infer_endpoint(
    payload: dict = Body(default={}),
    engine: InferenceEngine = Depends(get_inference_engine),
    loader: ModelLoader = Depends(get_model_loader),
    templates: PromptTemplateManager = Depends(get_prompt_template_manager),
    analytics: InferenceAnalyticsService = Depends(get_inference_analytics_service),
) -> dict:
    try:
        result = engine.infer(
            payload.get("model_name", ""),
            payload.get("input"),
            loader=loader,
            mode=payload.get("mode", "sync"),
            priority=payload.get("priority", 0),
            templates=templates,
            template_name=payload.get("template_name"),
            template_values=payload.get("template_values"),
            analytics=analytics,
        )
    except ModelNotLoadedError:
        raise HTTPException(status_code=404, detail="model is not loaded")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return result.to_dict()


@router.post("/stream")
def stream_endpoint(
    payload: dict = Body(default={}),
    engine: InferenceEngine = Depends(get_inference_engine),
    loader: ModelLoader = Depends(get_model_loader),
    templates: PromptTemplateManager = Depends(get_prompt_template_manager),
) -> StreamingResponse:
    try:
        request_id, chunks = engine.stream(
            payload.get("model_name", ""),
            payload.get("input"),
            loader=loader,
            chunk_size=payload.get("chunk_size", 1),
            templates=templates,
            template_name=payload.get("template_name"),
            template_values=payload.get("template_values"),
        )
    except ModelNotLoadedError:
        raise HTTPException(status_code=404, detail="model is not loaded")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    def body():
        for chunk in chunks:
            yield f"{chunk}\n"

    return StreamingResponse(body(), media_type="text/plain", headers={"X-Request-Id": request_id})


@router.get("/{request}")
def get_inference_status_endpoint(
    request: str,
    engine: InferenceEngine = Depends(get_inference_engine),
) -> dict:
    try:
        return engine.status(request).to_dict()
    except UnknownRequestError:
        raise HTTPException(status_code=404, detail="unknown inference request")


@router.delete("/{request}")
def cancel_inference_endpoint(
    request: str,
    engine: InferenceEngine = Depends(get_inference_engine),
) -> dict:
    try:
        cancelled = engine.cancel(request)
    except UnknownRequestError:
        raise HTTPException(status_code=404, detail="unknown inference request")
    except InvalidStateTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return cancelled.to_dict()
