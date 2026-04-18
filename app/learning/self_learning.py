from __future__ import annotations

import json
import re
from dataclasses import dataclass

from sqlalchemy import select

from app.ai.time_normalizer import normalize_time_phrase
from app.learning.confidence_adapter import ConfidenceAdjustment, adapt_confidence
from app.learning.correction_memory import CorrectionMemory
from app.learning.example_memory import ExampleMemoryStore
from app.learning.feedback_store import FeedbackStore
from app.learning.interaction_store import InteractionStore
from app.models import LearnedTimePattern, PhraseRiskScore


_TIME_TOKEN_RE = re.compile(r"\b(?:today|tomorrow|tonight)?\s*(?:morning|afternoon|evening|night)?\s*\d{1,2}(?::\d{2})?\s*(?:am|pm)\b", re.IGNORECASE)
_NUMBER_TOKEN_RE = re.compile(r"\b\d+\b")


@dataclass(slots=True)
class LearningContext:
    prepared_message: str
    positive_examples: list
    risky_examples: list
    interaction_hints: list[str]
    confidence_adjustment: ConfidenceAdjustment
    applied_patterns: list[str]
    risk_score: float
    signature: str


class SelfLearningEngine:
    def __init__(self) -> None:
        self.examples = ExampleMemoryStore()
        self.corrections = CorrectionMemory()
        self.interactions = InteractionStore()
        self.feedback = FeedbackStore()

    @staticmethod
    def _ensure_row_defaults(row: PhraseRiskScore) -> None:
        if row.success_count is None:
            row.success_count = 0
        if row.confirmed_count is None:
            row.confirmed_count = 0
        if row.correction_count is None:
            row.correction_count = 0
        if row.risk_level is None:
            row.risk_level = 0.0

    def prepare(self, session, *, telegram_user_id: int, message_text: str, base_confidence: float = 0.0) -> LearningContext:
        prepared_message, applied = self._apply_learned_time_patterns(session, message_text)
        positive = self.corrections.positive_examples(session, telegram_user_id=telegram_user_id, message_text=prepared_message)
        risky = self.corrections.risky_examples(session, telegram_user_id=telegram_user_id, message_text=prepared_message)
        interaction_hints = [item.outcome for item in self.interactions.find_similar(session, telegram_user_id=telegram_user_id, message_text=prepared_message, limit=3)]
        adjustment = adapt_confidence(base_confidence=base_confidence, positive_examples=positive, risky_examples=risky)
        signature = self.build_signature(prepared_message)
        risk_score = self.lookup_risk_score(session, telegram_user_id=telegram_user_id, signature=signature)
        return LearningContext(
            prepared_message=prepared_message,
            positive_examples=positive,
            risky_examples=risky,
            interaction_hints=interaction_hints,
            confidence_adjustment=adjustment,
            applied_patterns=applied,
            risk_score=risk_score,
            signature=signature,
        )

    def build_signature(self, message_text: str) -> str:
        lowered = (message_text or "").lower().strip()
        lowered = _TIME_TOKEN_RE.sub("<time>", lowered)
        lowered = _NUMBER_TOKEN_RE.sub("<n>", lowered)
        lowered = re.sub(r"\s+", " ", lowered)
        return lowered[:240]

    def lookup_risk_score(self, session, *, telegram_user_id: int, signature: str) -> float:
        row = session.scalar(select(PhraseRiskScore).where(PhraseRiskScore.telegram_user_id == telegram_user_id, PhraseRiskScore.signature == signature))
        if row is None:
            return 0.0
        return float(row.risk_level or 0.0)

    def record_confirmation(self, session, *, telegram_user_id: int, signature: str, confirmed: bool, notes: str | None = None) -> None:
        row = session.scalar(select(PhraseRiskScore).where(PhraseRiskScore.telegram_user_id == telegram_user_id, PhraseRiskScore.signature == signature))
        if row is None:
            row = PhraseRiskScore(telegram_user_id=telegram_user_id, signature=signature)
            session.add(row)
        self._ensure_row_defaults(row)
        if confirmed:
            row.confirmed_count += 1
            row.success_count += 1
        if notes:
            row.notes = notes
        row.risk_level = self._compute_risk(row)
        session.commit()

    def record_correction(self, session, *, telegram_user_id: int, signature: str, notes: str | None = None) -> None:
        row = session.scalar(select(PhraseRiskScore).where(PhraseRiskScore.telegram_user_id == telegram_user_id, PhraseRiskScore.signature == signature))
        if row is None:
            row = PhraseRiskScore(telegram_user_id=telegram_user_id, signature=signature)
            session.add(row)
        self._ensure_row_defaults(row)
        row.correction_count += 1
        if notes:
            row.notes = notes
        row.risk_level = self._compute_risk(row)
        session.commit()

    def record_success(self, session, *, telegram_user_id: int, signature: str, notes: str | None = None) -> None:
        row = session.scalar(select(PhraseRiskScore).where(PhraseRiskScore.telegram_user_id == telegram_user_id, PhraseRiskScore.signature == signature))
        if row is None:
            row = PhraseRiskScore(telegram_user_id=telegram_user_id, signature=signature)
            session.add(row)
        self._ensure_row_defaults(row)
        row.success_count += 1
        if notes:
            row.notes = notes
        row.risk_level = self._compute_risk(row)
        session.commit()

    def _compute_risk(self, row: PhraseRiskScore) -> float:
        self._ensure_row_defaults(row)
        total = max(1, row.success_count + row.confirmed_count + row.correction_count)
        risk = (row.correction_count * 1.5) / total
        if row.confirmed_count and row.correction_count == 0:
            risk *= 0.6
        return max(0.0, min(1.0, risk))

    def _apply_learned_time_patterns(self, session, message_text: str) -> tuple[str, list[str]]:
        cleaned = " ".join((message_text or "").strip().split())
        applied: list[str] = []
        rows = list(session.scalars(select(LearnedTimePattern).order_by(LearnedTimePattern.success_count.desc()).limit(100)).all())
        lowered = cleaned.lower()
        for row in rows:
            raw = row.raw_phrase.lower().strip()
            if raw and raw in lowered and row.normalized_phrase:
                cleaned = re.sub(re.escape(row.raw_phrase), row.normalized_phrase, cleaned, flags=re.IGNORECASE)
                lowered = cleaned.lower()
                applied.append(f"{row.raw_phrase}->{row.normalized_phrase}")
        return cleaned, applied
