from __future__ import annotations

import json

from app.models import InteractionFeedback


class FeedbackStore:
    def record(
        self,
        session,
        *,
        chat_id: int,
        telegram_user_id: int,
        message_text: str,
        phase: str,
        outcome: str,
        error_code: str | None = None,
        details: dict | None = None,
    ) -> InteractionFeedback:
        row = InteractionFeedback(
            chat_id=chat_id,
            telegram_user_id=telegram_user_id,
            message_text=message_text,
            phase=phase,
            outcome=outcome,
            error_code=error_code,
            details_json=json.dumps(details or {}, ensure_ascii=False),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row
