from dataclasses import dataclass


@dataclass
class LLMIntegration:

    provider: str

    interaction_pattern: str

    recommended_model: str

    prompt_strategy: str


class LLMIntegrationEngine:

    def generate(
        self
    ):

        return LLMIntegration(

            provider=
                "OpenAI",

            interaction_pattern=
                "tool_calling",

            recommended_model=
                "gpt-5.5",

            prompt_strategy=
                "structured_system_prompt"
        )
