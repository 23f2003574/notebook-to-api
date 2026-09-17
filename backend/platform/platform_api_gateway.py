from dataclasses import dataclass


@dataclass
class PlatformRequest:

    operation: str

    payload: dict


@dataclass
class PlatformResponse:

    success: bool

    message: str

    data: dict


class PlatformApiGateway:

    def handle(
        self,
        request: PlatformRequest
    ):

        return PlatformResponse(

            success=True,

            message="Request accepted.",

            data={}
        )
