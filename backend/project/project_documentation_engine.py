from dataclasses import dataclass


@dataclass
class DocumentationArtifact:

    name: str

    format: str

    output_path: str


@dataclass
class DocumentationBundle:

    artifacts: list[DocumentationArtifact]


class ProjectDocumentationEngine:

    def generate(
        self,
        project_id: str
    ):

        return DocumentationBundle(

            artifacts=[

                DocumentationArtifact(

                    name=
                        "API Documentation",

                    format=
                        "OpenAPI",

                    output_path=
                        "docs/openapi.json"
                ),

                DocumentationArtifact(

                    name=
                        "Project Guide",

                    format=
                        "Markdown",

                    output_path=
                        "docs/README.md"
                )
            ]
        )
