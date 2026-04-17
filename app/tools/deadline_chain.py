from __future__ import annotations

from app.assistant_features import format_offset_label, parse_deadline_phrase


class DeadlineChainTool:
    def __init__(self, reminder_service):
        self.reminder_service = reminder_service

    def execute(self, session, *, scheduler, incoming_text: str, telegram_user_id: int, chat_id: int, timezone_name: str, task: str, deadline_phrase: str, offsets: list):
        deadline_utc = parse_deadline_phrase(deadline_phrase, timezone_name)
        if deadline_utc is None:
            return [], "I couldn't understand that deadline time."
        reminders = self.reminder_service.create_deadline_chain(
            session,
            telegram_user_id=telegram_user_id,
            chat_id=chat_id,
            task=task,
            original_text=incoming_text,
            deadline_utc=deadline_utc,
            offsets=offsets,
            timezone_name=timezone_name,
        )
        if not reminders:
            return [], "All of those reminder times are already in the past."
        for reminder in reminders:
            scheduler.schedule_reminder(reminder.id, reminder.next_run_at_utc, reminder.job_id)
        labels = ", ".join(format_offset_label(offset) for offset in offsets)
        return reminders, f"Created {len(reminders)} deadline reminders for {task}: {labels}."
