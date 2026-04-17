from __future__ import annotations

from sqlalchemy import select

from app.models import EvalCase


class EvalRepository:
    def list_active_eval_cases(self, session=None) -> list[EvalCase]:
        if session is None:
            return []
        stmt = select(EvalCase).where(EvalCase.active.is_(True)).order_by(EvalCase.id.asc())
        return list(session.scalars(stmt).all())
