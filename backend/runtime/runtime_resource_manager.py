from dataclasses import dataclass


@dataclass
class RuntimeResources:

    cpu_cores: int

    memory_mb: int

    gpu_count: int

    available_workers: int


@dataclass
class ResourceAllocation:

    worker_id: str

    allocated_cpu: int

    allocated_memory_mb: int


class RuntimeResourceManager:

    def available_resources(
        self
    ):

        return RuntimeResources(

            cpu_cores=8,

            memory_mb=16384,

            gpu_count=0,

            available_workers=4
        )

    def allocate(
        self,
        task_id: str
    ):

        return ResourceAllocation(

            worker_id=
                "worker-1",

            allocated_cpu=
                1,

            allocated_memory_mb=
                512
        )
