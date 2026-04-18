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
    last_discussed_task: str | None = None
    last_discussed_time_phrase: str | None = None
    last_created_reminder_id: int | None = None
    last_listed_reminder_ids: list[int] = Field(default_factory=list)
    last_referenced_reminder_id: int | None = None


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

    def clear(self, session, *, chat_id: int, preserve_references: bool = True) -> None:
        row = session.scalar(select(ConversationState).where(ConversationState.chat_id == chat_id))
        if row is not None and row.pending_intent == self.PENDING_INTENT:
            if preserve_references:
                try:
                    state = ThreadConversationState.model_validate_json(row.state_json)
                except Exception:
                    state = ThreadConversationState()
                state.mode = 'general_chat'
                state.status = 'idle'
                state.turns = 0
                state.draft_action = None
                state.draft_summary = None
                state.last_bot_prompt = None
                state.last_user_message = None
                row.state_json = state.model_dump_json()
                session.commit()
            else:
                session.delete(row)
                session.commit()

    def remember_reference(self, session, *, chat_id: int, telegram_user_id: int, task: str | None = None, time_phrase: str | None = None, created_reminder_id: int | None = None, listed_reminder_ids: list[int] | None = None, referenced_reminder_id: int | None = None) -> None:
        state = self.get(session, chat_id=chat_id) or ThreadConversationState()
        if task is not None:
            state.last_discussed_task = task
        if time_phrase is not None:
            state.last_discussed_time_phrase = time_phrase
        if created_reminder_id is not None:
            state.last_created_reminder_id = created_reminder_id
            state.last_referenced_reminder_id = created_reminder_id
        if listed_reminder_ids is not None:
            state.last_listed_reminder_ids = listed_reminder_ids
        if referenced_reminder_id is not None:
            state.last_referenced_reminder_id = referenced_reminder_id
        self.save(session, chat_id=chat_id, telegram_user_id=telegram_user_id, state=state)
