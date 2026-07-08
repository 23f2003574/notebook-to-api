from dataclasses import dataclass


@dataclass
class PromptAsset:

    prompt_id: str

    name: str

    version: str

    content: str


class PromptManagementEngine:

    def register(
        self,
        name: str,
        content: str
    ):

        return PromptAsset(

            prompt_id=
                "prompt-001",

            name=
                name,

            version=
                "1.0.0",

            content=
                content
        )
