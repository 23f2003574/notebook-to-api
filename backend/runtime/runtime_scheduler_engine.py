from dataclasses import dataclass


@dataclass
class ScheduledTask:

    task_id: str

    priority: int

    execution_state: str


@dataclass
class RuntimeSchedule:

    tasks: list[ScheduledTask]


class RuntimeSchedulerEngine:

    def schedule(
        self,
        runtime_context
    ):

        return RuntimeSchedule(

            tasks=[

                ScheduledTask(

                    task_id=
                        "startup",

                    priority=
                        1,

                    execution_state=
                        "queued"
                )
            ]
        )
