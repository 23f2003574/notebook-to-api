from dataclasses import dataclass


@dataclass
class DeploymentIncident:

    severity: str

    summary: str

    actions: list[str]


class DeploymentIncidentAnalyzer:

    def analyze(
        self,
        risk
    ):

        actions = []

        if risk.level == "high":

            severity = (
                "critical"
            )

            summary = (
                "Deployment blocked"
            )

            actions.extend(
                [
                    "Resolve readiness issues",
                    "Improve deployment health"
                ]
            )

        elif (
            risk.level
            == "medium"
        ):

            severity = (
                "warning"
            )

            summary = (
                "Deployment requires review"
            )

            actions.append(
                "Review deployment plan"
            )

        else:

            severity = (
                "normal"
            )

            summary = (
                "No active incidents"
            )

        return DeploymentIncident(
            severity=severity,

            summary=summary,

            actions=actions
        )