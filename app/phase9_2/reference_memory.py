from __future__ import annotations

import json
from dataclasses import dataclass, field

from sqlalchemy import select

from app.models import ChatReferenceMemory


@dataclass(slots=True)
class ChatReferenceState:
    last_discussed_task: str | None = None
    last_discussed_time_phrase: str | None = None
    last_created_reminder_id: int | None = None
    last_listed_reminder_ids: list[int] = field(default_factory=list)
    last_referenced_reminder_id: int | None = None


class ReferenceMemoryStore:
    def get(self, session, *, chat_id: int) -> ChatReferenceState:
        row = session.scalar(select(ChatReferenceMemory).where(ChatReferenceMemory.chat_id == chat_id))
        if row is None:
            return ChatReferenceState()
        try:
            listed = json.loads(row.last_listed_reminder_ids_json) if row.last_listed_reminder_ids_json else []
            if not isinstance(listed, list):
                listed = []
        except Exception:
            listed = []
        return ChatReferenceState(
            last_discussed_task=row.last_discussed_task,
            last_discussed_time_phrase=row.last_discussed_time_phrase,
            last_created_reminder_id=row.last_created_reminder_id,
            last_listed_reminder_ids=[int(x) for x in listed if isinstance(x, int) or (isinstance(x, str) and str(x).isdigit())],
            last_referenced_reminder_id=row.last_referenced_reminder_id,
        )

    def save(self, session, *, chat_id: int, telegram_user_id: int, state: ChatReferenceState) -> None:
        row = session.scalar(select(ChatReferenceMemory).where(ChatReferenceMemory.chat_id == chat_id))
        if row is None:
            row = ChatReferenceMemory(chat_id=chat_id, telegram_user_id=telegram_user_id)
            session.add(row)
        row.telegram_user_id = telegram_user_id
        row.last_discussed_task = state.last_discussed_task
        row.last_discussed_time_phrase = state.last_discussed_time_phrase
        row.last_created_reminder_id = state.last_created_reminder_id
        row.last_listed_reminder_ids_json = json.dumps(state.last_listed_reminder_ids)
        row.last_referenced_reminder_id = state.last_referenced_reminder_id
        session.commit()

    def remember(self, session, *, chat_id: int, telegram_user_id: int, task: str | None = None, time_phrase: str | None = None, created_reminder_id: int | None = None, listed_reminder_ids: list[int] | None = None, referenced_reminder_id: int | None = None) -> ChatReferenceState:
        state = self.get(session, chat_id=chat_id)
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
        return state
