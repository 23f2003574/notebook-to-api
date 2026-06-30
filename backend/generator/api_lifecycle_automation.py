from dataclasses import dataclass


@dataclass
class APILifecycleAutomation:

    workflow_name: str

    triggers: list[str]

    actions: list[str]


class APILifecycleAutomationEngine:

    def generate(
        self
    ):

        return APILifecycleAutomation(

            workflow_name=
                "api_lifecycle_management",

            triggers=[

                "new_api_version_created",

                "release_candidate_approved",

                "deprecation_window_started"
            ],

            actions=[

                "publish_openapi_spec",

                "generate_sdk_release",

                "notify_api_consumers",

                "update_api_catalog"
            ]
        )