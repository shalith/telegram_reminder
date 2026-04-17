from __future__ import annotations

from app.assistant_features import parse_daily_agenda_time


class SetPreferencesTool:
    def __init__(self, reminder_service):
        self.reminder_service = reminder_service

    def execute(self, session, *, scheduler, preference, preferences_patch, timezone_name: str) -> str:
        updates: dict[str, object] = {}
        if preferences_patch.snooze_minutes is not None:
            updates["default_snooze_minutes"] = preferences_patch.snooze_minutes
        if preferences_patch.wake_retry_minutes is not None:
            updates["wakeup_retry_interval_minutes"] = preferences_patch.wake_retry_minutes
        if preferences_patch.wake_max_attempts is not None:
            updates["wakeup_max_attempts"] = preferences_patch.wake_max_attempts
        if preferences_patch.daily_agenda_enabled is not None:
            updates["daily_agenda_enabled"] = preferences_patch.daily_agenda_enabled
        if preferences_patch.missed_summary_enabled is not None:
            updates["missed_summary_enabled"] = preferences_patch.missed_summary_enabled
        if preferences_patch.daily_agenda_time is not None:
            parsed = parse_daily_agenda_time(preferences_patch.daily_agenda_time, timezone_name)
            if parsed is None:
                return "I couldn't understand that daily agenda time."
            updates["daily_agenda_enabled"] = True
            updates["daily_agenda_hour_local"] = parsed.hour_local
            updates["daily_agenda_minute_local"] = parsed.minute_local
        if not updates:
            return "I couldn't find a preference change in that message."
        updated = self.reminder_service.update_preferences(session, preference=preference, updates=updates)
        if updated.daily_agenda_enabled and updated.daily_agenda_hour_local is not None and updated.daily_agenda_minute_local is not None:
            scheduler.schedule_daily_agenda(chat_id=updated.chat_id, hour_local=updated.daily_agenda_hour_local, minute_local=updated.daily_agenda_minute_local, timezone_name=updated.timezone)
        else:
            scheduler.remove_daily_agenda_job(chat_id=updated.chat_id)
        return self.reminder_service.format_preferences_summary(updated)
