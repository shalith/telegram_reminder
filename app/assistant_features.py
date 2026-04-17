from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.agent_schema import DeadlineOffset
from app.parser import parse_datetime_phrase, parse_time_fragment


@dataclass(slots=True)
class DailyAgendaTime:
    hour_local: int
    minute_local: int


def parse_daily_agenda_time(time_phrase: str, timezone_name: str) -> DailyAgendaTime | None:
    parts = parse_time_fragment(time_phrase, timezone_name)
    if parts is None:
        return None
    return DailyAgendaTime(hour_local=parts[0], minute_local=parts[1])


def parse_deadline_offsets(text: str) -> list[DeadlineOffset]:
    normalized = text.lower().replace(", and", ",").replace(" and ", ",")
    matches = re.findall(r"(\d+)\s*(minute|minutes|hour|hours|day|days)\s+before", normalized)
    offsets: list[DeadlineOffset] = []
    for raw_value, raw_unit in matches:
        unit = raw_unit.rstrip("s") + ("s" if not raw_unit.endswith("s") else "")
        if unit == "minute":
            unit = "minutes"
        elif unit == "hour":
            unit = "hours"
        elif unit == "day":
            unit = "days"
        offsets.append(DeadlineOffset(value=int(raw_value), unit=unit))
    return offsets


def compute_deadline_trigger_utc(deadline_utc: datetime, offset: DeadlineOffset) -> datetime:
    if offset.unit == "minutes":
        return deadline_utc - timedelta(minutes=offset.value)
    if offset.unit == "hours":
        return deadline_utc - timedelta(hours=offset.value)
    return deadline_utc - timedelta(days=offset.value)


def format_offset_label(offset: DeadlineOffset) -> str:
    unit = offset.unit[:-1] if offset.value == 1 else offset.unit
    return f"{offset.value} {unit} before"


def local_day_bounds_utc(*, timezone_name: str, reference_utc: datetime | None = None) -> tuple[datetime, datetime]:
    tz = ZoneInfo(timezone_name)
    now_utc = reference_utc or datetime.now(UTC)
    local_now = now_utc.astimezone(tz)
    start_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def parse_deadline_phrase(deadline_phrase: str, timezone_name: str) -> datetime | None:
    return parse_datetime_phrase(deadline_phrase, timezone_name)


def due_today_label(dt_utc_naive: datetime, timezone_name: str) -> str:
    local_dt = dt_utc_naive.replace(tzinfo=UTC).astimezone(ZoneInfo(timezone_name))
    return local_dt.strftime("%I:%M %p")
