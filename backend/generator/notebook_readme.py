from dataclasses import dataclass


@dataclass
class NotebookREADME:

    title: str

    sections: list[str]

    content: str


class NotebookREADMEGenerator:

    def generate(
        self,
        understanding
    ):

        sections = [

            "Overview",

            "Intent",

            "Models",

            "Inputs",

            "Outputs",

            "API Candidates"
        ]

        content = (
            "# Notebook README\n\n"
            "Generated automatically from "
            "notebook understanding."
        )

        return NotebookREADME(

            title=
                "Notebook README",

            sections=
                sections,

            content=
                content
        )
