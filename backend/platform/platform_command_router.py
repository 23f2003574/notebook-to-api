from dataclasses import dataclass


@dataclass
class PlatformCommand:

    operation: str

    target: str

    payload: dict


@dataclass
class RouteResult:

    target_service: str

    accepted: bool


class PlatformCommandRouter:

    def route(
        self,
        command: PlatformCommand
    ):

        return RouteResult(

            target_service=
                command.target,

            accepted=
                True
        )
