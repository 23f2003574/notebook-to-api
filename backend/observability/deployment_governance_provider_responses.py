from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Callable, Mapping, TYPE_CHECKING

if TYPE_CHECKING:
    from .deployment_governance_provider_registry import (
        GovernanceIntegrityProviderRegistry,
    )


@dataclass(frozen=True)
class GovernanceIntegrityProviderResponse:
    """
    A provider's raw response to one delivery attempt, before
    normalization.
    """

    status_code: int

    headers: Mapping[str, str]

    body: Mapping[str, Any]

    duration_ms: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "headers", MappingProxyType(dict(self.headers))
        )

        object.__setattr__(
            self, "body", MappingProxyType(dict(self.body))
        )

        if self.duration_ms < 0:
            raise ValueError(
                "duration_ms must be greater than or equal to zero"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "status_code": self.status_code,
            "headers": dict(self.headers),
            "body": dict(self.body),
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True)
class GovernanceIntegrityProviderResponseOutcome:
    """
    A provider response normalized into a common delivery outcome,
    independent of which provider produced it.
    """

    success: bool

    provider_status: str

    retryable: bool

    message: str | None

    completed_at: datetime

    def __post_init__(self) -> None:
        if self.completed_at.tzinfo is None:
            raise ValueError(
                "completed_at must be timezone-aware"
            )

        if self.success:
            if self.message is not None:
                raise ValueError(
                    "message must not be set when success is True"
                )

        else:
            if self.message is None:
                raise ValueError(
                    "message must be set when success is False"
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "success": self.success,
            "provider_status": self.provider_status,
            "retryable": self.retryable,
            "message": self.message,
            "completed_at": self.completed_at.isoformat(),
        }


class GovernanceIntegrityProviderResponseService:
    """
    Normalizes a provider's raw response into a common delivery
    outcome, so the delivery engine and audit history never interpret
    provider-specific response shapes directly.
    """

    def __init__(
        self,
        registry: "GovernanceIntegrityProviderRegistry",
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._registry = registry

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

    def process(
        self,
        response: GovernanceIntegrityProviderResponse,
    ) -> GovernanceIntegrityProviderResponseOutcome:
        """
        Normalize one provider response into a delivery outcome.

        2xx status codes succeed. 4xx status codes fail without being
        retryable (the request itself was rejected). 5xx status codes
        fail and are retryable (a transient provider failure). Any
        other status code raises ValueError.
        """

        status_code = response.status_code

        if 200 <= status_code <= 299:
            return GovernanceIntegrityProviderResponseOutcome(
                success=True,
                provider_status="success",
                retryable=False,
                message=None,
                completed_at=self._clock(),
            )

        if 400 <= status_code <= 499:
            return GovernanceIntegrityProviderResponseOutcome(
                success=False,
                provider_status="client_error",
                retryable=False,
                message=(
                    "provider returned client error status "
                    f"{status_code}"
                ),
                completed_at=self._clock(),
            )

        if 500 <= status_code <= 599:
            return GovernanceIntegrityProviderResponseOutcome(
                success=False,
                provider_status="server_error",
                retryable=True,
                message=(
                    "provider returned server error status "
                    f"{status_code}"
                ),
                completed_at=self._clock(),
            )

        raise ValueError(
            f"unsupported provider response status code '{status_code}'"
        )
