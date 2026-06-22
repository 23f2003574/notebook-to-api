from dataclasses import dataclass


@dataclass
class TestAutomation:

    workflow_name: str

    triggers: list[str]

    actions: list[str]


class TestAutomationEngine:

    def generate(
        self
    ):

        return TestAutomation(

            workflow_name=
                "continuous_testing",

            triggers=[

                "pull_request",

                "merge_to_main",

                "release_candidate"
            ],

            actions=[

                "run_unit_tests",

                "run_integration_tests",

                "generate_test_report"
            ]
        )
