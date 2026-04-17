from __future__ import annotations

from app.ai.normalizer import build_semantic_key, normalize_task
from app.parser import parse_schedule_components


class UpdateReminderTool:
    def __init__(self, reminder_service):
        self.reminder_service = reminder_service

    def execute(
        self,
        session,
        *,
        scheduler,
        reminder,
        incoming_text: str,
        timezone_name: str,
        time_phrase: str,
        retry_interval_minutes: int,
        max_attempts: int,
        source_mode: str,
        interpretation_json: str | None,
        target_selector_json: str | None,
        ai_confidence: float,
    ) -> tuple[object | None, str]:
        parsed = parse_schedule_components(
            task=reminder.task,
            time_phrase=time_phrase,
            timezone_name=timezone_name,
            requires_ack=reminder.requires_ack,
            retry_interval_minutes=retry_interval_minutes if reminder.requires_ack else reminder.retry_interval_minutes,
            max_attempts=max_attempts if reminder.requires_ack else reminder.max_attempts,
        )
        if not parsed.ok or parsed.next_run_at_utc is None:
            return None, parsed.error or "I couldn't update that reminder."
        scheduler.remove_reminder_job(reminder.job_id)
        updated = self.reminder_service.update_reminder_schedule(
            session,
            reminder=reminder,
            original_text=incoming_text,
            next_run_at_utc=parsed.next_run_at_utc,
            recurrence_type=parsed.recurrence_type,
            recurrence_day_of_week=parsed.recurrence_day_of_week,
            hour_local=parsed.hour_local,
            minute_local=parsed.minute_local,
            requires_ack=parsed.requires_ack,
            retry_interval_minutes=parsed.retry_interval_minutes,
            max_attempts=parsed.max_attempts,
        )
        updated.normalized_task = normalize_task(updated.task)
        updated.semantic_key = build_semantic_key(updated.task, time_phrase, updated.recurrence_type)
        updated.last_ai_confidence = ai_confidence
        updated.last_interpretation_json = interpretation_json
        updated.last_target_selector_json = target_selector_json
        updated.source_mode = source_mode
        session.commit()
        session.refresh(updated)
        scheduler.schedule_reminder(updated.id, updated.next_run_at_utc, updated.job_id)
        return updated, "ok"
