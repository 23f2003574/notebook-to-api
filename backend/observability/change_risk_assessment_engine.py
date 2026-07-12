from dataclasses import dataclass


@dataclass
class ChangeRiskAssessment:

    change_id: str

    files_changed: int

    affected_components: int

    database_change: bool

    infrastructure_change: bool

    risk_score: int

    risk_level: str


class ChangeRiskAssessmentEngine:

    def assess(
        self,
        change_id: str,
        files_changed: int,
        affected_components: int,
        database_change: bool,
        infrastructure_change: bool
    ):

        risk_score = (
            files_changed
            + affected_components * 5
            + (25 if database_change else 0)
            + (25 if infrastructure_change else 0)
        )

        if risk_score >= 100:

            risk_level = "critical"

        elif risk_score >= 50:

            risk_level = "high"

        elif risk_score >= 20:

            risk_level = "medium"

        else:

            risk_level = "low"

        return ChangeRiskAssessment(

            change_id=
                change_id,

            files_changed=
                files_changed,

            affected_components=
                affected_components,

            database_change=
                database_change,

            infrastructure_change=
                infrastructure_change,

            risk_score=
                risk_score,

            risk_level=
                risk_level
        )
