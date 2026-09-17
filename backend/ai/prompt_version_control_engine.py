from dataclasses import dataclass


@dataclass
class PromptVersion:

    version: str

    content: str

    author: str


@dataclass
class PromptHistory:

    prompt_id: str

    versions: list[PromptVersion]


class PromptVersionControlEngine:

    def create_history(
        self,
        prompt_id: str
    ):

        return PromptHistory(

            prompt_id=
                prompt_id,

            versions=[]
        )
