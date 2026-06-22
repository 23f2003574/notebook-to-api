from dataclasses import dataclass


@dataclass
class TestCoverage:

    endpoint_coverage_percent: float

    test_case_count: int

    covered_endpoints: int

    uncovered_endpoints: int


class TestCoverageEngine:

    def generate(
        self
    ):

        return TestCoverage(

            endpoint_coverage_percent=
                95.0,

            test_case_count=
                25,

            covered_endpoints=
                19,

            uncovered_endpoints=
                1
        )
