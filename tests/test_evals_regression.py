from app.ai.evals import EvalRunner
from app.repositories.eval_repo import EvalRepository


def test_eval_runner_handles_empty_repo():
    runner = EvalRunner(EvalRepository())
    assert runner.run_eval_suite() == []
