from dataclasses import dataclass


@dataclass
class RegisteredAgent:

    agent_id: str

    name: str

    model_id: str

    prompt_id: str


class AiAgentRegistryEngine:

    def register(
        self,
        name: str,
        model_id: str,
        prompt_id: str
    ):

        return RegisteredAgent(

            agent_id=
                "agent-001",

            name=
                name,

            model_id=
                model_id,

            prompt_id=
                prompt_id
        )
