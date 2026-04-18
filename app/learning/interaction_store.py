from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from sqlalchemy import desc, select

from app.models import InteractionFeedback


@dataclass(slots=True)
class SimilarFeedback:
    score: float
    phase: str
    outcome: str
    message_text: str
    details_json: str | None


class InteractionStore:
    def find_similar(self, session, *, telegram_user_id: int, message_text: str, limit: int = 5) -> list[SimilarFeedback]:
        stmt = (
            select(InteractionFeedback)
            .where(InteractionFeedback.telegram_user_id == telegram_user_id)
            .order_by(desc(InteractionFeedback.created_at))
            .limit(100)
        )
        rows = list(session.scalars(stmt).all())
        needle = (message_text or "").lower().strip()
        scored: list[SimilarFeedback] = []
        for row in rows:
            hay = (row.message_text or "").lower().strip()
            score = SequenceMatcher(None, needle, hay).ratio()
            if needle and needle in hay:
                score += 0.15
            if score >= 0.45:
                scored.append(SimilarFeedback(score=score, phase=row.phase, outcome=row.outcome, message_text=row.message_text, details_json=row.details_json))
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:limit]
