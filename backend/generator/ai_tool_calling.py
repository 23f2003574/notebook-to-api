from dataclasses import dataclass


@dataclass
class AIToolCalling:

    tool_selection_strategy: str

    available_tools: list[str]

    dynamic_routing_enabled: bool

    function_calling_mode: str


class AIToolCallingIntelligenceEngine:

    def generate(
        self
    ):

        return AIToolCalling(

            tool_selection_strategy=
                "capability_based",

            available_tools=[

                "search",

                "code_execution",

                "database",

                "http_client"
            ],

            dynamic_routing_enabled=
                True,

            function_calling_mode=
                "automatic"
        )
