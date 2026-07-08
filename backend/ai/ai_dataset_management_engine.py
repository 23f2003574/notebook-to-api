from dataclasses import dataclass


@dataclass
class AiDataset:

    dataset_id: str

    name: str

    version: str

    sample_count: int


class AiDatasetManagementEngine:

    def register(
        self,
        name: str,
        sample_count: int
    ):

        return AiDataset(

            dataset_id=
                "dataset-001",

            name=
                name,

            version=
                "1.0.0",

            sample_count=
                sample_count
        )
