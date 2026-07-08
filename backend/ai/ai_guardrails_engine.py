from dataclasses import dataclass


@dataclass
class GuardrailPolicy:

    name: str

    enabled: bool

    action: str


@dataclass
class GuardrailDecision:

    allowed: bool

    triggered_policies: list[str]


class AiGuardrailsEngine:

    def evaluate(
        self,
        prompt: str
    ):

        return GuardrailDecision(

            allowed=
                True,

            triggered_policies=[]
        )
