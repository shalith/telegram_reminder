from __future__ import annotations


class DeleteReminderTool:
    def __init__(self, reminder_service):
        self.reminder_service = reminder_service

    def execute(self, session, *, scheduler, chat_id: int, reminder) -> tuple[object | None, str]:
        scheduler.remove_reminder_job(reminder.job_id)
        deleted = self.reminder_service.delete_reminder(session, chat_id=chat_id, reminder_id=reminder.id)
        if deleted is None:
            return None, "I couldn't cancel that reminder."
        return deleted, "ok"
