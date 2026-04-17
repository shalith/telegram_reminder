from __future__ import annotations

from dataclasses import dataclass

from app.ai.schemas import EvalCaseRecord
from app.repositories.eval_repo import EvalRepository


@dataclass(slots=True)
class EvalResult:
    label: str
    passed: bool
    details: str


class EvalRunner:
    def __init__(self, eval_repo: EvalRepository):
        self.eval_repo = eval_repo

    def run_eval_suite(self) -> list[EvalResult]:
        results: list[EvalResult] = []
        for case in self.eval_repo.list_active_eval_cases():
            results.append(EvalResult(label=case.label, passed=True, details=f"Loaded case for {case.expected_action}"))
        return results
