from __future__ import annotations

from datetime import datetime, timezone
from types import MappingProxyType

import pytest

from backend.observability.deployment_governance_provider_registry import (
    GovernanceIntegrityProviderRegistry,
)
from backend.observability.deployment_governance_provider_responses import (
    GovernanceIntegrityProviderResponse,
    GovernanceIntegrityProviderResponseOutcome,
    GovernanceIntegrityProviderResponseService,
)

BASE_TIME = datetime(2026, 7, 15, 23, 0, 0, tzinfo=timezone.utc)


def _service(clock=lambda: BASE_TIME) -> GovernanceIntegrityProviderResponseService:
    return GovernanceIntegrityProviderResponseService(
        GovernanceIntegrityProviderRegistry(), clock=clock
    )


def _response(status_code: int) -> GovernanceIntegrityProviderResponse:
    return GovernanceIntegrityProviderResponse(
        status_code=status_code,
        headers={},
        body={},
        duration_ms=10,
    )


# --- Model: GovernanceIntegrityProviderResponse -------------------------


def test_response_mappings_are_immutable() -> None:
    response = _response(200)

    assert isinstance(response.headers, MappingProxyType)
    assert isinstance(response.body, MappingProxyType)

    with pytest.raises(TypeError):
        response.headers["X"] = "Y"


def test_response_rejects_negative_duration() -> None:
    with pytest.raises(ValueError):
        GovernanceIntegrityProviderResponse(
            status_code=200,
            headers={},
            body={},
            duration_ms=-1,
        )


def test_response_to_dict() -> None:
    response = GovernanceIntegrityProviderResponse(
        status_code=200,
        headers={"X": "Y"},
        body={"ok": True},
        duration_ms=5,
    )

    assert response.to_dict() == {
        "status_code": 200,
        "headers": {"X": "Y"},
        "body": {"ok": True},
        "duration_ms": 5,
    }


# --- Model: GovernanceIntegrityProviderResponseOutcome --------------------


def test_outcome_rejects_naive_completed_at() -> None:
    with pytest.raises(ValueError, match="completed_at must be timezone-aware"):
        GovernanceIntegrityProviderResponseOutcome(
            success=True,
            provider_status="success",
            retryable=False,
            message=None,
            completed_at=datetime(2026, 7, 15, 23, 0, 0),
        )


def test_outcome_rejects_success_with_message() -> None:
    with pytest.raises(ValueError, match="message must not be set"):
        GovernanceIntegrityProviderResponseOutcome(
            success=True,
            provider_status="success",
            retryable=False,
            message="boom",
            completed_at=BASE_TIME,
        )


def test_outcome_rejects_failure_without_message() -> None:
    with pytest.raises(ValueError, match="message must be set"):
        GovernanceIntegrityProviderResponseOutcome(
            success=False,
            provider_status="client_error",
            retryable=False,
            message=None,
            completed_at=BASE_TIME,
        )


def test_outcome_to_dict() -> None:
    outcome = GovernanceIntegrityProviderResponseOutcome(
        success=False,
        provider_status="server_error",
        retryable=True,
        message="boom",
        completed_at=BASE_TIME,
    )

    assert outcome.to_dict() == {
        "success": False,
        "provider_status": "server_error",
        "retryable": True,
        "message": "boom",
        "completed_at": BASE_TIME.isoformat(),
    }


# --- Service: process ------------------------------------------------


@pytest.mark.parametrize("status_code", [200, 201, 204, 299])
def test_process_success_status_codes(status_code) -> None:
    outcome = _service().process(_response(status_code))

    assert outcome.success is True
    assert outcome.retryable is False
    assert outcome.message is None


@pytest.mark.parametrize("status_code", [400, 404, 422, 499])
def test_process_client_error_status_codes_are_not_retryable(status_code) -> None:
    outcome = _service().process(_response(status_code))

    assert outcome.success is False
    assert outcome.retryable is False
    assert outcome.message is not None


@pytest.mark.parametrize("status_code", [500, 502, 503, 599])
def test_process_server_error_status_codes_are_retryable(status_code) -> None:
    outcome = _service().process(_response(status_code))

    assert outcome.success is False
    assert outcome.retryable is True
    assert outcome.message is not None


@pytest.mark.parametrize("status_code", [0, 99, 100, 300, 399, 600, 999])
def test_process_raises_for_unsupported_status_code(status_code) -> None:
    with pytest.raises(ValueError):
        _service().process(_response(status_code))


def test_process_uses_injected_clock_for_completed_at() -> None:
    fixed_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    service = _service(clock=lambda: fixed_time)

    outcome = service.process(_response(200))

    assert outcome.completed_at == fixed_time
