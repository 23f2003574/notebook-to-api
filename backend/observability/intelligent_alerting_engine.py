from dataclasses import dataclass


@dataclass
class PlatformAlert:

    alert_id: str

    title: str

    severity: str

    component: str

    status: str


class IntelligentAlertingEngine:

    def create_alert(
        self,
        title: str,
        severity: str,
        component: str
    ):

        return PlatformAlert(

            alert_id=
                "alert-001",

            title=
                title,

            severity=
                severity,

            component=
                component,

            status=
                "open"
        )
