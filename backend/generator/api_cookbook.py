from dataclasses import dataclass


@dataclass
class CookbookRecipe:

    title: str

    description: str

    example: str


@dataclass
class APICookbook:

    recipes: list[CookbookRecipe]


class APICookbookGenerator:

    def generate(
        self,
        endpoint
    ):

        recipes = [

            CookbookRecipe(

                title=
                    "Basic Request",

                description=
                    (
                        "Call the endpoint "
                        "with required inputs"
                    ),

                example=
                    (
                        f"client."
                        f"{endpoint.name}()"
                    )
            ),

            CookbookRecipe(

                title=
                    "Response Handling",

                description=
                    (
                        "Process returned "
                        "results"
                    ),

                example=
                    (
                        "response['result']"
                    )
            ),

            CookbookRecipe(

                title=
                    "Error Handling",

                description=
                    (
                        "Handle common API "
                        "failures"
                    ),

                example=
                    (
                        "try/except"
                    )
            )
        ]

        return APICookbook(
            recipes=recipes
        )
