from dataclasses import dataclass


@dataclass
class RuntimeWorker:

    worker_id: str

    status: str

    active_tasks: int


@dataclass
class WorkerPool:

    workers: list[RuntimeWorker]


class RuntimeWorkerPoolEngine:

    def create(
        self
    ):

        return WorkerPool(

            workers=[

                RuntimeWorker(

                    worker_id=
                        "worker-1",

                    status=
                        "idle",

                    active_tasks=
                        0
                )
            ]
        )
