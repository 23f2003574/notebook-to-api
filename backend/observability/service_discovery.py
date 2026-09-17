from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional


VALID_DISCOVERY_SOURCES = ("static_registry", "cluster", "plugins", "workers", "gateway")


@dataclass
class ServiceNode:
    name: str
    source: str
    address: str
    depends_on: List[str] = field(default_factory=list)

    def __post_init__(self):
        if self.source not in VALID_DISCOVERY_SOURCES:
            raise ValueError(
                f"Unsupported source '{self.source}'. Expected one of {VALID_DISCOVERY_SOURCES}."
            )


@dataclass
class TopologySnapshot:
    nodes: List[ServiceNode]
    captured_at: str


class ServiceDiscoveryMonitor:
    def __init__(self, scan_fn: Callable[[], List[ServiceNode]]):
        self._scan_fn = scan_fn
        self._last_snapshot: Optional[TopologySnapshot] = None

    def scan(self) -> TopologySnapshot:
        nodes = self._scan_fn()
        return TopologySnapshot(nodes=list(nodes), captured_at=_utc_now_iso())

    def detect_changes(
        self, previous: TopologySnapshot, current: TopologySnapshot
    ) -> Dict[str, List[str]]:
        previous_names = {node.name for node in previous.nodes}
        current_names = {node.name for node in current.nodes}
        return {
            "added": sorted(current_names - previous_names),
            "removed": sorted(previous_names - current_names),
        }

    def refresh(self) -> Dict[str, List[str]]:
        new_snapshot = self.scan()
        if self._last_snapshot is None:
            changes = {"added": sorted(node.name for node in new_snapshot.nodes), "removed": []}
        else:
            changes = self.detect_changes(self._last_snapshot, new_snapshot)
        self._last_snapshot = new_snapshot
        return changes

    def topology(self) -> TopologySnapshot:
        if self._last_snapshot is None:
            raise ValueError("No topology snapshot available; call refresh() first")
        return self._last_snapshot


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
