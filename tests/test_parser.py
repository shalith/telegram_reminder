from __future__ import annotations

from app.models import RecurrenceType
from app.parser import parse_reminder_text, parse_schedule_components


TIMEZONE = "Asia/Singapore"


def test_parse_standard_one_time_reminder() -> None:
    result = parse_reminder_text(
        "Remind me tomorrow at 7 PM to pay rent",
        TIMEZONE,
    )
    assert result.ok is True
    assert result.task == "pay rent"
    assert result.next_run_at_utc is not None
    assert result.recurrence_type == RecurrenceType.ONCE.value
    assert result.requires_ack is False


def test_parse_daily_recurring_reminder() -> None:
    result = parse_reminder_text("Remind me every day at 8 AM to check my tasks", TIMEZONE)
    assert result.ok is True
    assert result.task == "check my tasks"
    assert result.next_run_at_utc is not None
    assert result.recurrence_type == RecurrenceType.DAILY.value
    assert result.hour_local == 8
    assert result.minute_local == 0


def test_parse_weekday_wake_up_reminder() -> None:
    result = parse_reminder_text("Wake me up every weekday at 6 AM", TIMEZONE)
    assert result.ok is True
    assert result.task == "wake up"
    assert result.next_run_at_utc is not None
    assert result.recurrence_type == RecurrenceType.WEEKDAY.value
    assert result.requires_ack is True


def test_parse_every_monday_leading_phrase() -> None:
    result = parse_reminder_text("Every Monday at 9 AM remind me to submit the report", TIMEZONE)
    assert result.ok is True
    assert result.task == "submit the report"
    assert result.recurrence_type == RecurrenceType.WEEKLY.value
    assert result.recurrence_day_of_week == 0


def test_parse_components_for_update() -> None:
    result = parse_schedule_components(task="wake up", time_phrase="every day at 6 AM", timezone_name=TIMEZONE, requires_ack=True)
    assert result.ok is True
    assert result.requires_ack is True
    assert result.recurrence_type == RecurrenceType.DAILY.value


def test_invalid_format_returns_error() -> None:
    result = parse_reminder_text("Remind me tomorrow at 7 PM pay rent", TIMEZONE)
    assert result.ok is False
    assert result.error is not None
