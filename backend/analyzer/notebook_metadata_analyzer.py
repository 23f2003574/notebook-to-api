from dataclasses import dataclass


@dataclass
class NotebookMetadata:

    notebook_name: str

    kernel_name: str | None

    language: str | None

    total_cells: int

    code_cells: int

    markdown_cells: int

    raw_cells: int

    executed_cells: int


class NotebookMetadataAnalyzer:

    def analyze(
        self,
        notebook_name,
        notebook
    ):

        cells = notebook.get(
            "cells",
            []
        )

        metadata = notebook.get(
            "metadata",
            {}
        )

        code_cells = 0
        markdown_cells = 0
        raw_cells = 0
        executed_cells = 0

        for cell in cells:

            cell_type = (
                cell.get(
                    "cell_type"
                )
            )

            if (
                cell_type
                == "code"
            ):

                code_cells += 1

                if (
                    cell.get(
                        "execution_count"
                    )
                    is not None
                ):
                    executed_cells += 1

            elif (
                cell_type
                == "markdown"
            ):
                markdown_cells += 1

            elif (
                cell_type
                == "raw"
            ):
                raw_cells += 1

        return NotebookMetadata(

            notebook_name=
                notebook_name,

            kernel_name=
                metadata
                .get(
                    "kernelspec",
                    {}
                )
                .get(
                    "name"
                ),

            language=
                metadata
                .get(
                    "language_info",
                    {}
                )
                .get(
                    "name"
                ),

            total_cells=
                len(cells),

            code_cells=
                code_cells,

            markdown_cells=
                markdown_cells,

            raw_cells=
                raw_cells,

            executed_cells=
                executed_cells
        )
