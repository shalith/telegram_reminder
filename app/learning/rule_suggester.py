from __future__ import annotations

from app.ai.time_normalizer import normalize_time_phrase
from app.models import LearnedTimePattern


class RuleSuggester:
    def remember_time_phrase(self, session, *, raw_phrase: str) -> LearnedTimePattern | None:
        raw = " ".join((raw_phrase or "").strip().split())
        if not raw:
            return None
        normalized = normalize_time_phrase(raw)
        if normalized == raw:
            return None
        existing = session.query(LearnedTimePattern).filter(LearnedTimePattern.raw_phrase == raw).one_or_none()
        if existing is None:
            existing = LearnedTimePattern(raw_phrase=raw, normalized_phrase=normalized, success_count=1)
            session.add(existing)
        else:
            existing.normalized_phrase = normalized
            existing.success_count += 1
        session.commit()
        session.refresh(existing)
        return existing
