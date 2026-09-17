from dataclasses import dataclass


@dataclass
class ScheduledWorkload:

    workload_id: str

    node_id: str

    status: str


class WorkloadSchedulingEngine:

    def schedule(
        self,
        workload_id: str,
        cluster_id: str
    ):

        return ScheduledWorkload(

            workload_id=
                workload_id,

            node_id=
                "node-001",

            status=
                "scheduled"
        )
