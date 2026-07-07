from dataclasses import dataclass


@dataclass
class ProjectTemplate:

    template_id: str

    name: str

    description: str

    dependencies: list[str]


class ProjectTemplateEngine:

    def load(
        self,
        template_name: str
    ):

        return ProjectTemplate(

            template_id=
                "template-001",

            name=
                template_name,

            description=
                "Project template.",

            dependencies=[]
        )
