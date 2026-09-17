from dataclasses import dataclass


@dataclass
class StructuredLogRecord:

    level: str

    message: str

    component: str

    timestamp: str

    metadata: dict


class StructuredLoggingEngine:

    def log(
        self,
        level: str,
        message: str,
        component: str,
        metadata: dict
    ):

        return StructuredLogRecord(

            level=
                level,

            message=
                message,

            component=
                component,

            timestamp=
                "2026-07-11T00:00:00Z",

            metadata=
                metadata
        )
