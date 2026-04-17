from __future__ import annotations

import json

from app.models import AiRun


class AiRunRepository:
    def create_ai_run(self, session, **kwargs) -> AiRun:
        ai_run = AiRun(**kwargs)
        session.add(ai_run)
        session.commit()
        session.refresh(ai_run)
        return ai_run

    def update_ai_run(self, session, ai_run: AiRun, **kwargs) -> AiRun:
        for key, value in kwargs.items():
            setattr(ai_run, key, value)
        session.commit()
        session.refresh(ai_run)
        return ai_run

    def get_by_id(self, session, ai_run_id: int) -> AiRun | None:
        return session.get(AiRun, ai_run_id)
