from dataclasses import dataclass


@dataclass
class RegressionTestSuite:

    suite_name: str

    test_count: int

    compatibility_validation: bool

    release_blocking: bool


class RegressionTestingEngine:

    def generate(
        self
    ):

        return RegressionTestSuite(

            suite_name=
                "api_regression_suite",

            test_count=
                50,

            compatibility_validation=
                True,

            release_blocking=
                True
        )
