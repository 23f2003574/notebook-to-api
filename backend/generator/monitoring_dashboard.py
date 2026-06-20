from dataclasses import dataclass


@dataclass
class MonitoringDashboard:

    title: str

    widgets: list[str]

    widget_count: int


class MonitoringDashboardEngine:

    def generate(
        self
    ):

        widgets = [

            "Request Rate",

            "Request Latency",

            "Error Rate",

            "System Health",

            "CPU Usage",

            "Memory Usage"
        ]

        return MonitoringDashboard(

            title=
                "API Monitoring Dashboard",

            widgets=
                widgets,

            widget_count=
                len(widgets)
        )
