from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

from sqlalchemy import select

from app.ai.normalizer import normalize_task
from app.models import Reminder, ReminderStatus


class ProactiveSuggester:
    def suggestions_after_create(self, session, *, chat_id: int, created_reminders: list[Reminder], open_reminders: list[Reminder]) -> list[str]:
        suggestions: list[str] = []
        for created in created_reminders:
            if created.next_run_at_utc is not None:
                close = self._find_close_conflict(created, open_reminders)
                if close is not None:
                    suggestions.append(
                        f"You also have '{close.task}' close to that time. If you want, ask me to move one of them."
                    )
            repeat_count = self._count_same_task(session, chat_id=chat_id, task=created.task)
            if repeat_count >= 3:
                suggestions.append(
                    f"You create '{created.task}' often. You can ask me to make it a recurring reminder."
                )
            if created.requires_ack is False and created.recurrence_type == 'once' and created.task.lower() not in {'wake up'}:
                suggestions.append(
                    f"If '{created.task}' is important, you can ask me to add an earlier reminder too."
                )
        return self._dedupe(suggestions)[:2]

    def suggestions_for_agenda(self, reminders: list[Reminder]) -> list[str]:
        if len(reminders) >= 3:
            return [
                'You have several reminders planned. You can send a multi-task message like “Tomorrow remind me about gym at 7, dentist at 11, and call mom in the evening.”'
            ]
        return []

    def suggestions_for_list(self, reminders: list[Reminder]) -> list[str]:
        tasks = [normalize_task(reminder.task) for reminder in reminders if reminder.task]
        counts = Counter(task for task in tasks if task)
        common = [task for task, count in counts.items() if count >= 3]
        if common:
            label = common[0]
            return [f"You have several '{label}' reminders. Ask me to make it recurring if that's a routine."]
        return []

    def _find_close_conflict(self, created: Reminder, open_reminders: list[Reminder]) -> Reminder | None:
        if created.next_run_at_utc is None:
            return None
        for other in open_reminders:
            if other.id == created.id or other.next_run_at_utc is None:
                continue
            delta = abs((other.next_run_at_utc - created.next_run_at_utc).total_seconds())
            if delta <= 60 * 60:
                return other
        return None

    def _count_same_task(self, session, *, chat_id: int, task: str) -> int:
        normalized = normalize_task(task)
        stmt = select(Reminder).where(
            Reminder.chat_id == chat_id,
            Reminder.status.in_([
                ReminderStatus.ACTIVE.value,
                ReminderStatus.PENDING_ACK.value,
                ReminderStatus.COMPLETED.value,
            ]),
        )
        rows = list(session.scalars(stmt).all())
        return sum(1 for row in rows if normalize_task(row.task) == normalized)

    def _dedupe(self, values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            if value not in seen:
                seen.add(value)
                result.append(value)
        return result
