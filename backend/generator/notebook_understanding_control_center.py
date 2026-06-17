from dataclasses import dataclass


@dataclass
class NotebookUnderstandingControlCenter:

    summary_enabled: bool

    report_enabled: bool

    readme_enabled: bool

    endpoint_suggestions_enabled: bool


class NotebookUnderstandingControlCenterGenerator:

    def generate(
        self
    ):

        return NotebookUnderstandingControlCenter(

            summary_enabled=
                True,

            report_enabled=
                True,

            readme_enabled=
                True,

            endpoint_suggestions_enabled=
                True
        )
