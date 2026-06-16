from dataclasses import dataclass


@dataclass
class TutorialStep:

    order: int

    title: str

    instruction: str


@dataclass
class APITutorial:

    title: str

    steps: list[TutorialStep]


class APITutorialGenerator:

    def generate(
        self,
        endpoint
    ):

        return APITutorial(

            title=
                (
                    f"Getting Started "
                    f"with {endpoint.name}"
                ),

            steps=[

                TutorialStep(
                    order=1,

                    title=
                        "Install SDK",

                    instruction=
                        (
                            "Install the "
                            "generated SDK"
                        )
                ),

                TutorialStep(
                    order=2,

                    title=
                        "Create Client",

                    instruction=
                        (
                            "Initialize "
                            "the API client"
                        )
                ),

                TutorialStep(
                    order=3,

                    title=
                        "Send Request",

                    instruction=
                        (
                            "Call the "
                            "generated endpoint"
                        )
                ),

                TutorialStep(
                    order=4,

                    title=
                        "Process Response",

                    instruction=
                        (
                            "Handle returned "
                            "results"
                        )
                )
            ]
        )
