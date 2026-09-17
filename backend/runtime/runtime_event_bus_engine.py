from dataclasses import dataclass


@dataclass
class RuntimeEvent:

    event_type: str

    source: str

    payload: dict


class RuntimeEventBusEngine:

    def publish(
        self,
        event: RuntimeEvent
    ):

        return True

    def subscribe(
        self,
        event_type: str
    ):

        return []
