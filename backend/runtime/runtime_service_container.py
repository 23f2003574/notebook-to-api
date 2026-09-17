from dataclasses import dataclass
from typing import Any


@dataclass
class ServiceRegistration:

    name: str

    implementation: Any


class RuntimeServiceContainer:

    def __init__(self):

        self._services = {}

    def register(
        self,
        service: ServiceRegistration
    ):

        self._services[
            service.name
        ] = service.implementation

    def resolve(
        self,
        name: str
    ):

        return self._services.get(
            name
        )
