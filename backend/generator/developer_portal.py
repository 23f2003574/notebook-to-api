from dataclasses import dataclass


@dataclass
class DeveloperPortal:

    title: str

    sections: list[str]

    documentation_count: int


class DeveloperPortalGenerator:

    def generate(
        self
    ):

        sections = [

            "Documentation",

            "Examples",

            "Quick Start",

            "Error Reference",

            "Tutorials",

            "Cookbook",

            "FAQ",

            "Troubleshooting",

            "Migration Guide",

            "Changelog"
        ]

        return DeveloperPortal(

            title=
                "Generated API Portal",

            sections=
                sections,

            documentation_count=
                len(sections)
        )
