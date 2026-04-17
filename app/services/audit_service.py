from __future__ import annotations

from app.models import ActionAuditLog
from app.repositories.ai_run_repo import AiRunRepository


class AuditService:
    def __init__(self):
        self.ai_runs = AiRunRepository()

    def record_ai_run(self, session, **kwargs):
        return self.ai_runs.create_ai_run(session, **kwargs)

    def update_ai_run(self, session, ai_run, **kwargs):
        return self.ai_runs.update_ai_run(session, ai_run, **kwargs)

    def record_action(self, session, *, user_id: int, reminder_id: int | None, action_name: str, action_args_json: str | None, executor_result_json: str | None, status: str) -> ActionAuditLog:
        row = ActionAuditLog(
            user_id=user_id,
            reminder_id=reminder_id,
            action_name=action_name,
            action_args_json=action_args_json,
            executor_result_json=executor_result_json,
            status=status,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row
