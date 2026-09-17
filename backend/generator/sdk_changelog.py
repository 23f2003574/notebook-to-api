from dataclasses import dataclass


@dataclass
class SDKChangelog:

    version: str

    added: list[str]

    improved: list[str]

    fixed: list[str]


class SDKChangelogEngine:

    def generate(
        self,
        version
    ):

        return SDKChangelog(

            version=
                version,

            added=[
                "Generated functionality"
            ],

            improved=[
                "SDK generation"
            ],

            fixed=[
                "Minor issues"
            ]
        )
