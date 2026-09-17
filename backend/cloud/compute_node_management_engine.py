from dataclasses import dataclass


@dataclass
class ComputeNode:

    node_id: str

    hostname: str

    cpu_cores: int

    memory_gb: int

    status: str


class ComputeNodeManagementEngine:

    def register(
        self,
        hostname: str,
        cpu_cores: int,
        memory_gb: int
    ):

        return ComputeNode(

            node_id=
                "node-001",

            hostname=
                hostname,

            cpu_cores=
                cpu_cores,

            memory_gb=
                memory_gb,

            status=
                "ready"
        )
