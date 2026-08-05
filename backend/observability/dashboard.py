from datetime import datetime, timezone
from typing import Dict, List, Optional

from backend.observability.alert_engine import AlertRuleEngine
from backend.observability.export_service import ExportManifest, TelemetryExportService
from backend.observability.health_checks import HealthCheckFramework
from backend.observability.observability_analytics import ObservabilityAnalyticsService


class ObservabilityDashboardAPI:
    def __init__(
        self,
        analytics_service: ObservabilityAnalyticsService,
        alert_engine: AlertRuleEngine,
        health_framework: HealthCheckFramework,
        export_service: Optional[TelemetryExportService] = None,
    ):
        self._analytics_service = analytics_service
        self._alert_engine = alert_engine
        self._health_framework = health_framework
        self._export_service = export_service

    def metrics(self) -> Dict[str, Dict[str, float]]:
        snapshot = self._analytics_service.summary()
        return {
            name: {"average": average, "latest": self._analytics_service.latest(name)}
            for name, average in snapshot.metrics.items()
        }

    def alerts(self, active_only: bool = True) -> List[Dict]:
        events = self._alert_engine.list_alerts(active_only=active_only)
        return [
            {
                "alert_id": event.alert_id,
                "rule_name": event.rule_name,
                "severity": event.severity,
                "message": event.message,
                "is_active": event.is_active,
            }
            for event in events
        ]

    def health(self) -> Dict:
        report = self._health_framework.aggregate()
        return {"status": report.status, "checked_at": report.checked_at}

    def export(self, metric_names: List[str], format: str = "json") -> ExportManifest:
        if self._export_service is None:
            raise ValueError("No export_service configured for this dashboard")
        return self._export_service.export_all(metric_names, format=format)

    def overview(self) -> Dict:
        return {
            "generated_at": _utc_now_iso(),
            "metrics": self.metrics(),
            "alerts": self.alerts(),
            "health": self.health(),
        }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
