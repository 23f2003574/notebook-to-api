from dataclasses import dataclass


@dataclass
class TroubleshootingIssue:

    issue: str

    possible_cause: str

    resolution: str


@dataclass
class APITroubleshootingGuide:

    issues: list[TroubleshootingIssue]


class APITroubleshootingGenerator:

    def generate(
        self
    ):

        return APITroubleshootingGuide(

            issues=[

                TroubleshootingIssue(

                    issue=
                        "400 Bad Request",

                    possible_cause=
                        "Invalid request payload",

                    resolution=
                        (
                            "Verify request "
                            "parameters"
                        )
                ),

                TroubleshootingIssue(

                    issue=
                        "422 Validation Error",

                    possible_cause=
                        "Missing required field",

                    resolution=
                        (
                            "Review schema "
                            "requirements"
                        )
                ),

                TroubleshootingIssue(

                    issue=
                        "500 Internal Server Error",

                    possible_cause=
                        "Unexpected server failure",

                    resolution=
                        (
                            "Review logs "
                            "and deployment state"
                        )
                )
            ]
        )
