from dataclasses import dataclass


@dataclass
class AuthorizationPolicy:

    model: str

    roles: list[str]

    default_role: str


class AuthorizationPolicyEngine:

    def generate(
        self
    ):

        return AuthorizationPolicy(

            model=
                "rbac",

            roles=[

                "admin",

                "developer",

                "viewer"
            ],

            default_role=
                "viewer"
        )
