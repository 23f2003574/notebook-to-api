from dataclasses import dataclass


@dataclass
class ReliabilityControlCenter:

    recovery: object

    analysis: object

    recommendations: object

    patterns: object

    trends: object

    forecast: object

    scorecard: object

    governance: object

    maturity: object

    roadmap: object


class ReliabilityControlCenterGenerator:

    def generate(
        self,
        recovery,
        analysis,
        recommendations,
        patterns,
        trends,
        forecast,
        scorecard,
        governance,
        maturity,
        roadmap
    ):

        return ReliabilityControlCenter(

            recovery=
                recovery,

            analysis=
                analysis,

            recommendations=
                recommendations,

            patterns=
                patterns,

            trends=
                trends,

            forecast=
                forecast,

            scorecard=
                scorecard,

            governance=
                governance,

            maturity=
                maturity,

            roadmap=
                roadmap
        )