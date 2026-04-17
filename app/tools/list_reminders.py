from __future__ import annotations

from app.recurrence import format_dt_for_user
from app.service import reminder_summary_line


class ListRemindersTool:
    def __init__(self, reminder_service):
        self.reminder_service = reminder_service

    def execute(self, session, *, chat_id: int) -> str:
        reminders = self.reminder_service.list_open_reminders(session, chat_id=chat_id)
        if not reminders:
            return "You do not have any open reminders right now."
        lines = ["Open reminders:"]
        for reminder in reminders:
            when_label = format_dt_for_user(reminder.next_run_at_utc, reminder.timezone) if reminder.next_run_at_utc else "not scheduled"
            lines.append(f"• {reminder_summary_line(reminder, when_label)}")
        return "\n".join(lines)
