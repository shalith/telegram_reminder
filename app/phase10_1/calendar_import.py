
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import dateparser
from PIL import Image, ImageOps, ImageFilter

try:
    import pytesseract
except Exception:  # pragma: no cover
    pytesseract = None


class CalendarImportError(Exception):
    pass


@dataclass(slots=True)
class ImportedMeeting:
    title: str
    meeting_start: datetime
    reminder_at: datetime
    reminder_time_phrase: str
    source_line: str


@dataclass(slots=True)
class CalendarImportProposal:
    meetings: list[ImportedMeeting]
    day_hint: str | None
    lead_minutes: int
    raw_text: str

    def confirmation_text(self) -> str:
        lines = [f"I found {len(self.meetings)} meeting(s) in your screenshot. Confirm and I will create reminders {self.lead_minutes} minute(s) before each meeting:"]
        for meeting in self.meetings:
            start_label = meeting.meeting_start.strftime("%d %b %Y %I:%M %p")
            remind_label = meeting.reminder_at.strftime("%d %b %Y %I:%M %p")
            lines.append(f"• {meeting.title} — meeting at {start_label}, reminder at {remind_label}")
        lines.append("Reply yes to create them, or no to cancel.")
        return "\n".join(lines)


TIME_RANGE_RE = re.compile(
    r"(?P<start>\b\d{1,2}(?::\d{2})?\s*(?:AM|PM|am|pm)?\b)\s*(?:-|–|—|to)\s*(?P<end>\b\d{1,2}(?::\d{2})?\s*(?:AM|PM|am|pm)?\b)",
    re.IGNORECASE,
)
TIME_RE = re.compile(r"\b\d{1,2}(?::\d{2})?\s*(?:AM|PM|am|pm)?\b")
DATE_HINT_RE = re.compile(
    r"\b(?:today|tomorrow|tonight|next\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|this\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|\d{1,2}\s+[A-Za-z]{3,9}|[A-Za-z]{3,9}\s+\d{1,2})\b",
    re.IGNORECASE,
)
NOISE_RE = re.compile(r"^(?:calendar|agenda|teams|microsoft teams|all day|join|meet now|more options)$", re.IGNORECASE)


class CalendarScreenshotImporter:
    def __init__(self, *, default_timezone: str, lead_minutes: int = 10, fallback_to_today: bool = True):
        self.default_timezone = default_timezone
        self.lead_minutes = lead_minutes
        self.fallback_to_today = fallback_to_today

    def import_from_image(self, image_path: str | Path, *, caption_text: str | None = None) -> CalendarImportProposal:
        raw_text = self._ocr_text(image_path)
        meetings = self._extract_meetings(raw_text=raw_text, caption_text=caption_text)
        if not meetings:
            raise CalendarImportError(
                "I couldn't confidently find any meetings in that screenshot. Send a clearer Teams calendar screenshot, or add a caption like 'tomorrow' or '18 Apr'."
            )
        return CalendarImportProposal(meetings=meetings, day_hint=self._extract_day_hint(raw_text, caption_text), lead_minutes=self.lead_minutes, raw_text=raw_text)

    def _ocr_text(self, image_path: str | Path) -> str:
        if pytesseract is None:
            raise CalendarImportError(
                "Calendar screenshot import needs OCR support. Install pytesseract and the Tesseract OCR system package, or deploy with the provided Dockerfile."
            )
        try:
            image = Image.open(image_path)
        except Exception as exc:  # pragma: no cover
            raise CalendarImportError(f"I couldn't open that image: {exc}") from exc
        processed = ImageOps.grayscale(image)
        processed = ImageOps.autocontrast(processed)
        processed = processed.filter(ImageFilter.SHARPEN)
        try:
            text = pytesseract.image_to_string(processed)
        except Exception as exc:
            raise CalendarImportError(
                "OCR failed while reading that screenshot. Make sure the OCR engine is installed and try a clearer screenshot."
            ) from exc
        text = text.replace("\x0c", " ")
        return "\n".join(line.strip() for line in text.splitlines() if line.strip())

    def _extract_day_hint(self, raw_text: str, caption_text: str | None) -> str | None:
        combined = "\n".join(part for part in [caption_text or "", raw_text] if part)
        match = DATE_HINT_RE.search(combined)
        if match:
            return match.group(0)
        return None

    def _extract_meetings(self, *, raw_text: str, caption_text: str | None) -> list[ImportedMeeting]:
        lines = [self._clean_line(line) for line in raw_text.splitlines()]
        lines = [line for line in lines if line and not NOISE_RE.match(line)]
        day_hint = self._extract_day_hint(raw_text, caption_text)
        base_now = datetime.now()
        meetings: list[ImportedMeeting] = []
        used_indices: set[int] = set()
        for idx, line in enumerate(lines):
            if idx in used_indices:
                continue
            parsed = self._parse_line_meeting(line=line, base_now=base_now, day_hint=day_hint)
            if parsed is None and self._looks_like_time_only(line):
                title = self._neighbor_title(lines, idx)
                if title:
                    parsed = self._parse_line_meeting(line=f"{line} {title}", base_now=base_now, day_hint=day_hint)
                    if parsed is not None:
                        used_indices.add(idx + 1)
            if parsed is not None:
                meetings.append(parsed)
                used_indices.add(idx)
        deduped: list[ImportedMeeting] = []
        seen = set()
        for meeting in meetings:
            key = (meeting.title.lower(), meeting.meeting_start.isoformat())
            if key in seen:
                continue
            seen.add(key)
            deduped.append(meeting)
        return deduped[:10]

    def _clean_line(self, line: str) -> str:
        cleaned = " ".join(line.strip().split())
        cleaned = cleaned.replace("|", " ")
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned.strip(" -–—")

    def _looks_like_time_only(self, line: str) -> bool:
        stripped = line.strip()
        return bool(stripped and TIME_RE.fullmatch(stripped))

    def _neighbor_title(self, lines: list[str], idx: int) -> str | None:
        for offset in (1, 2):
            j = idx + offset
            if j >= len(lines):
                break
            candidate = lines[j]
            if not candidate or TIME_RE.search(candidate):
                continue
            if len(candidate) < 3:
                continue
            return candidate
        return None

    def _parse_line_meeting(self, *, line: str, base_now: datetime, day_hint: str | None) -> ImportedMeeting | None:
        line = self._clean_line(line)
        if not line:
            return None
        time_value = None
        title = None
        range_match = TIME_RANGE_RE.search(line)
        if range_match:
            time_value = range_match.group('start')
            title = self._strip_time_text(line, range_match.group(0))
        else:
            match = TIME_RE.search(line)
            if match:
                time_value = match.group(0)
                title = self._strip_time_text(line, match.group(0))
        if not time_value or not title:
            return None
        if len(title) < 3:
            return None
        start_dt = self._build_datetime(day_hint=day_hint, time_value=time_value, base_now=base_now)
        if start_dt is None:
            return None
        reminder_at = start_dt - timedelta(minutes=self.lead_minutes)
        reminder_phrase = reminder_at.strftime('%d %b %Y %I:%M %p')
        return ImportedMeeting(
            title=title,
            meeting_start=start_dt,
            reminder_at=reminder_at,
            reminder_time_phrase=reminder_phrase,
            source_line=line,
        )

    def _strip_time_text(self, line: str, time_text: str) -> str:
        stripped = line.replace(time_text, ' ')
        stripped = re.sub(r'\b(?:today|tomorrow|tonight|next\s+\w+|this\s+\w+)\b', ' ', stripped, flags=re.IGNORECASE)
        stripped = re.sub(r'\s+', ' ', stripped)
        stripped = stripped.strip(' -–—:')
        return stripped

    def _build_datetime(self, *, day_hint: str | None, time_value: str, base_now: datetime) -> datetime | None:
        candidates = []
        time_value = self._normalize_time_token(time_value)
        if day_hint:
            candidates.append(f"{day_hint} {time_value}")
        if self.fallback_to_today:
            candidates.append(f"today {time_value}")
        for phrase in candidates:
            dt = dateparser.parse(
                phrase,
                settings={
                    'TIMEZONE': self.default_timezone,
                    'RETURN_AS_TIMEZONE_AWARE': False,
                    'PREFER_DATES_FROM': 'future',
                    'RELATIVE_BASE': base_now,
                },
            )
            if dt is not None:
                return dt
        return None

    def _normalize_time_token(self, value: str) -> str:
        cleaned = value.strip().upper().replace('.', '')
        if re.fullmatch(r'\d{1,2}:\d{2}', cleaned):
            try:
                hour = int(cleaned.split(':')[0])
            except Exception:
                hour = 12
            suffix = 'AM' if 6 <= hour < 12 else ('PM' if 12 <= hour <= 11 else 'AM')
            return f"{cleaned} {suffix}" if not cleaned.endswith(('AM', 'PM')) else cleaned
        if re.fullmatch(r'\d{1,2}', cleaned):
            hour = int(cleaned)
            suffix = 'AM' if 6 <= hour < 12 else 'PM'
            return f"{cleaned} {suffix}"
        return cleaned
