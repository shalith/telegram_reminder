from __future__ import annotations

from app.recurrence import format_dt_for_user
from app.service import reminder_summary_line


class TodayAgendaTool:
    def __init__(self, reminder_service):
        self.reminder_service = reminder_service

    def execute(self, session, *, chat_id: int, timezone_name: str) -> str:
        reminders = self.reminder_service.list_today_reminders(session, chat_id=chat_id, timezone_name=timezone_name)
        if not reminders:
            return "You have no upcoming reminders for today."
        lines = ["Today's agenda:"]
        for reminder in reminders:
            when_label = format_dt_for_user(reminder.next_run_at_utc, reminder.timezone) if reminder.next_run_at_utc else "not scheduled"
            lines.append(f"• {reminder_summary_line(reminder, when_label)}")
        return "\n".join(lines)
