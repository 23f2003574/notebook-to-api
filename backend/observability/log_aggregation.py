from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional


VALID_SEVERITIES = ("debug", "info", "warning", "error", "critical")
VALID_LOG_SOURCES = ("api", "workers", "pipelines", "gateway", "ai_engine")


@dataclass
class LogEntry:
    source: str
    severity: str
    message: str
    timestamp: str = ""
    attributes: Dict[str, str] = field(default_factory=dict)
    archived: bool = False

    def __post_init__(self):
        if self.source not in VALID_LOG_SOURCES:
            raise ValueError(
                f"Unsupported source '{self.source}'. Expected one of {VALID_LOG_SOURCES}."
            )
        if self.severity not in VALID_SEVERITIES:
            raise ValueError(
                f"Unsupported severity '{self.severity}'. Expected one of {VALID_SEVERITIES}."
            )
        if not self.timestamp:
            self.timestamp = _utc_now_iso()


@dataclass
class LogBatch:
    entries: List[LogEntry] = field(default_factory=list)


class LogAggregationService:
    def __init__(self, retention_limit: Optional[int] = None):
        self._entries: List[LogEntry] = []
        self._retention_limit = retention_limit

    def ingest(
        self,
        source: str,
        severity: str,
        message: str,
        attributes: Optional[Dict[str, str]] = None,
        timestamp: Optional[str] = None,
    ) -> LogEntry:
        entry = LogEntry(
            source=source,
            severity=severity,
            message=message,
            attributes=attributes or {},
            timestamp=timestamp or "",
        )
        self._entries.append(entry)
        self._enforce_retention()
        return entry

    def query(
        self,
        source: Optional[str] = None,
        severity: Optional[str] = None,
        min_severity: Optional[str] = None,
        include_archived: bool = False,
    ) -> List[LogEntry]:
        results = self._entries if include_archived else [
            entry for entry in self._entries if not entry.archived
        ]

        if source is not None:
            results = [entry for entry in results if entry.source == source]

        if severity is not None:
            results = [entry for entry in results if entry.severity == severity]

        if min_severity is not None:
            min_rank = VALID_SEVERITIES.index(min_severity)
            results = [
                entry for entry in results
                if VALID_SEVERITIES.index(entry.severity) >= min_rank
            ]

        return results

    def tail(self, n: int = 10) -> List[LogEntry]:
        if n <= 0:
            return []
        return list(self._entries[-n:])

    def archive(self, before_timestamp: str) -> LogBatch:
        archived = []
        for entry in self._entries:
            if not entry.archived and entry.timestamp < before_timestamp:
                entry.archived = True
                archived.append(entry)
        return LogBatch(entries=archived)

    def _enforce_retention(self) -> None:
        if self._retention_limit is not None and len(self._entries) > self._retention_limit:
            self._entries = self._entries[-self._retention_limit:]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
