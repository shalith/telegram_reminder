from __future__ import annotations

import re

WEEKDAY_WORDS = "monday|tuesday|wednesday|thursday|friday|saturday|sunday"
MONTH_WORDS = "jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|sept|september|oct|october|nov|november|dec|december"

_TIME_HINT_RE = re.compile(
    rf"\b(today|tomorrow|tonight|morning|afternoon|evening|night|noon|midnight|next\s+(?:{WEEKDAY_WORDS})|this\s+(?:{WEEKDAY_WORDS})|every\s+(?:day|weekday|{WEEKDAY_WORDS})|in\s+\d+\s+(?:minutes?|hours?|days?)|(?:\d{{1,2}})(?:st|nd|rd|th)?\s+(?:{MONTH_WORDS})(?:\s+\d{{4}})?|at\s+\d|\d{{1,2}}(?::\d{{2}})?\s*(?:am|pm))\b",
    re.IGNORECASE,
)
_APPROXIMATE_RE = re.compile(r"\b(?:around|about|approximately|approx(?:\.)?|ish)\b", re.IGNORECASE)


def looks_like_time_phrase(text: str) -> bool:
    normalized = " ".join((text or "").strip().split())
    if not normalized:
        return False
    return bool(_TIME_HINT_RE.search(normalized))


def normalize_time_phrase(text: str) -> str:
    value = " ".join((text or "").strip().split())
    if not value:
        return value

    value = re.sub(r"\b(around|about|approximately|approx(?:\.)?|ish)\s+", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\b(\d{1,2})(am|pm)\b", r"\1 \2", value, flags=re.IGNORECASE)
    value = re.sub(r"\bat\s+today\b", "today", value, flags=re.IGNORECASE)
    value = re.sub(r"\bat\s+tomorrow\b", "tomorrow", value, flags=re.IGNORECASE)
    value = re.sub(r"\bat\s+at\b", "at", value, flags=re.IGNORECASE)
    value = re.sub(r"(?i)\bat(?=\d)", "at ", value)

    def _period_repl(match: re.Match[str]) -> str:
        prefix = (match.group("prefix") or "").strip()
        period = match.group("period").lower()
        hour = int(match.group("hour"))
        minute = match.group("minute")
        suffix = (match.group("suffix") or "").lower()

        if suffix in {"am", "pm"}:
            ampm = suffix.upper()
        elif period == "morning":
            ampm = "AM"
        elif period in {"afternoon", "evening", "night"}:
            ampm = "PM"
        else:
            ampm = "AM"

        if minute:
            time_text = f"{hour}:{minute} {ampm}"
        else:
            time_text = f"{hour} {ampm}"
        prefix_text = f"{prefix} at " if prefix else ""
        return f"{prefix_text}{time_text}".strip()

    # today morning 9 / 18th Apr morning 8 / next Monday evening 6
    value = re.sub(
        rf"(?P<prefix>(?:today|tomorrow|tonight|next\s+(?:{WEEKDAY_WORDS})|this\s+(?:{WEEKDAY_WORDS})|(?:\d{{1,2}})(?:st|nd|rd|th)?\s+(?:{MONTH_WORDS})(?:\s+\d{{4}})?))\s+(?P<period>morning|afternoon|evening|night)\s+(?P<hour>\d{{1,2}})(?::(?P<minute>\d{{2}}))?\s*(?P<suffix>am|pm)?\b",
        _period_repl,
        value,
        flags=re.IGNORECASE,
    )

    # morning 9 / evening 6 / night 10:30
    value = re.sub(
        r"(?<!\w)(?P<period>morning|afternoon|evening|night)\s+(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<suffix>am|pm)?\b",
        _period_repl,
        value,
        flags=re.IGNORECASE,
    )


    def _postfix_period_repl(match: re.Match[str]) -> str:
        postfix = (match.group("postfix") or "").strip()
        period = match.group("period").lower()
        hour = int(match.group("hour"))
        minute = match.group("minute")
        suffix = (match.group("suffix") or "").lower()

        if suffix in {"am", "pm"}:
            ampm = suffix.upper()
        elif period == "morning":
            ampm = "AM"
        elif period in {"afternoon", "evening", "night"}:
            ampm = "PM"
        else:
            ampm = "AM"

        if minute:
            return f"{postfix} at {hour}:{minute} {ampm}".strip()
        return f"{postfix} at {hour} {ampm}".strip()

    # 11:30 morning tomorrow / 8 evening today
    value = re.sub(
        rf"(?P<hour>\d{{1,2}})(?::(?P<minute>\d{{2}}))?\s+(?P<period>morning|afternoon|evening|night)\s+(?P<postfix>(?:today|tomorrow|tonight|next\s+(?:{WEEKDAY_WORDS})|this\s+(?:{WEEKDAY_WORDS})|(?:\d{{1,2}})(?:st|nd|rd|th)?\s+(?:{MONTH_WORDS})(?:\s+\d{{4}})?))\s*(?P<suffix>am|pm)?\b",
        _postfix_period_repl,
        value,
        flags=re.IGNORECASE,
    )

    # 10:30 morning / 8 evening / today 10:30 morning / tomorrow 7 night
    value = re.sub(
        rf"(?P<prefix>(?:today|tomorrow|tonight|next\s+(?:{WEEKDAY_WORDS})|this\s+(?:{WEEKDAY_WORDS})|(?:\d{{1,2}})(?:st|nd|rd|th)?\s+(?:{MONTH_WORDS})(?:\s+\d{{4}})?))?\s*(?P<hour>\d{{1,2}})(?::(?P<minute>\d{{2}}))?\s+(?P<period>morning|afternoon|evening|night)\s*(?P<suffix>am|pm)?\b",
        _period_repl,
        value,
        flags=re.IGNORECASE,
    )
    # today morning / tomorrow evening with no hour -> keep as a richer phrase but add a default cue
    value = re.sub(r"\btoday morning\b", "today at 9 AM", value, flags=re.IGNORECASE)
    value = re.sub(r"\btomorrow morning\b", "tomorrow at 9 AM", value, flags=re.IGNORECASE)
    value = re.sub(r"\btoday evening\b", "today at 6 PM", value, flags=re.IGNORECASE)
    value = re.sub(r"\btomorrow evening\b", "tomorrow at 6 PM", value, flags=re.IGNORECASE)

    value = re.sub(r"\b(\d{1,2})\s+o'clock\b", r"\1", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def contains_approximate_time_language(text: str) -> bool:
    normalized = " ".join((text or "").strip().split())
    if not normalized:
        return False
    return bool(_APPROXIMATE_RE.search(normalized))
