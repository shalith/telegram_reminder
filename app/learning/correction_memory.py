from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from sqlalchemy import desc, select

from app.models import CorrectionExample


@dataclass(slots=True)
class SimilarCorrection:
    score: float
    example: CorrectionExample


class CorrectionMemory:
    def find_similar(self, session, *, telegram_user_id: int, message_text: str, limit: int = 5) -> list[SimilarCorrection]:
        stmt = (
            select(CorrectionExample)
            .where(CorrectionExample.telegram_user_id == telegram_user_id)
            .order_by(desc(CorrectionExample.created_at))
            .limit(100)
        )
        rows = list(session.scalars(stmt).all())
        needle = (message_text or "").lower().strip()
        scored: list[SimilarCorrection] = []
        for row in rows:
            hay = (row.source_text or "").lower().strip()
            score = SequenceMatcher(None, needle, hay).ratio()
            if needle and needle in hay:
                score += 0.15
            if score >= 0.42:
                scored.append(SimilarCorrection(score=score, example=row))
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:limit]

    def risky_examples(self, session, *, telegram_user_id: int, message_text: str, limit: int = 3) -> list[SimilarCorrection]:
        return [item for item in self.find_similar(session, telegram_user_id=telegram_user_id, message_text=message_text, limit=limit * 3) if item.example.notes and any(token in item.example.notes for token in ("corrected", "repair", "risky", "ambiguous"))][:limit]

    def positive_examples(self, session, *, telegram_user_id: int, message_text: str, limit: int = 3) -> list[SimilarCorrection]:
        return [item for item in self.find_similar(session, telegram_user_id=telegram_user_id, message_text=message_text, limit=limit * 3) if not item.example.notes or any(token in item.example.notes for token in ("confirmed", "accepted", "success"))][:limit]
