from dataclasses import dataclass


@dataclass
class EndpointSuggestion:

    endpoint_name: str

    route: str

    confidence: float


class NotebookEndpointSuggestionEngine:

    def generate(
        self,
        understanding
    ):

        suggestions = []

        for candidate in (
            understanding.api_candidates
        ):

            endpoint_name = (
                candidate.endpoint_name
            )

            suggestions.append(

                EndpointSuggestion(

                    endpoint_name=
                        endpoint_name,

                    route=
                        f"/{endpoint_name}",

                    confidence=
                        candidate.confidence
                )
            )

        return suggestions
