from dataclasses import dataclass


@dataclass
class PlatformReport:

    title: str

    sections: list[str]

    section_count: int


class PlatformReportGenerator:

    def generate(
        self
    ):

        sections = [
            "Platform Readiness Assessment",
            "Developer Experience",
            "Internal Developer Platform",
            "Platform Engineering Architecture",
            "Platform Operations",
            "Platform Recommendations",
            "Platform Scorecard"
        ]

        return PlatformReport(
            title=
                "Platform Report",
            sections=
                sections,
            section_count=
                len(
                    sections
                )
        )
