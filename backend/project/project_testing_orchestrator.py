from dataclasses import dataclass


@dataclass
class TestSuite:

    name: str

    tests: list[str]


@dataclass
class TestExecution:

    passed: int

    failed: int

    skipped: int


class ProjectTestingOrchestrator:

    def execute(
        self,
        suite: TestSuite
    ):

        return TestExecution(

            passed=0,

            failed=0,

            skipped=0
        )
