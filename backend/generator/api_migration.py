from dataclasses import dataclass


@dataclass
class MigrationStep:

    order: int

    title: str

    action: str


@dataclass
class APIMigrationGuide:

    from_version: str

    to_version: str

    steps: list[MigrationStep]


class APIMigrationGuideGenerator:

    def generate(
        self
    ):

        return APIMigrationGuide(

            from_version=
                "v1",

            to_version=
                "v2",

            steps=[

                MigrationStep(
                    order=1,

                    title=
                        "Review Changes",

                    action=
                        (
                            "Review generated "
                            "API differences"
                        )
                ),

                MigrationStep(
                    order=2,

                    title=
                        "Update Client",

                    action=
                        (
                            "Upgrade generated "
                            "SDK version"
                        )
                ),

                MigrationStep(
                    order=3,

                    title=
                        "Validate Integration",

                    action=
                        (
                            "Run integration "
                            "tests"
                        )
                )
            ]
        )
