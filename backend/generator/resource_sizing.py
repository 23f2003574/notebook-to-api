from dataclasses import dataclass


@dataclass
class ResourceSizing:

    cpu_limit: str

    memory_limit: str

    storage_limit: str

    concurrency_limit: int


class ResourceSizingEngine:

    def generate(
        self
    ):

        return ResourceSizing(

            cpu_limit=
                "2 vCPU",

            memory_limit=
                "1 GB",

            storage_limit=
                "5 GB",

            concurrency_limit=
                100
        )
