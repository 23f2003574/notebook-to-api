from __future__ import annotations

import os
from dataclasses import dataclass, replace
from threading import Lock
from typing import Mapping

DEFAULT_COLLECTION_INTERVAL_SECONDS: int = 60

DEFAULT_MAX_HISTORY_ENTRIES: int = 500

DEFAULT_MAX_HISTORY_AGE_DAYS: int = 30

DEFAULT_AUTO_FLUSH: bool = True

_ENV_COLLECTION_INTERVAL_SECONDS = (
    "NOTEBOOK2API_GOVERNANCE_METRICS_COLLECTION_INTERVAL_SECONDS"
)

_ENV_MAX_HISTORY_ENTRIES = (
    "NOTEBOOK2API_GOVERNANCE_METRICS_MAX_HISTORY_ENTRIES"
)

_ENV_MAX_HISTORY_AGE_DAYS = (
    "NOTEBOOK2API_GOVERNANCE_METRICS_MAX_HISTORY_AGE_DAYS"
)

_ENV_AUTO_FLUSH = "NOTEBOOK2API_GOVERNANCE_METRICS_AUTO_FLUSH"


@dataclass(frozen=True)
class GovernanceIntegrityMetricsConfig:
    """
    Immutable, validated configuration for the governance metrics
    subsystem: how often the background collector runs, how much
    history is retained, and whether metrics auto-flush to durable
    storage after every update.
    """

    collection_interval_seconds: int = (
        DEFAULT_COLLECTION_INTERVAL_SECONDS
    )

    max_history_entries: int = DEFAULT_MAX_HISTORY_ENTRIES

    max_history_age_days: int = DEFAULT_MAX_HISTORY_AGE_DAYS

    auto_flush: bool = DEFAULT_AUTO_FLUSH

    def __post_init__(self) -> None:
        if self.collection_interval_seconds <= 0:
            raise ValueError(
                "collection_interval_seconds must be greater than "
                "zero"
            )

        if self.max_history_entries < 0:
            raise ValueError(
                "max_history_entries must not be negative"
            )

        if self.max_history_age_days <= 0:
            raise ValueError(
                "max_history_age_days must be greater than zero"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "collection_interval_seconds": (
                self.collection_interval_seconds
            ),
            "max_history_entries": self.max_history_entries,
            "max_history_age_days": self.max_history_age_days,
            "auto_flush": self.auto_flush,
        }

    @classmethod
    def from_env(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> "GovernanceIntegrityMetricsConfig":
        """
        Build configuration from environment variables, falling back
        to sensible defaults for anything unset.

        Supported variables:

        NOTEBOOK2API_GOVERNANCE_METRICS_COLLECTION_INTERVAL_SECONDS
        NOTEBOOK2API_GOVERNANCE_METRICS_MAX_HISTORY_ENTRIES
        NOTEBOOK2API_GOVERNANCE_METRICS_MAX_HISTORY_AGE_DAYS
        NOTEBOOK2API_GOVERNANCE_METRICS_AUTO_FLUSH
        """

        if environ is None:
            environ = os.environ

        return cls(
            collection_interval_seconds=int(
                environ.get(
                    _ENV_COLLECTION_INTERVAL_SECONDS,
                    str(DEFAULT_COLLECTION_INTERVAL_SECONDS),
                )
            ),
            max_history_entries=int(
                environ.get(
                    _ENV_MAX_HISTORY_ENTRIES,
                    str(DEFAULT_MAX_HISTORY_ENTRIES),
                )
            ),
            max_history_age_days=int(
                environ.get(
                    _ENV_MAX_HISTORY_AGE_DAYS,
                    str(DEFAULT_MAX_HISTORY_AGE_DAYS),
                )
            ),
            auto_flush=_parse_boolean_environment_value(
                environ.get(
                    _ENV_AUTO_FLUSH,
                    "true" if DEFAULT_AUTO_FLUSH else "false",
                ),
                variable_name=_ENV_AUTO_FLUSH,
            ),
        )


class GovernanceIntegrityMetricsConfigService:
    """
    Holds the current governance metrics configuration, sourced from
    environment variables, and lets it be replaced at runtime
    without restarting the process.

    Every config the service ever holds is immutable and validated;
    only the reference this service holds is ever swapped.
    """

    def __init__(
        self,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._environ = environ

        self._lock = Lock()

        self._config = GovernanceIntegrityMetricsConfig.from_env(
            environ=environ
        )

    def load(self) -> GovernanceIntegrityMetricsConfig:
        """
        Return the currently held configuration.
        """

        with self._lock:
            return self._config

    def reload(self) -> GovernanceIntegrityMetricsConfig:
        """
        Re-read configuration from the environment and replace the
        currently held configuration with it.
        """

        config = GovernanceIntegrityMetricsConfig.from_env(
            environ=self._environ
        )

        with self._lock:
            self._config = config

        return config

    def update(
        self,
        **overrides: object,
    ) -> GovernanceIntegrityMetricsConfig:
        """
        Apply field overrides on top of the currently held
        configuration and replace it with the validated result.
        """

        config = self.validate(**overrides)

        with self._lock:
            self._config = config

        return config

    def validate(
        self,
        **overrides: object,
    ) -> GovernanceIntegrityMetricsConfig:
        """
        Build and return a candidate configuration with overrides
        applied on top of the currently held one, without replacing
        it. Raises ValueError if the result would be invalid.
        """

        with self._lock:
            base = self._config

        return replace(base, **overrides)


def _parse_boolean_environment_value(
    value: str,
    *,
    variable_name: str,
) -> bool:
    normalized = value.strip().lower()

    if normalized in {"1", "true", "yes", "on"}:
        return True

    if normalized in {"0", "false", "no", "off"}:
        return False

    raise ValueError(
        f"{variable_name} must be a boolean value "
        "(1/0, true/false, yes/no, on/off); "
        f"got '{value}'"
    )
