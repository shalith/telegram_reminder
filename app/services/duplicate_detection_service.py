from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import dateparser

from app.ai.normalizer import build_semantic_key, normalize_task
from app.ai.time_normalizer import normalize_time_phrase


class DuplicateDetectionService:
    def _parse_local(self, phrase: str, timezone_name: str) -> datetime | None:
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

    def find_possible_duplicates(self, *, reminders: list, task: str, due_repr: str, recurrence: str | None = None, timezone_name: str = 'Asia/Singapore') -> list:
        normalized = normalize_task(task)
        key = build_semantic_key(task, due_repr, recurrence)
        target_dt = self._parse_local(due_repr, timezone_name) if due_repr else None
        matches = []
        for reminder in reminders:
            reminder_task = normalize_task(reminder.task)
            same_task = bool(normalized and reminder_task and reminder_task == normalized)
            same_key = bool(reminder.semantic_key and reminder.semantic_key == key)
            if same_key:
                matches.append(reminder)
                continue
            if not same_task:
                continue
            if target_dt is None or reminder.next_run_at_utc is None:
                continue
            existing_dt = reminder.next_run_at_utc
            if existing_dt.tzinfo is None:
                existing_dt = existing_dt.replace(tzinfo=ZoneInfo('UTC'))
            existing_local = existing_dt.astimezone(ZoneInfo(timezone_name))
            delta_minutes = abs((existing_local - target_dt).total_seconds()) / 60.0
            if delta_minutes <= 20:
                matches.append(reminder)
        return matches
