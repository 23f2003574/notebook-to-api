from dataclasses import dataclass


@dataclass
class ChangelogEntry:

    category: str

    description: str


@dataclass
class APIChangelog:

    version: str

    entries: list[ChangelogEntry]


class APIChangelogGenerator:

    def generate(
        self,
        version
    ):

        return APIChangelog(

            version=version,

            entries=[

                ChangelogEntry(

                    category=
                        "added",

                    description=
                        (
                            "Generated API "
                            "documentation"
                        )
                ),

                ChangelogEntry(

                    category=
                        "improved",

                    description=
                        (
                            "Enhanced developer "
                            "experience assets"
                        )
                ),

                ChangelogEntry(

                    category=
                        "fixed",

                    description=
                        (
                            "Improved generated "
                            "API metadata"
                        )
                )
            ]
        )
