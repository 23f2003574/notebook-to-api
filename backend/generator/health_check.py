from dataclasses import dataclass


@dataclass
class HealthCheck:

    endpoint: str

    method: str

    success_status: int


class HealthCheckEngine:

    def generate(
        self
    ):

        return HealthCheck(

            endpoint=
                "/health",

            method=
                "GET",

            success_status=
                200
        )
