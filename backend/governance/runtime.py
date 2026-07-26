from __future__ import annotations

from typing import Callable

_SUBSYSTEM_BOOTSTRAPS: "dict[str, Callable[[], object]]" = {}
_HEALTH_CHECKS: "dict[str, Callable[[], dict]]" = {}


def register_subsystem(name: str, bootstrap: "Callable[[], object]") -> None:
    """Register a subsystem's bootstrap callable to run during governance runtime startup."""

    _SUBSYSTEM_BOOTSTRAPS[name] = bootstrap


def register_health_check(name: str, check: "Callable[[], dict]") -> None:
    """Register a subsystem's health check callable."""

    _HEALTH_CHECKS[name] = check


def registered_subsystems() -> tuple:
    return tuple(_SUBSYSTEM_BOOTSTRAPS)


def registered_health_checks() -> tuple:
    return tuple(_HEALTH_CHECKS)


def run_subsystem_validation(name: str) -> object:
    """Run one specific registered subsystem's bootstrap callable by name."""

    if name not in _SUBSYSTEM_BOOTSTRAPS:
        raise KeyError(name)
    return _SUBSYSTEM_BOOTSTRAPS[name]()


def run_health_check(name: str) -> dict:
    """Run one specific registered subsystem's health check callable by name."""

    if name not in _HEALTH_CHECKS:
        raise KeyError(name)
    return _HEALTH_CHECKS[name]()


def run_startup_validation() -> dict:
    """
    Run every registered subsystem's bootstrap callable in
    registration order.

    Each callable is expected to raise on failure, aborting startup
    rather than letting the governance runtime start in a partially
    wired state.
    """

    return {name: bootstrap() for name, bootstrap in _SUBSYSTEM_BOOTSTRAPS.items()}


def run_health_checks() -> dict:
    """Run every registered subsystem health check, returning its report."""

    return {name: check() for name, check in _HEALTH_CHECKS.items()}


def reset() -> None:
    """Clear all registrations. Intended for test isolation only."""

    _SUBSYSTEM_BOOTSTRAPS.clear()
    _HEALTH_CHECKS.clear()
