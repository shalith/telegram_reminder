from __future__ import annotations


class MissedSummaryTool:
    def __init__(self, reminder_service):
        self.reminder_service = reminder_service

    def execute(self, session, *, chat_id: int) -> str:
        reminders = self.reminder_service.list_missed_reminders(session, chat_id=chat_id)
        if not reminders:
            return "You do not have any missed reminders."
        lines = ["Missed reminders:"]
        for reminder in reminders[:20]:
            lines.append(f"• #{reminder.id} — {reminder.task}")
        return "\n".join(lines)
