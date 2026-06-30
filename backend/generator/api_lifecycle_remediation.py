from dataclasses import dataclass


@dataclass
class APILifecycleRemediation:

    issue_type: str

    remediation_actions: list[str]

    priority: str


class APILifecycleRemediationEngine:

    def generate(
        self
    ):

        return APILifecycleRemediation(

            issue_type=
                "failed_api_release",

            remediation_actions=[

                "rollback_previous_release",

                "restore_previous_sdk",

                "reopen_previous_api_version",

                "notify_api_consumers"
            ],

            priority=
                "high"
        )
