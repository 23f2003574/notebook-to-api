from dataclasses import dataclass


@dataclass
class DeveloperExperienceControlCenter:

    documentation: object

    openapi: object

    examples: object

    quickstart: object

    errors: object

    tutorial: object

    cookbook: object

    faq: object

    troubleshooting: object

    migration: object

    changelog: object

    portal: object


class DeveloperExperienceControlCenterGenerator:

    def generate(
        self,
        documentation,
        openapi,
        examples,
        quickstart,
        errors,
        tutorial,
        cookbook,
        faq,
        troubleshooting,
        migration,
        changelog,
        portal
    ):

        return (
            DeveloperExperienceControlCenter(

                documentation=
                    documentation,

                openapi=
                    openapi,

                examples=
                    examples,

                quickstart=
                    quickstart,

                errors=
                    errors,

                tutorial=
                    tutorial,

                cookbook=
                    cookbook,

                faq=
                    faq,

                troubleshooting=
                    troubleshooting,

                migration=
                    migration,

                changelog=
                    changelog,

                portal=
                    portal
            )
        )
