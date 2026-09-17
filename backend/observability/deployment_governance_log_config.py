from __future__ import annotations

import os
from dataclasses import dataclass, replace
from threading import Lock
from typing import Mapping

_VALID_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")

DEFAULT_MINIMUM_LEVEL: str = "DEBUG"

DEFAULT_BATCH_SIZE: int = 100

DEFAULT_FLUSH_INTERVAL_SECONDS: int = 5

DEFAULT_ENABLE_SAMPLING: bool = True

DEFAULT_ENABLE_REDACTION: bool = True

_ENV_MINIMUM_LEVEL = "NOTEBOOK2API_GOVERNANCE_LOG_MINIMUM_LEVEL"

_ENV_BATCH_SIZE = "NOTEBOOK2API_GOVERNANCE_LOG_BATCH_SIZE"

_ENV_FLUSH_INTERVAL_SECONDS = (
    "NOTEBOOK2API_GOVERNANCE_LOG_FLUSH_INTERVAL_SECONDS"
)

_ENV_ENABLE_SAMPLING = (
    "NOTEBOOK2API_GOVERNANCE_LOG_ENABLE_SAMPLING"
)

_ENV_ENABLE_REDACTION = (
    "NOTEBOOK2API_GOVERNANCE_LOG_ENABLE_REDACTION"
)


@dataclass(frozen=True)
class GovernanceLogConfig:
    """
    Immutable, validated configuration for the governance logging
    subsystem as a whole: the minimum level worth logging at all,
    how batching is sized, and whether sampling and redaction are
    active.
    """

    minimum_level: str = DEFAULT_MINIMUM_LEVEL

    batch_size: int = DEFAULT_BATCH_SIZE

    flush_interval_seconds: int = DEFAULT_FLUSH_INTERVAL_SECONDS

    enable_sampling: bool = DEFAULT_ENABLE_SAMPLING

    enable_redaction: bool = DEFAULT_ENABLE_REDACTION

    def __post_init__(self) -> None:
        if self.minimum_level not in _VALID_LEVELS:
            raise ValueError(
                f"minimum_level must be one of {', '.join(_VALID_LEVELS)}"
            )

        if self.batch_size <= 0:
            raise ValueError(
                "batch_size must be greater than zero"
            )

        if self.flush_interval_seconds <= 0:
            raise ValueError(
                "flush_interval_seconds must be greater than zero"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "minimum_level": self.minimum_level,
            "batch_size": self.batch_size,
            "flush_interval_seconds": self.flush_interval_seconds,
            "enable_sampling": self.enable_sampling,
            "enable_redaction": self.enable_redaction,
        }

    @classmethod
    def from_env(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> "GovernanceLogConfig":
        """
        Build configuration from environment variables, falling back
        to sensible defaults for anything unset.

        Supported variables:

        NOTEBOOK2API_GOVERNANCE_LOG_MINIMUM_LEVEL
        NOTEBOOK2API_GOVERNANCE_LOG_BATCH_SIZE
        NOTEBOOK2API_GOVERNANCE_LOG_FLUSH_INTERVAL_SECONDS
        NOTEBOOK2API_GOVERNANCE_LOG_ENABLE_SAMPLING
        NOTEBOOK2API_GOVERNANCE_LOG_ENABLE_REDACTION
        """

        if environ is None:
            environ = os.environ

        return cls(
            minimum_level=environ.get(
                _ENV_MINIMUM_LEVEL, DEFAULT_MINIMUM_LEVEL
            ).upper(),
            batch_size=int(
                environ.get(
                    _ENV_BATCH_SIZE, str(DEFAULT_BATCH_SIZE)
                )
            ),
            flush_interval_seconds=int(
                environ.get(
                    _ENV_FLUSH_INTERVAL_SECONDS,
                    str(DEFAULT_FLUSH_INTERVAL_SECONDS),
                )
            ),
            enable_sampling=_parse_boolean_environment_value(
                environ.get(
                    _ENV_ENABLE_SAMPLING,
                    "true" if DEFAULT_ENABLE_SAMPLING else "false",
                ),
                variable_name=_ENV_ENABLE_SAMPLING,
            ),
            enable_redaction=_parse_boolean_environment_value(
                environ.get(
                    _ENV_ENABLE_REDACTION,
                    "true" if DEFAULT_ENABLE_REDACTION else "false",
                ),
                variable_name=_ENV_ENABLE_REDACTION,
            ),
        )


class GovernanceLogConfigService:
    """
    Holds the current governance logging configuration, sourced from
    environment variables, and lets it be replaced at runtime
    without restarting the process.

    Every config the service ever holds is immutable and validated;
    only the reference this service holds is ever swapped. This
    already gives GovernanceLoggingBootstrap (see
    deployment_governance_logging_bootstrap.py) its "fail fast on
    invalid configuration" property for free: constructing this
    service (and every reload()/update() call) validates through
    GovernanceLogConfig.__post_init__ immediately, so the bootstrap
    never has to separately re-check a config it has already loaded.
    """

    def __init__(
        self,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._environ = environ

        self._lock = Lock()

        self._config = GovernanceLogConfig.from_env(environ=environ)

    def load(self) -> GovernanceLogConfig:
        """
        Return the currently held configuration.
        """

        with self._lock:
            return self._config

    def reload(self) -> GovernanceLogConfig:
        """
        Re-read configuration from the environment and replace the
        currently held configuration with it.
        """

        config = GovernanceLogConfig.from_env(environ=self._environ)

        with self._lock:
            self._config = config

        return config

    def update(
        self,
        **overrides: object,
    ) -> GovernanceLogConfig:
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
    ) -> GovernanceLogConfig:
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
