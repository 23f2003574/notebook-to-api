from dataclasses import dataclass


@dataclass
class PlatformAutomation:

    workflow_name: str

    triggers: list[str]

    actions: list[str]


class PlatformAutomationEngine:

    def generate(
        self
    ):

        return PlatformAutomation(
            workflow_name=
                "platform_self_service",
            triggers=[
                "developer_request",
                "repository_created",
                "service_registered"
            ],
            actions=[
                "provision_infrastructure",
                "configure_ci_cd",
                "register_service",
                "notify_platform_team"
            ]
        )
