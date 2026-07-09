from dataclasses import dataclass


@dataclass
class ResourceQuota:

    quota_id: str

    cpu_cores: int

    memory_gb: int

    gpu_count: int


@dataclass
class QuotaAllocation:

    approved: bool

    remaining_cpu: int

    remaining_memory_gb: int


class ResourceQuotaManagementEngine:

    def allocate(
        self,
        quota: ResourceQuota
    ):

        return QuotaAllocation(

            approved=
                True,

            remaining_cpu=
                quota.cpu_cores,

            remaining_memory_gb=
                quota.memory_gb
        )
