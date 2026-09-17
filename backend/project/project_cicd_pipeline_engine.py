from dataclasses import dataclass


@dataclass
class PipelineStage:

    name: str

    status: str


@dataclass
class ProjectPipeline:

    pipeline_id: str

    stages: list[PipelineStage]


class ProjectCICDPipelineEngine:

    def create(
        self,
        project_id: str
    ):

        return ProjectPipeline(

            pipeline_id=
                "pipeline-001",

            stages=[

                PipelineStage(

                    name=
                        "build",

                    status=
                        "pending"
                ),

                PipelineStage(

                    name=
                        "test",

                    status=
                        "pending"
                ),

                PipelineStage(

                    name=
                        "release",

                    status=
                        "pending"
                ),

                PipelineStage(

                    name=
                        "deploy",

                    status=
                        "pending"
                )
            ]
        )
