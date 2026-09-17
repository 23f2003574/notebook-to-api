from textwrap import dedent


class PythonPaginationGenerator:

    def generate_page_model(
        self
    ):

        return dedent(
            """
            from pydantic import (
                BaseModel
            )


            class PaginationInfo(
                BaseModel
            ):

                page: int

                limit: int

                total: int
            """
        )
