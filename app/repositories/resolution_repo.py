from __future__ import annotations

from sqlalchemy import select

from app.models import TargetResolutionCandidate


class ResolutionRepository:
    def save_candidates(self, session, *, ai_run_id: int, action_name: str, candidates: list) -> list[TargetResolutionCandidate]:
        rows: list[TargetResolutionCandidate] = []
        for candidate in candidates:
            row = TargetResolutionCandidate(
                ai_run_id=ai_run_id,
                reminder_id=candidate.reminder.id,
                score=candidate.score,
                match_reason=candidate.reason,
                selected=False,
                action_name=action_name,
            )
            session.add(row)
            rows.append(row)
        session.commit()
        for row in rows:
            session.refresh(row)
        return rows

    def mark_selected(self, session, *, ai_run_id: int, reminder_id: int) -> None:
        stmt = select(TargetResolutionCandidate).where(TargetResolutionCandidate.ai_run_id == ai_run_id)
        rows = list(session.scalars(stmt).all())
        for row in rows:
            row.selected = row.reminder_id == reminder_id
        session.commit()
