from __future__ import annotations

from difflib import SequenceMatcher

from sqlalchemy import desc, select

from app.models import CorrectionExample


class ExampleMemoryStore:
    def remember(
        self,
        session,
        *,
        chat_id: int,
        telegram_user_id: int,
        source_text: str,
        action_name: str,
        resolved_task: str | None,
        resolved_time_phrase: str | None,
        learned_from_follow_up: bool,
        notes: str | None = None,
    ) -> CorrectionExample:
        row = CorrectionExample(
            chat_id=chat_id,
            telegram_user_id=telegram_user_id,
            source_text=source_text,
            action_name=action_name,
            resolved_task=resolved_task,
            resolved_time_phrase=resolved_time_phrase,
            learned_from_follow_up=learned_from_follow_up,
            notes=notes,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row

    def find_similar(self, session, *, telegram_user_id: int, message_text: str, limit: int = 3) -> list[CorrectionExample]:
        stmt = (
            select(CorrectionExample)
            .where(CorrectionExample.telegram_user_id == telegram_user_id)
            .order_by(desc(CorrectionExample.created_at))
            .limit(50)
        )
        examples = list(session.scalars(stmt).all())
        if not examples:
            return []
        scored: list[tuple[float, CorrectionExample]] = []
        needle = (message_text or "").lower().strip()
        for example in examples:
            hay = (example.source_text or "").lower().strip()
            score = SequenceMatcher(None, needle, hay).ratio()
            if needle and needle in hay:
                score += 0.15
            scored.append((score, example))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [example for score, example in scored[:limit] if score >= 0.35]

    def format_for_prompt(self, examples: list[CorrectionExample]) -> list[str]:
        lines: list[str] = []
        for example in examples:
            pieces = [f"user='{example.source_text}'", f"action={example.action_name}"]
            if example.resolved_task:
                pieces.append(f"task='{example.resolved_task}'")
            if example.resolved_time_phrase:
                pieces.append(f"time='{example.resolved_time_phrase}'")
            if example.learned_from_follow_up:
                pieces.append("from_follow_up=true")
            lines.append(" | ".join(pieces))
        return lines
