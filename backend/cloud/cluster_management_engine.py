from dataclasses import dataclass


@dataclass
class ComputeCluster:

    cluster_id: str

    name: str

    node_count: int

    status: str


class ClusterManagementEngine:

    def register(
        self,
        name: str,
        node_count: int
    ):

        return ComputeCluster(

            cluster_id=
                "cluster-001",

            name=
                name,

            node_count=
                node_count,

            status=
                "healthy"
        )
