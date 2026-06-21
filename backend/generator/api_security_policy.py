from dataclasses import dataclass


@dataclass
class APISecurityPolicy:

    https_required: bool

    rate_limiting_enabled: bool

    cors_enabled: bool

    security_headers_enabled: bool


class APISecurityPolicyEngine:

    def generate(
        self
    ):

        return APISecurityPolicy(

            https_required=
                True,

            rate_limiting_enabled=
                True,

            cors_enabled=
                True,

            security_headers_enabled=
                True
        )
