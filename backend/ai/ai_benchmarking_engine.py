from dataclasses import dataclass


@dataclass
class BenchmarkEntry:

    model_id: str

    score: float

    latency_ms: float

    cost_per_request: float


@dataclass
class BenchmarkReport:

    benchmark_id: str

    entries: list[BenchmarkEntry]


class AiBenchmarkingEngine:

    def benchmark(
        self,
        dataset_id: str
    ):

        return BenchmarkReport(

            benchmark_id=
                "benchmark-001",

            entries=[]
        )
