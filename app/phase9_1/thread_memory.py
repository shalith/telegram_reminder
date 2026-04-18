from __future__ import annotations

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.models import ConversationState


class ThreadConversationState(BaseModel):
    mode: str = 'general_chat'
    status: str = 'idle'
    turns: int = 0
    draft_action: str | None = None
    draft_summary: str | None = None
    last_bot_prompt: str | None = None
    last_user_message: str | None = None


class ThreadMemoryStore:
    PENDING_INTENT = 'phase9_thread'

    def get(self, session, *, chat_id: int) -> ThreadConversationState | None:
        row = session.scalar(select(ConversationState).where(ConversationState.chat_id == chat_id))
        if row is None or row.pending_intent != self.PENDING_INTENT:
            return None
        try:
            return ThreadConversationState.model_validate_json(row.state_json)
        except Exception:
            return None

    def save(self, session, *, chat_id: int, telegram_user_id: int, state: ThreadConversationState) -> None:
        row = session.scalar(select(ConversationState).where(ConversationState.chat_id == chat_id))
        payload = state.model_dump_json()
        if row is None:
            row = ConversationState(chat_id=chat_id, telegram_user_id=telegram_user_id, pending_intent=self.PENDING_INTENT, state_json=payload)
            session.add(row)
        else:
            row.telegram_user_id = telegram_user_id
            row.pending_intent = self.PENDING_INTENT
            row.state_json = payload
        session.commit()

    def clear(self, session, *, chat_id: int) -> None:
        row = session.scalar(select(ConversationState).where(ConversationState.chat_id == chat_id))
        if row is not None and row.pending_intent == self.PENDING_INTENT:
            session.delete(row)
            session.commit()
