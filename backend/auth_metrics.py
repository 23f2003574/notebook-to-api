"""Failed-authentication counters and security log events for the management dashboard.

The dashboard's middleware records here; GET /api/metrics/prometheus exposes the totals.
Only counts, a client address and a reason are kept -- never the key that was tried.
"""
import json
import logging
import threading

_LOCK = threading.Lock()
_TOTALS = {"failures": 0, "throttled": 0, "lockouts": 0, "denied": 0}


def reset():
    with _LOCK:
        for key in _TOTALS:
            _TOTALS[key] = 0


def totals():
    with _LOCK:
        return dict(_TOTALS)


def _log(event, client, **fields):
    logging.getLogger("uvicorn.error").warning(
        "security_event %s", json.dumps({"event": event, "service": "dashboard", "client": client, **fields}, sort_keys=True)
    )


def record_failure(client, reason, window_count, limit):
    """One rejected key. `window_count` is this client's failures in the current window (0 when the
    throttle is off) and `limit` the configured maximum; logs the first failure of a window and the
    moment the client is locked out."""
    with _LOCK:
        _TOTALS["failures"] += 1
        if limit > 0 and window_count == limit:
            _TOTALS["lockouts"] += 1
    if window_count <= 1:
        _log("auth_failure", client, reason=reason)
    if limit > 0 and window_count == limit:
        _log("auth_lockout", client, limit_per_minute=limit)


def record_denied(client, key_name, key_scope, needed):
    """An authenticated request refused because the key's scope is too narrow (names, never values)."""
    with _LOCK:
        _TOTALS["denied"] += 1
    _log("auth_denied", client, key=key_name, scope=key_scope, required=needed)


def record_throttled():
    with _LOCK:
        _TOTALS["throttled"] += 1


def prometheus_text():
    totals_now = totals()
    text = ""
    for metric, help_text, key in (
        ("notebook_to_api_dashboard_auth_failures_total", "Requests rejected for a missing or invalid dashboard key.", "failures"),
        ("notebook_to_api_dashboard_auth_throttled_total", "Requests refused with 429 because their client had too many failed attempts.", "throttled"),
        ("notebook_to_api_dashboard_auth_lockouts_total", "Times a client reached the failed-authentication limit.", "lockouts"),
        ("notebook_to_api_dashboard_auth_denied_total", "Authenticated requests refused with 403 because the key's scope was too narrow.", "denied"),
    ):
        text += f"# HELP {metric} {help_text}\n# TYPE {metric} counter\n{metric} {totals_now[key]}\n"
    return text
