from dataclasses import dataclass


@dataclass
class PlatformIncident:

    incident_id: str

    title: str

    severity: str

    component: str

    owner: str

    status: str


class IncidentManagementEngine:

    def create_incident(
        self,
        title: str,
        severity: str,
        component: str,
        owner: str
    ):

        return PlatformIncident(

            incident_id=
                "incident-001",

            title=
                title,

            severity=
                severity,

            component=
                component,

            owner=
                owner,

            status=
                "investigating"
        )
