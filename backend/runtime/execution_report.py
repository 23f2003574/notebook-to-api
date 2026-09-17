from dataclasses import dataclass
from typing import List


@dataclass
class StageExecutionResult:

    stage_name: str

    success: bool

    retry_count: int = 0

    error: str | None = None


@dataclass
class ExecutionReport:

    stages: List[
        StageExecutionResult
    ]

    total_stages: int

    successful_stages: int

    failed_stages: int