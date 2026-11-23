"""Health and failure visibility for the governance audit storage.

Audit-storage trouble is reported separately from compile/deploy outcomes: an operation can
succeed while its audit record could not be written, and that must be visible on its own.
Reasons are a fixed small set (low-cardinality metric labels); nothing here carries notebook
names, record contents, filesystem paths or credentials."""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone

REASONS = ("storage_error", "configuration", "other")
_log = logging.getLogger("uvicorn.error")


def classify(exc: BaseException) -> str:
    from .deployment_governance_audit_store import GovernanceAuditStorageError

    if isinstance(exc, GovernanceAuditStorageError):
        # Misconfiguration messages are raised while building the service; I/O ones while using it.
        return "configuration" if getattr(exc, "configuration", False) else "storage_error"

    return "other"


class AuditHealth:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with getattr(self, "_lock", threading.Lock()):
            self.writes = 0
            self.failures = {reason: 0 for reason in REASONS}
            self.consecutive_failures = 0
            self.last_failure_at = None
            self.last_failure_reason = None
            self.recovered_at = None

    def _event(self, event: str, **fields) -> None:
        _log.warning("governance_audit_event %s", json.dumps({"event": event, **fields}, sort_keys=True))

    def record_success(self) -> None:
        with self._lock:
            self.writes += 1
            recovered = self.consecutive_failures > 0
            streak = self.consecutive_failures
            self.consecutive_failures = 0
            if recovered:
                self.recovered_at = datetime.now(timezone.utc).isoformat()
        if recovered:
            self._event("audit_write_recovered", failed_writes_before_recovery=streak)

    def record_failure(self, exc: BaseException) -> None:
        reason = classify(exc)
        with self._lock:
            self.failures[reason] += 1
            self.consecutive_failures += 1
            self.last_failure_at = datetime.now(timezone.utc).isoformat()
            self.last_failure_reason = reason
            streak = self.consecutive_failures
        self._event("audit_write_failed", reason=reason, consecutive_failures=streak)

    def snapshot(self, *, probe: bool = True) -> dict:
        """Current state. With probe=True the storage is also read once, so "unavailable" means
        it cannot be used right now (not merely that a past write failed)."""
        from .deployment_governance_audit import get_audit_service
        from .deployment_governance_audit_store import audit_storage_status

        status = audit_storage_status()
        reachable, probe_reason = True, None
        if probe:
            try:
                get_audit_service().size()
            except Exception as exc:  # noqa: BLE001 - any failure to use the store counts
                reachable, probe_reason = False, classify(exc)
        with self._lock:
            consecutive = self.consecutive_failures
            data = {
                "backend": status["backend"],
                "durable": status["durable"],
                "keyed_integrity": status["keyed"],
                "issues": list(status["issues"]),
                "writes_total": self.writes,
                "write_failures_total": dict(self.failures),
                "consecutive_failures": consecutive,
                "last_failure_at": self.last_failure_at,
                "last_failure_reason": self.last_failure_reason,
                "recovered_at": self.recovered_at,
            }
        if not reachable:
            data["state"], data["reason"] = "unavailable", probe_reason
        elif consecutive:
            data["state"], data["reason"] = "degraded", self.last_failure_reason
        elif not status["durable"] and (status["production"] or status["backend"] == "sqlite"):
            # Records are being written, but they would not survive a restart. In-memory auditing
            # chosen on purpose for development is not reported as a fault.
            data["state"], data["reason"] = "degraded", "not_durable"
        else:
            data["state"], data["reason"] = "healthy", None
        data["storage"] = self._storage(status)
        return data

    @staticmethod
    def _storage(status) -> "dict | None":
        """Size and age of a sqlite audit database; a warning here never changes `state`."""
        if status["backend"] != "sqlite":
            return None
        from .deployment_governance_audit_storage import database_path, storage_stats

        try:
            return storage_stats(database_path())
        except Exception:  # noqa: BLE001 - statistics must never break a health probe
            return {"state": "unknown", "warnings": []}

    def prometheus_text(self) -> str:
        snap = self.snapshot(probe=True)
        lines = [
            "# HELP notebook_to_api_governance_audit_up 1 when the audit storage is usable and the last write succeeded.",
            "# TYPE notebook_to_api_governance_audit_up gauge",
            f"notebook_to_api_governance_audit_up {1 if snap['state'] == 'healthy' else 0}",
            "# HELP notebook_to_api_governance_audit_durable 1 when audit records survive a restart.",
            "# TYPE notebook_to_api_governance_audit_durable gauge",
            f"notebook_to_api_governance_audit_durable {1 if snap['durable'] else 0}",
            "# HELP notebook_to_api_governance_audit_writes_total Audit records written.",
            "# TYPE notebook_to_api_governance_audit_writes_total counter",
            f"notebook_to_api_governance_audit_writes_total {snap['writes_total']}",
            "# HELP notebook_to_api_governance_audit_write_failures_total Audit writes that failed, by reason.",
            "# TYPE notebook_to_api_governance_audit_write_failures_total counter",
        ]
        storage = snap.get("storage") or {}
        if "records" in storage:
            lines += [
                "# HELP notebook_to_api_governance_audit_records Audit records currently stored.",
                "# TYPE notebook_to_api_governance_audit_records gauge",
                f"notebook_to_api_governance_audit_records {storage['records']}",
                "# HELP notebook_to_api_governance_audit_database_bytes Size of the audit database on disk.",
                "# TYPE notebook_to_api_governance_audit_database_bytes gauge",
                f"notebook_to_api_governance_audit_database_bytes {storage['bytes']}",
            ]
        for reason in REASONS:
            lines.append(
                f'notebook_to_api_governance_audit_write_failures_total{{reason="{reason}"}} {snap["write_failures_total"][reason]}'
            )
        return "\n".join(lines) + "\n"


audit_health = AuditHealth()
