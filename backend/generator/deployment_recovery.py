from dataclasses import dataclass


@dataclass
class RecoveryAction:

    priority: int

    action: str


@dataclass
class DeploymentRecovery:

    severity: str

    actions: list[RecoveryAction]


class DeploymentRecoveryGenerator:

    def generate(
        self,
        incident
    ):

        actions = []

        if (
            incident.severity
            == "critical"
        ):

            actions.extend(
                [
                    RecoveryAction(
                        priority=1,
                        action=
                            "Execute rollback"
                    ),

                    RecoveryAction(
                        priority=2,
                        action=
                            "Restore previous deployment"
                    ),

                    RecoveryAction(
                        priority=3,
                        action=
                            "Verify application health"
                    )
                ]
            )

        elif (
            incident.severity
            == "warning"
        ):

            actions.append(
                RecoveryAction(
                    priority=1,
                    action=
                        "Review deployment logs"
                )
            )

        else:

            actions.append(
                RecoveryAction(
                    priority=1,
                    action=
                        "No recovery required"
                )
            )

        return DeploymentRecovery(
            severity=
                incident.severity,

            actions=
                actions
        )