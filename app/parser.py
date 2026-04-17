from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import dateparser

from app.ai.time_normalizer import looks_like_time_phrase, normalize_time_phrase
from app.models import RecurrenceType
from app.recurrence import compute_next_occurrence_utc


@dataclass(slots=True)
class ParseResult:
    ok: bool
    task: str | None = None
    next_run_at_utc: datetime | None = None
    recurrence_type: str = RecurrenceType.ONCE.value
    recurrence_day_of_week: int | None = None
    requires_ack: bool = False
    retry_interval_minutes: int = 2
    max_attempts: int = 10
    hour_local: int | None = None
    minute_local: int | None = None
    error: str | None = None


REMIND_ME_PREFIX = re.compile(r"^\s*remind me\s+", re.IGNORECASE)
WAKE_ME_UP_PREFIX = re.compile(r"^\s*wake me up\s+", re.IGNORECASE)
LEADING_EVERY_PREFIX = re.compile(r"^\s*(every\s+.+?|daily\s+at\s+.+?)\s+remind me\s+to\s+(.+)$", re.IGNORECASE)
WEEKDAY_NAME_TO_INT = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
WEEKDAY_NAME_RE = r"monday|tuesday|wednesday|thursday|friday|saturday|sunday"
MONTH_NAME_RE = r"jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|sept|september|oct|october|nov|november|dec|december"
TIME_ANCHOR_RE = re.compile(
    rf"\b(today|tomorrow|tonight|next\s+(?:{WEEKDAY_NAME_RE})|this\s+(?:{WEEKDAY_NAME_RE})|every\s+(?:day|weekday|{WEEKDAY_NAME_RE})|in\s+\d+\s+(?:minutes?|hours?|days?)|(?:\d{{1,2}})(?:st|nd|rd|th)?\s+(?:{MONTH_NAME_RE})(?:\s+\d{{4}})?|morning|afternoon|evening|night|\d{{1,2}}(?::\d{{2}})?\s*(?:am|pm))\b",
    re.IGNORECASE,
)


def parse_reminder_text(text: str, timezone_name: str) -> ParseResult:
    normalized = " ".join(text.strip().split())
    if not normalized:
        return ParseResult(ok=False, error="Please send a reminder message.")

    every_match = LEADING_EVERY_PREFIX.match(normalized)
    if every_match:
        recurrence_phrase = every_match.group(1).strip()
        task = every_match.group(2).strip(" .")
        return parse_schedule_components(task=task, time_phrase=recurrence_phrase, timezone_name=timezone_name)

    if WAKE_ME_UP_PREFIX.match(normalized):
        remainder = WAKE_ME_UP_PREFIX.sub("", normalized, count=1).strip()
        if not remainder:
            return ParseResult(ok=False, error="Please tell me when to wake you up.")
        return parse_schedule_components(
            task="wake up",
            time_phrase=remainder,
            timezone_name=timezone_name,
            requires_ack=True,
        )

    if REMIND_ME_PREFIX.match(normalized):
        remainder = REMIND_ME_PREFIX.sub("", normalized, count=1).strip()
        task, time_phrase = split_task_and_time_phrase(remainder)
        if not task and not time_phrase:
            return ParseResult(
                ok=False,
                error=(
                    "Use one of these formats:\n"
                    "• Remind me tomorrow at 7 PM to pay rent\n"
                    "• Remind me to pay rent today morning 9am\n"
                    "• Remind me every day at 8 AM to check my tasks\n"
                    "• Wake me up at 6 AM tomorrow"
                ),
            )
        if not task:
            return ParseResult(ok=False, error="I need the task to remind you about.")
        if not time_phrase:
            return ParseResult(ok=False, error="I need a time for that reminder.")
        return parse_schedule_components(task=task, time_phrase=time_phrase, timezone_name=timezone_name)

    return ParseResult(
        ok=False,
        error=(
            "I support these reminder formats right now:\n"
            "• Remind me tomorrow at 7 PM to pay rent\n"
            "• Remind me to pay rent today morning 9am\n"
            "• Remind me every day at 8 AM to check my tasks\n"
            "• Remind me every Monday at 9 AM to submit the report\n"
            "• Wake me up at 6 AM tomorrow\n"
            "• Wake me up every weekday at 6 AM"
        ),
    )


def split_task_and_time_phrase(remainder: str) -> tuple[str | None, str | None]:
    value = " ".join((remainder or "").strip().split())
    if not value:
        return None, None

    # Classic: <time> to <task>
    split_index = value.lower().rfind(" to ")
    if split_index != -1:
        time_phrase = value[:split_index].strip()
        task = value[split_index + 4 :].strip(" .")
        if time_phrase and task:
            return task, time_phrase

    # Task-first with explicit "to"
    match = re.match(r"^to\s+(.+?)\s+(today|tomorrow|tonight|next\s+\w+|this\s+\w+|in\s+\d+\s+\w+|every\s+.+|\d{1,2}(?:st|nd|rd|th)?\s+\w+.+)$", value, re.IGNORECASE)
    if match and looks_like_time_phrase(match.group(2)):
        return match.group(1).strip(" ."), match.group(2).strip()

    # Task-first with "at"
    match = re.match(r"^(?:to\s+)?(.+?)\s+at\s+(.+)$", value, re.IGNORECASE)
    if match and looks_like_time_phrase(match.group(2)):
        return match.group(1).strip(" ."), match.group(2).strip()

    # Task-first with a trailing time anchor without "at"
    anchor_match = None
    for found in TIME_ANCHOR_RE.finditer(value):
        anchor_match = found
        break
    if anchor_match is not None and anchor_match.start() > 0:
        task = value[: anchor_match.start()].strip(" .,")
        time_phrase = value[anchor_match.start() :].strip(" .,")
        if task and looks_like_time_phrase(time_phrase):
            return cleanup_task_prefix(task), time_phrase

    # about <task> <time>
    match = re.match(r"^about\s+(.+?)\s+(today|tomorrow|tonight|next\s+\w+|this\s+\w+|in\s+\d+\s+\w+|every\s+.+|\d{1,2}(?:st|nd|rd|th)?\s+\w+.+)$", value, re.IGNORECASE)
    if match and looks_like_time_phrase(match.group(2)):
        return match.group(1).strip(" ."), match.group(2).strip()

    if looks_like_time_phrase(value):
        return None, value
    return value.strip(" ."), None


def cleanup_task_prefix(task: str) -> str:
    cleaned = task.strip(" .")
    cleaned = re.sub(r"^(to|about)\s+", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip(" .")


def parse_schedule_components(
    *,
    task: str,
    time_phrase: str,
    timezone_name: str,
    requires_ack: bool = False,
    retry_interval_minutes: int = 2,
    max_attempts: int = 10,
) -> ParseResult:
    task = cleanup_task_prefix(task)
    if not task:
        return ParseResult(ok=False, error="I need the task to remind you about.")

    normalized_time_phrase = normalize_time_phrase(time_phrase)

    recurrence_bits = parse_recurrence_phrase(normalized_time_phrase, timezone_name)
    if recurrence_bits is not None:
        return ParseResult(
            ok=True,
            task=task,
            requires_ack=requires_ack,
            retry_interval_minutes=retry_interval_minutes,
            max_attempts=max_attempts,
            **recurrence_bits,
        )

    when_utc = parse_datetime_phrase(normalized_time_phrase, timezone_name)
    if when_utc is None:
        return ParseResult(
            ok=False,
            error=(
                "I couldn't understand that time. Try something like:\n"
                "• tomorrow at 7 PM\n"
                "• today morning 9\n"
                "• 18th Apr morning 8\n"
                "• next Monday at 9 AM\n"
                "• every weekday at 6 AM"
            ),
        )

    local_parts = extract_local_time_parts(when_utc, timezone_name)
    return ParseResult(
        ok=True,
        task=task,
        next_run_at_utc=when_utc,
        recurrence_type=RecurrenceType.ONCE.value,
        requires_ack=requires_ack,
        retry_interval_minutes=retry_interval_minutes,
        max_attempts=max_attempts,
        hour_local=local_parts[0],
        minute_local=local_parts[1],
    )


def parse_recurrence_phrase(time_phrase: str, timezone_name: str) -> dict[str, object] | None:
    normalized = normalize_time_phrase(" ".join(time_phrase.strip().split())).lower()

    match = re.match(r"^(?:every day|daily) at (.+)$", normalized, re.IGNORECASE)
    if match:
        return build_recurrence_result(
            recurrence_type=RecurrenceType.DAILY.value,
            recurrence_day_of_week=None,
            time_fragment=match.group(1),
            timezone_name=timezone_name,
        )

    match = re.match(r"^every weekday at (.+)$", normalized, re.IGNORECASE)
    if match:
        return build_recurrence_result(
            recurrence_type=RecurrenceType.WEEKDAY.value,
            recurrence_day_of_week=None,
            time_fragment=match.group(1),
            timezone_name=timezone_name,
        )

    match = re.match(r"^every (monday|tuesday|wednesday|thursday|friday|saturday|sunday) at (.+)$", normalized, re.IGNORECASE)
    if match:
        weekday_name = match.group(1).lower()
        return build_recurrence_result(
            recurrence_type=RecurrenceType.WEEKLY.value,
            recurrence_day_of_week=WEEKDAY_NAME_TO_INT[weekday_name],
            time_fragment=match.group(2),
            timezone_name=timezone_name,
        )

    return None


def build_recurrence_result(
    *,
    recurrence_type: str,
    recurrence_day_of_week: int | None,
    time_fragment: str,
    timezone_name: str,
) -> dict[str, object] | None:
    time_parts = parse_time_fragment(time_fragment, timezone_name)
    if time_parts is None:
        return None

    hour_local, minute_local = time_parts
    next_run = compute_next_occurrence_utc(
        recurrence_type=recurrence_type,
        timezone_name=timezone_name,
        hour_local=hour_local,
        minute_local=minute_local,
        recurrence_day_of_week=recurrence_day_of_week,
    )
    if next_run is None:
        return None

    return {
        "next_run_at_utc": next_run,
        "recurrence_type": recurrence_type,
        "recurrence_day_of_week": recurrence_day_of_week,
        "hour_local": hour_local,
        "minute_local": minute_local,
    }


def parse_datetime_phrase(time_phrase: str, timezone_name: str) -> datetime | None:
    normalized = normalize_time_phrase(time_phrase)
    settings = {
        "PREFER_DATES_FROM": "future",
        "TIMEZONE": timezone_name,
        "RETURN_AS_TIMEZONE_AWARE": True,
        "PREFER_DAY_OF_MONTH": "first",
    }
    parsed = dateparser.parse(normalized, settings=settings)
    if parsed is None:
        return None

    user_tz = ZoneInfo(timezone_name)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=user_tz)

    return parsed.astimezone(UTC)


def parse_time_fragment(time_fragment: str, timezone_name: str) -> tuple[int, int] | None:
    now = datetime.now(ZoneInfo(timezone_name))
    normalized = normalize_time_phrase(time_fragment)
    parsed = dateparser.parse(
        normalized,
        settings={
            "TIMEZONE": timezone_name,
            "RETURN_AS_TIMEZONE_AWARE": True,
            "RELATIVE_BASE": now,
        },
    )
    if parsed is None:
        return None

    user_tz = ZoneInfo(timezone_name)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=user_tz)
    local_dt = parsed.astimezone(user_tz)
    return local_dt.hour, local_dt.minute


def extract_local_time_parts(dt_utc: datetime, timezone_name: str) -> tuple[int, int]:
    local_dt = dt_utc.astimezone(ZoneInfo(timezone_name))
    return local_dt.hour, local_dt.minute


def recurrence_error() -> ParseResult:
    return ParseResult(
        ok=False,
        error=(
            "I couldn't understand that recurring schedule. Try one of these:\n"
            "• every day at 8 AM\n"
            "• every Monday at 9 AM\n"
            "• every weekday at 6 AM"
        ),
    )
