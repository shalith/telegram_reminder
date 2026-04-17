from __future__ import annotations

import json

from app.ai.normalizer import build_semantic_key, normalize_task
from app.models import Reminder
from app.parser import parse_schedule_components
from app.service import ReminderService


class CreateReminderTool:
    def __init__(self, reminder_service: ReminderService):
        self.reminder_service = reminder_service

    def execute(
        self,
        session,
        *,
        scheduler,
        incoming_text: str,
        telegram_user_id: int,
        chat_id: int,
        timezone_name: str,
        task: str,
        time_phrase: str,
        requires_ack: bool,
        retry_interval_minutes: int,
        max_attempts: int,
        source_mode: str,
        interpretation_json: str | None,
        target_selector_json: str | None,
        ai_confidence: float,
    ) -> tuple[Reminder | None, str]:
        parsed = parse_schedule_components(
            task=task,
            time_phrase=time_phrase,
            timezone_name=timezone_name,
            requires_ack=requires_ack,
            retry_interval_minutes=retry_interval_minutes,
            max_attempts=max_attempts,
        )
        if not parsed.ok or parsed.next_run_at_utc is None:
            return None, parsed.error or "I couldn't create that reminder."

        reminder = self.reminder_service.create_reminder(
            session,
            telegram_user_id=telegram_user_id,
            chat_id=chat_id,
            task=parsed.task or task,
            original_text=incoming_text,
            next_run_at_utc=parsed.next_run_at_utc,
            timezone_name=timezone_name,
            recurrence_type=parsed.recurrence_type,
            recurrence_day_of_week=parsed.recurrence_day_of_week,
            hour_local=parsed.hour_local,
            minute_local=parsed.minute_local,
            requires_ack=parsed.requires_ack,
            retry_interval_minutes=parsed.retry_interval_minutes,
            max_attempts=parsed.max_attempts,
        )
        reminder.normalized_task = normalize_task(reminder.task)
        reminder.semantic_key = build_semantic_key(reminder.task, time_phrase, reminder.recurrence_type)
        reminder.last_ai_confidence = ai_confidence
        reminder.last_interpretation_json = interpretation_json
        reminder.last_target_selector_json = target_selector_json
        reminder.source_mode = source_mode
        session.commit()
        session.refresh(reminder)
        scheduler.schedule_reminder(reminder.id, reminder.next_run_at_utc, reminder.job_id)
        return reminder, "ok"
