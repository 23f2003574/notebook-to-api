from dataclasses import dataclass


@dataclass
class DeploymentAlert:

    level: str

    notify: bool

    recipients: list[str]

    message: str


class DeploymentAlertGenerator:

    def generate(
        self,
        incident
    ):

        if (
            incident.severity
            == "critical"
        ):

            return DeploymentAlert(
                level="critical",

                notify=True,

                recipients=[
                    "devops",
                    "engineering"
                ],

                message=
                    "Deployment blocked"
            )

        if (
            incident.severity
            == "warning"
        ):

            return DeploymentAlert(
                level="warning",

                notify=True,

                recipients=[
                    "engineering"
                ],

                message=
                    "Deployment requires review"
            )

        return DeploymentAlert(
            level="info",

            notify=False,

            recipients=[],

            message=
                "No action required"
        )