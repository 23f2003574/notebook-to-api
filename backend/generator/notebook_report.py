from dataclasses import dataclass


@dataclass
class NotebookReport:

    title: str

    sections: list[str]

    section_count: int


class NotebookReportGenerator:

    def generate(
        self
    ):

        sections = [

            "Summary",

            "Metadata",

            "Intent",

            "Models",

            "Inputs",

            "Outputs",

            "API Candidates"
        ]

        return NotebookReport(

            title=
                "Notebook Analysis Report",

            sections=
                sections,

            section_count=
                len(sections)
        )
