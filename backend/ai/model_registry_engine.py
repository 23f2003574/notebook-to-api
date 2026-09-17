from dataclasses import dataclass


@dataclass
class RegisteredModel:

    model_id: str

    provider: str

    name: str

    version: str


class ModelRegistryEngine:

    def register(
        self,
        provider: str,
        name: str,
        version: str
    ):

        return RegisteredModel(

            model_id=
                "model-001",

            provider=
                provider,

            name=
                name,

            version=
                version
        )
