from dataclasses import dataclass


@dataclass
class Symbol:

    name: str

    symbol_type: str

    scope: str

    resolved: bool


class SymbolResolutionEngine:

    def resolve(
        self,
        ast
    ):

        return [

            Symbol(

                name="predict",

                symbol_type="function",

                scope="module",

                resolved=True
            )
        ]
