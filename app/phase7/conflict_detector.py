from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable
from zoneinfo import ZoneInfo

import dateparser

from app.ai.normalizer import normalize_task
from app.ai.schemas import InterpretationEnvelope
from app.ai.time_normalizer import normalize_time_phrase
from app.models import Reminder, ReminderStatus


@dataclass(slots=True)
class ConflictItem:
    code: str
    severity: str
    message: str
    reminder_id: int | None = None


class SemanticConflictDetector:
    def __init__(self, *, overlap_minutes: int = 30, duplicate_task_window_minutes: int = 120) -> None:
        self.overlap_minutes = overlap_minutes
        self.duplicate_task_window_minutes = duplicate_task_window_minutes

    def detect(self, *, envelope: InterpretationEnvelope, open_reminders: Iterable[Reminder], timezone_name: str) -> list[ConflictItem]:
        if envelope.action not in {"create_reminder", "update_reminder", "deadline_chain"}:
            return []
        if not envelope.reminder.datetime_text:
            return []

        parsed_dt = self._parse_phrase(envelope.reminder.datetime_text, timezone_name)
        if parsed_dt is None:
            return []

        conflicts: list[ConflictItem] = []
        normalized_task = normalize_task(envelope.reminder.task or "")
        target_selector_id = envelope.target.reminder_id

        for reminder in open_reminders:
            if reminder.status not in {ReminderStatus.ACTIVE.value, ReminderStatus.PENDING_ACK.value}:
                continue
            if target_selector_id is not None and reminder.id == target_selector_id:
                continue
            if reminder.next_run_at_utc is None:
                continue

            reminder_dt = reminder.next_run_at_utc
            if reminder_dt.tzinfo is None:
                reminder_dt = reminder_dt.replace(tzinfo=ZoneInfo('UTC'))
            reminder_local = reminder_dt.astimezone(ZoneInfo(timezone_name))
            delta_minutes = abs((reminder_local - parsed_dt).total_seconds()) / 60.0

            if delta_minutes <= self.overlap_minutes:
                conflicts.append(
                    ConflictItem(
                        code='time_overlap',
                        severity='high' if delta_minutes <= 5 else 'medium',
                        message=f"You already have reminder #{reminder.id} around that time: {reminder.task}.",
                        reminder_id=reminder.id,
                    )
                )

            reminder_task = normalize_task(reminder.task or "")
            if normalized_task and normalized_task == reminder_task and delta_minutes <= self.duplicate_task_window_minutes:
                conflicts.append(
                    ConflictItem(
                        code='possible_duplicate',
                        severity='high',
                        message=f"This looks similar to reminder #{reminder.id}: {reminder.task}.",
                        reminder_id=reminder.id,
                    )
                )

            if envelope.reminder.is_wake_up and 0 <= (reminder_local - parsed_dt).total_seconds() / 60.0 <= 20:
                conflicts.append(
                    ConflictItem(
                        code='wake_up_tight_spacing',
                        severity='medium',
                        message=f"Reminder #{reminder.id} is very close after that wake-up time.",
                        reminder_id=reminder.id,
                    )
                )

        return self._dedupe(conflicts)

    def _parse_phrase(self, phrase: str, timezone_name: str) -> datetime | None:
        parsed = dateparser.parse(
            normalize_time_phrase(phrase),
            settings={
                'PREFER_DATES_FROM': 'future',
                'TIMEZONE': timezone_name,
                'RETURN_AS_TIMEZONE_AWARE': True,
            },
        )
        if parsed is None:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
        return parsed.astimezone(ZoneInfo(timezone_name))

    def _dedupe(self, items: list[ConflictItem]) -> list[ConflictItem]:
        seen: set[tuple[str, int | None]] = set()
        result: list[ConflictItem] = []
        for item in items:
            key = (item.code, item.reminder_id)
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
        return result
