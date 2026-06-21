from dataclasses import dataclass


@dataclass
class AuthenticationRecommendation:

    strategy: str

    token_based: bool

    confidence: float


class AuthenticationRecommendationEngine:

    def generate(
        self
    ):

        return AuthenticationRecommendation(

            strategy=
                "jwt",

            token_based=
                True,

            confidence=
                0.95
        )
