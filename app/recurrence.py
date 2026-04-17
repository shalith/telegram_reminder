from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.models import RecurrenceType, Reminder

WEEKDAY_NAMES = {
    0: "Monday",
    1: "Tuesday",
    2: "Wednesday",
    3: "Thursday",
    4: "Friday",
    5: "Saturday",
    6: "Sunday",
}


def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def compute_next_occurrence_utc(
    *,
    recurrence_type: str,
    timezone_name: str,
    hour_local: int | None,
    minute_local: int | None,
    recurrence_day_of_week: int | None,
    after_utc: datetime | None = None,
) -> datetime | None:
    if recurrence_type == RecurrenceType.ONCE.value:
        return None
    if hour_local is None or minute_local is None:
        return None

    tz = ZoneInfo(timezone_name)
    local_now = ensure_utc(after_utc or datetime.now(UTC)).astimezone(tz)

    if recurrence_type == RecurrenceType.DAILY.value:
        candidate = local_now.replace(hour=hour_local, minute=minute_local, second=0, microsecond=0)
        if candidate <= local_now:
            candidate = candidate + timedelta(days=1)
        return candidate.astimezone(UTC)

    if recurrence_type == RecurrenceType.WEEKDAY.value:
        for offset in range(0, 8):
            day = local_now + timedelta(days=offset)
            candidate = day.replace(hour=hour_local, minute=minute_local, second=0, microsecond=0)
            if candidate.weekday() < 5 and candidate > local_now:
                return candidate.astimezone(UTC)
        return None

    if recurrence_type == RecurrenceType.WEEKLY.value:
        if recurrence_day_of_week is None:
            return None
        days_ahead = (recurrence_day_of_week - local_now.weekday()) % 7
        candidate = (local_now + timedelta(days=days_ahead)).replace(
            hour=hour_local,
            minute=minute_local,
            second=0,
            microsecond=0,
        )
        if candidate <= local_now:
            candidate = candidate + timedelta(days=7)
        return candidate.astimezone(UTC)

    return None


def format_dt_for_user(dt_utc_naive: datetime, timezone_name: str) -> str:
    tz = ZoneInfo(timezone_name)
    aware_utc = dt_utc_naive.replace(tzinfo=UTC)
    local_dt = aware_utc.astimezone(tz)
    return local_dt.strftime("%a, %d %b %Y at %I:%M %p")


def recurrence_label(reminder: Reminder) -> str:
    if reminder.recurrence_type == RecurrenceType.ONCE.value:
        return "one-time"
    if reminder.recurrence_type == RecurrenceType.DAILY.value:
        return "daily"
    if reminder.recurrence_type == RecurrenceType.WEEKDAY.value:
        return "weekdays"
    if reminder.recurrence_type == RecurrenceType.WEEKLY.value and reminder.recurrence_day_of_week is not None:
        return f"every {WEEKDAY_NAMES.get(reminder.recurrence_day_of_week, 'week')}"
    return reminder.recurrence_type
