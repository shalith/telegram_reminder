from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import dateparser
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

try:
    import pytesseract
    from pytesseract import Output
except Exception:  # pragma: no cover
    pytesseract = None
    Output = None


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
        lines = [
            f"I found {len(self.meetings)} likely meeting(s) in your screenshot. Confirm and I will create reminders {self.lead_minutes} minute(s) before each:"
        ]
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
NOISE_RE = re.compile(r"^(?:calendar|agenda|teams|microsoft teams|all day|join|meet now|more options|search.*|today)$", re.IGNORECASE)
MONTH_YEAR_RE = re.compile(r"\b(?P<month>January|February|March|April|May|June|July|August|September|October|November|December)\s+(?P<year>20\d{2})\b", re.IGNORECASE)
WEEKDAY_SET = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
KNOWN_EVENT_WORDS = {"meeting", "standup", "microsoft", "teams", "canceled", "cancelled", "prod", "daily"}


@dataclass(slots=True)
class OcrWord:
    text: str
    left: int
    top: int
    width: int
    height: int
    conf: float

    @property
    def cx(self) -> float:
        return self.left + self.width / 2

    @property
    def cy(self) -> float:
        return self.top + self.height / 2


@dataclass(slots=True)
class DayColumn:
    label: str
    day_phrase: str
    x_left: float
    x_right: float
    x_center: float


@dataclass(slots=True)
class TimeRow:
    phrase: str
    hour_24: int
    y: float


@dataclass(slots=True)
class EventBox:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def cx(self) -> float:
        return (self.left + self.right) / 2

    @property
    def cy(self) -> float:
        return (self.top + self.bottom) / 2


class CalendarScreenshotImporter:
    def __init__(self, *, default_timezone: str, lead_minutes: int = 10, fallback_to_today: bool = True):
        self.default_timezone = default_timezone
        self.lead_minutes = lead_minutes
        self.fallback_to_today = fallback_to_today

    def import_from_image(self, image_path: str | Path, *, caption_text: str | None = None) -> CalendarImportProposal:
        base_image = self._load_image(image_path)
        processed = self._prepare_for_ocr(base_image)
        raw_text = self._ocr_text(processed)

        # OCR text parsing is allowed only when the extracted text itself looks coherent.
        meetings = self._extract_meetings(raw_text=raw_text, caption_text=caption_text)
        vision_meetings = self._extract_meetings_vision(
            base_image=base_image,
            processed=processed,
            raw_text=raw_text,
            caption_text=caption_text,
        )

        if vision_meetings:
            meetings = self._select_best_candidates(ocr_candidates=meetings, vision_candidates=vision_meetings)

        if not meetings:
            raise CalendarImportError(
                "I couldn't confidently find any meetings in that screenshot. Send a clearer Teams calendar screenshot, or add a caption like 'tomorrow' or '18 Apr'."
            )
        meetings = sorted(meetings, key=lambda item: item.meeting_start)
        return CalendarImportProposal(
            meetings=meetings,
            day_hint=self._extract_day_hint(raw_text, caption_text),
            lead_minutes=self.lead_minutes,
            raw_text=raw_text,
        )

    def _load_image(self, image_path: str | Path) -> Image.Image:
        try:
            return Image.open(image_path).convert("RGB")
        except Exception as exc:  # pragma: no cover
            raise CalendarImportError(f"I couldn't open that image: {exc}") from exc

    def _prepare_for_ocr(self, image: Image.Image) -> Image.Image:
        w, h = image.size
        scale = 2 if max(w, h) < 1800 else 1
        if scale != 1:
            image = image.resize((w * scale, h * scale), Image.Resampling.LANCZOS)
        gray = ImageOps.grayscale(image)
        gray = ImageOps.autocontrast(gray)
        gray = ImageEnhance.Contrast(gray).enhance(2.1)
        gray = ImageFilter.UnsharpMask(radius=1.4, percent=180, threshold=2).filter(gray)
        return gray

    def _ocr_text(self, image: Image.Image) -> str:
        if pytesseract is None:
            raise CalendarImportError(
                "Calendar screenshot import needs OCR support. Install pytesseract and the Tesseract OCR system package, or deploy with the provided Dockerfile."
            )
        texts: list[str] = []
        variants = [image, ImageOps.invert(image), image.point(lambda p: 255 if p > 155 else 0)]
        for variant in variants:
            try:
                text = pytesseract.image_to_string(variant, config="--psm 6")
            except Exception as exc:
                raise CalendarImportError(
                    "OCR failed while reading that screenshot. Make sure the OCR engine is installed and try a clearer screenshot."
                ) from exc
            text = text.replace("\x0c", " ")
            cleaned = "\n".join(line.strip() for line in text.splitlines() if line.strip())
            if cleaned:
                texts.append(cleaned)
        return "\n".join(dict.fromkeys(texts))

    def _extract_day_hint(self, raw_text: str, caption_text: str | None) -> str | None:
        combined = "\n".join(part for part in [caption_text or "", raw_text] if part)
        match = DATE_HINT_RE.search(combined)
        if match:
            return match.group(0)
        return None

    def _extract_meetings(self, *, raw_text: str, caption_text: str | None) -> list[ImportedMeeting]:
        # Keep OCR-only extraction conservative. Photos of screens often create garbage OCR.
        lines = [self._clean_line(line) for line in raw_text.splitlines()]
        lines = [line for line in lines if line and not NOISE_RE.match(line)]
        day_hint = self._extract_day_hint(raw_text, caption_text)
        base_now = datetime.now()
        meetings: list[ImportedMeeting] = []
        used_indices: set[int] = set()
        for idx, line in enumerate(lines):
            if idx in used_indices or not self._ocr_line_looks_like_event(line):
                continue
            parsed = self._parse_line_meeting(line=line, base_now=base_now, day_hint=day_hint)
            if parsed is None and self._looks_like_time_only(line):
                title = self._neighbor_title(lines, idx)
                if title:
                    parsed = self._parse_line_meeting(line=f"{line} {title}", base_now=base_now, day_hint=day_hint)
                    if parsed is not None:
                        used_indices.add(idx + 1)
            if parsed is not None and self._meeting_quality(parsed) >= 0.55:
                meetings.append(parsed)
                used_indices.add(idx)
        return self._dedupe_meetings(meetings, limit=6)

    def _extract_meetings_vision(
        self,
        *,
        base_image: Image.Image,
        processed: Image.Image,
        raw_text: str,
        caption_text: str | None,
    ) -> list[ImportedMeeting]:
        try:
            words = self._ocr_words(processed)
        except Exception:
            words = []
        if not words:
            return []

        width, height = processed.size
        month_name, year = self._infer_month_year(words, raw_text)
        columns = self._extract_day_columns(words, width=width, height=height, month_name=month_name, year=year, caption_text=caption_text)
        if not columns:
            return []
        time_rows = self._extract_time_rows(words, image_width=width, image_height=height)
        if len(time_rows) < 2:
            return []

        grid_left = max(0, int(min(col.x_left for col in columns) - 10))
        grid_right = min(width, int(max(col.x_right for col in columns) + 10))
        grid_top = max(0, int(min(row.y for row in time_rows) - 25))
        row_step = self._average_row_step(time_rows)
        grid_bottom = min(height, int(max(row.y for row in time_rows) + row_step * 5.5))

        boxes = self._detect_event_boxes(base_image, grid_bounds=(grid_left, grid_top, grid_right, grid_bottom))
        if not boxes:
            return []

        boxes = self._merge_duplicate_boxes(boxes)
        meetings: list[ImportedMeeting] = []
        for box in boxes:
            if box.width < width * 0.06 or box.height < height * 0.012:
                continue
            column = self._assign_day_column(box, columns)
            if column is None:
                continue
            start_dt = self._infer_start_datetime(box, time_rows=time_rows, day_phrase=column.day_phrase)
            if start_dt is None:
                continue

            title, title_quality = self._extract_title_for_box(words, box)
            if title_quality < 0.28:
                title = self._generic_title_from_box(column.label, start_dt)
            reminder_at = start_dt - timedelta(minutes=self.lead_minutes)
            candidate = ImportedMeeting(
                title=title,
                meeting_start=start_dt,
                reminder_at=reminder_at,
                reminder_time_phrase=reminder_at.strftime("%d %b %Y %I:%M %p"),
                source_line=f"vision:{column.label}:{title}",
            )
            if self._meeting_quality(candidate, title_quality=title_quality, box=box, time_rows=time_rows) >= 0.45:
                meetings.append(candidate)

        meetings = self._dedupe_meetings(meetings, limit=8)
        return self._keep_best_per_slot(meetings)

    def _ocr_words(self, image: Image.Image) -> list[OcrWord]:
        if pytesseract is None or Output is None:
            return []
        data = pytesseract.image_to_data(image, output_type=Output.DICT, config="--psm 11")
        words: list[OcrWord] = []
        n = len(data.get("text", []))
        for idx in range(n):
            text = (data["text"][idx] or "").strip()
            if not text:
                continue
            try:
                conf = float(data["conf"][idx])
            except Exception:
                conf = -1.0
            if conf < 10:
                continue
            words.append(
                OcrWord(
                    text=text,
                    left=int(data["left"][idx]),
                    top=int(data["top"][idx]),
                    width=int(data["width"][idx]),
                    height=int(data["height"][idx]),
                    conf=conf,
                )
            )
        return words

    def _infer_month_year(self, words: list[OcrWord], raw_text: str) -> tuple[str | None, int | None]:
        combined = " ".join(word.text for word in words) + " " + raw_text
        match = MONTH_YEAR_RE.search(combined)
        if match:
            return match.group("month"), int(match.group("year"))
        now = datetime.now()
        return now.strftime("%B"), now.year

    def _extract_day_columns(
        self,
        words: list[OcrWord],
        *,
        width: int,
        height: int,
        month_name: str | None,
        year: int | None,
        caption_text: str | None,
    ) -> list[DayColumn]:
        top_limit = max(260, int(height * 0.22))
        top_words = [w for w in words if w.top < top_limit and w.cy > 60]
        numeric_days = [w for w in top_words if re.fullmatch(r"\d{1,2}", w.text) and 1 <= int(w.text) <= 31]
        weekday_words = [w for w in top_words if w.text.lower() in WEEKDAY_SET]
        columns: list[DayColumn] = []
        for num in sorted(numeric_days, key=lambda w: w.cx):
            near_weekday = min(
                (w for w in weekday_words if abs(w.cx - num.cx) < max(80, num.width * 5)),
                key=lambda w: abs(w.cx - num.cx),
                default=None,
            )
            day_num = int(num.text)
            day_phrase = self._build_day_phrase(day_num=day_num, month_name=month_name, year=year, caption_text=caption_text)
            label = str(day_num)
            if near_weekday is not None:
                label = f"{day_num} {near_weekday.text.title()}"
            columns.append(DayColumn(label=label, day_phrase=day_phrase, x_left=0, x_right=0, x_center=num.cx))
        if not columns and weekday_words:
            for weekday in sorted(weekday_words, key=lambda w: w.cx):
                columns.append(
                    DayColumn(label=weekday.text.title(), day_phrase=weekday.text.lower(), x_left=0, x_right=0, x_center=weekday.cx)
                )
        columns.sort(key=lambda c: c.x_center)
        for idx, col in enumerate(columns):
            left = 0 if idx == 0 else (columns[idx - 1].x_center + col.x_center) / 2
            right = width if idx == len(columns) - 1 else (col.x_center + columns[idx + 1].x_center) / 2
            col.x_left = left
            col.x_right = right
        return columns[:7]

    def _build_day_phrase(self, *, day_num: int, month_name: str | None, year: int | None, caption_text: str | None) -> str:
        if caption_text:
            cap = caption_text.strip().lower()
            if "tomorrow" in cap:
                base = datetime.now() + timedelta(days=1)
                return base.strftime("%d %b %Y")
        if month_name and year:
            return f"{day_num} {month_name} {year}"
        return f"{day_num}"

    def _extract_time_rows(self, words: list[OcrWord], *, image_width: int, image_height: int) -> list[TimeRow]:
        left_limit = max(120, int(image_width * 0.12))
        time_words = [w for w in words if w.cx < left_limit and w.top > int(image_height * 0.12)]
        rows: list[TimeRow] = []
        for word in time_words:
            phrase = self._normalize_time_phrase(word.text, time_words, word)
            if not phrase:
                continue
            dt = dateparser.parse(phrase)
            if dt is None:
                continue
            if not (6 <= dt.hour <= 22):
                continue
            rows.append(TimeRow(phrase=phrase, hour_24=dt.hour, y=word.cy))

        # dedupe and enforce monotonic increasing rows to avoid noisy labels.
        rows.sort(key=lambda r: r.y)
        deduped: list[TimeRow] = []
        for row in rows:
            if deduped and abs(row.y - deduped[-1].y) < 18:
                continue
            if deduped and row.hour_24 < deduped[-1].hour_24:
                continue
            if deduped and row.hour_24 - deduped[-1].hour_24 > 3:
                continue
            deduped.append(row)
        return deduped

    def _normalize_time_phrase(self, text: str, words: list[OcrWord], current: OcrWord) -> str | None:
        token = text.upper().replace(".", "")
        token = token.replace("O", "0")
        if re.fullmatch(r"\d{1,2}(?::\d{2})?(AM|PM)", token):
            return token
        if re.fullmatch(r"\d{1,2}(?::\d{2})?", token):
            suffix = None
            for other in words:
                if other is current:
                    continue
                if abs(other.cy - current.cy) < max(12, current.height * 1.2) and 0 < other.left - current.left < 70:
                    maybe = other.text.upper().replace(".", "")
                    if maybe in {"AM", "PM"}:
                        suffix = maybe
                        break
            hour = int(token.split(":")[0])
            if suffix:
                return f"{token} {suffix}"
            if 6 <= hour < 12:
                return f"{token} AM"
            if 12 <= hour <= 10:
                return f"{token} PM"
            return f"{token} {'AM' if hour < 12 else 'PM'}"
        return None

    def _average_row_step(self, rows: list[TimeRow]) -> float:
        if len(rows) < 2:
            return 60.0
        diffs = [rows[i + 1].y - rows[i].y for i in range(len(rows) - 1) if rows[i + 1].y - rows[i].y > 8]
        return sum(diffs) / len(diffs) if diffs else 60.0

    def _detect_event_boxes(self, image: Image.Image, *, grid_bounds: tuple[int, int, int, int] | None = None) -> list[EventBox]:
        base = image.copy()
        orig_w, orig_h = base.size
        target_w = 700
        if orig_w > target_w:
            scale = target_w / orig_w
            small = base.resize((target_w, max(1, int(orig_h * scale))), Image.Resampling.BILINEAR)
        else:
            scale = 1.0
            small = base
        w, h = small.size
        if grid_bounds:
            l, t, r, b = grid_bounds
            l = int(l * scale)
            t = int(t * scale)
            r = int(r * scale)
            b = int(b * scale)
        else:
            l, t, r, b = 0, 0, w, h

        px = small.load()
        mask = [[False] * w for _ in range(h)]
        for y in range(max(0, t), min(h, b)):
            for x in range(max(0, l), min(w, r)):
                red, green, blue = px[x, y]
                if blue > 110 and blue > red + 18 and blue > green + 10 and red < 190 and green < 210:
                    mask[y][x] = True

        visited = [[False] * w for _ in range(h)]
        boxes: list[EventBox] = []
        min_area = max(60, int(w * h * 0.00014))
        for y in range(max(0, t), min(h, b)):
            for x in range(max(0, l), min(w, r)):
                if not mask[y][x] or visited[y][x]:
                    continue
                q = deque([(x, y)])
                visited[y][x] = True
                min_x = max_x = x
                min_y = max_y = y
                area = 0
                while q:
                    cx, cy = q.popleft()
                    area += 1
                    min_x = min(min_x, cx)
                    max_x = max(max_x, cx)
                    min_y = min(min_y, cy)
                    max_y = max(max_y, cy)
                    for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                        if max(0, l) <= nx < min(w, r) and max(0, t) <= ny < min(h, b) and mask[ny][nx] and not visited[ny][nx]:
                            visited[ny][nx] = True
                            q.append((nx, ny))
                bw = max_x - min_x + 1
                bh = max_y - min_y + 1
                if area < min_area or bw < 40 or bh < 12:
                    continue
                if bh > 95 or bw < bh * 2.2:
                    continue
                left = int(min_x / scale)
                right = int((max_x + 1) / scale)
                top = int(min_y / scale)
                bottom = int((max_y + 1) / scale)
                boxes.append(EventBox(left=left, top=top, right=right, bottom=bottom))

        return boxes

    def _merge_duplicate_boxes(self, boxes: list[EventBox]) -> list[EventBox]:
        boxes = sorted(boxes, key=lambda b: (b.top, b.left))
        merged: list[EventBox] = []
        for box in boxes:
            if merged and self._should_merge_boxes(merged[-1], box):
                prev = merged[-1]
                merged[-1] = EventBox(
                    left=min(prev.left, box.left),
                    top=min(prev.top, box.top),
                    right=max(prev.right, box.right),
                    bottom=max(prev.bottom, box.bottom),
                )
            else:
                merged.append(box)
        return merged

    def _should_merge_boxes(self, a: EventBox, b: EventBox) -> bool:
        same_row = abs(a.top - b.top) < 12 and abs(a.bottom - b.bottom) < 12
        touching = b.left - a.right < 18
        overlap = not (b.top > a.bottom or b.bottom < a.top)
        return same_row and touching and overlap

    def _assign_day_column(self, box: EventBox, columns: list[DayColumn]) -> DayColumn | None:
        if not columns:
            return None
        for col in columns:
            if col.x_left <= box.cx <= col.x_right:
                return col
        return min(columns, key=lambda col: abs(col.x_center - box.cx), default=None)

    def _infer_start_datetime(self, box: EventBox, *, time_rows: list[TimeRow], day_phrase: str) -> datetime | None:
        if len(time_rows) < 2:
            return None
        time_rows = sorted(time_rows, key=lambda r: r.y)
        row_step = self._average_row_step(time_rows)
        nearest_idx = min(range(len(time_rows)), key=lambda i: abs(time_rows[i].y - box.cy))
        base_row = time_rows[nearest_idx]
        if box.top < base_row.y and nearest_idx > 0 and (base_row.y - box.top) > row_step * 0.35:
            base_row = time_rows[nearest_idx - 1]
        offset_ratio = max(0.0, min(1.0, (box.top - base_row.y) / max(1.0, row_step)))
        minutes = 0 if offset_ratio < 0.28 else 30
        phrase = f"{day_phrase} {self._format_hour(base_row.hour_24, minutes)}"
        dt = dateparser.parse(
            phrase,
            settings={
                "TIMEZONE": self.default_timezone,
                "RETURN_AS_TIMEZONE_AWARE": False,
                "PREFER_DATES_FROM": "future",
                "RELATIVE_BASE": datetime.now(),
            },
        )
        return dt

    def _format_hour(self, hour_24: int, minutes: int) -> str:
        base = datetime(2000, 1, 1, hour_24, 0) + timedelta(minutes=minutes)
        return base.strftime("%I:%M %p").lstrip("0")

    def _extract_title_for_box(self, words: list[OcrWord], box: EventBox) -> tuple[str, float]:
        inside: list[tuple[int, int, str, float]] = []
        for word in words:
            if word.cx >= box.left - 8 and word.cx <= box.right + 8 and word.cy >= box.top - 6 and word.cy <= box.bottom + 6:
                text = self._clean_box_word(word.text)
                if not text:
                    continue
                inside.append((word.top, word.left, text, word.conf))
        if not inside:
            return "", 0.0
        inside.sort()
        title = " ".join(text for _, _, text, _ in inside[:8])
        title = re.sub(r"\s+", " ", title).strip(" -–—:")
        title = re.sub(r"^(?:Canceled:?)\s*", "Canceled ", title, flags=re.IGNORECASE)
        quality = self._title_quality(title, avg_conf=sum(conf for *_, conf in inside) / max(1, len(inside)))
        return title, quality

    def _clean_box_word(self, text: str) -> str:
        cleaned = text.strip()
        cleaned = cleaned.replace("|", "I")
        cleaned = re.sub(r"[^A-Za-z0-9\-:\[\]&]+", "", cleaned)
        return cleaned

    def _title_quality(self, title: str, *, avg_conf: float = 0.0) -> float:
        if not title:
            return 0.0
        letters = sum(ch.isalpha() for ch in title)
        digits = sum(ch.isdigit() for ch in title)
        bad = sum(not (ch.isalnum() or ch in " []&:-") for ch in title)
        tokens = [t.lower() for t in re.split(r"\s+", title) if t]
        known = sum(token.strip("[]:-") in KNOWN_EVENT_WORDS for token in tokens)
        ratio = letters / max(1, len(title))
        score = 0.2 + min(0.35, ratio * 0.45) + min(0.2, avg_conf / 100 * 0.2) + min(0.2, known * 0.08)
        if letters < 4:
            score -= 0.35
        if bad > 2:
            score -= 0.2
        if digits > letters:
            score -= 0.15
        return max(0.0, min(1.0, score))

    def _generic_title_from_box(self, column_label: str, start_dt: datetime) -> str:
        if start_dt.strftime("%I:%M %p"):
            return f"Meeting ({column_label})"
        return "Meeting"

    def _clean_line(self, line: str) -> str:
        cleaned = " ".join(line.strip().split())
        cleaned = cleaned.replace("|", " ")
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned.strip(" -–—")

    def _ocr_line_looks_like_event(self, line: str) -> bool:
        if len(line) < 6 or len(line) > 120:
            return False
        letters = sum(ch.isalpha() for ch in line)
        if letters < 4:
            return False
        if re.search(r"[A-Za-z]{2,}.*\d{1,2}:?\d{0,2}", line) or re.search(r"\d{1,2}:?\d{0,2}.*[A-Za-z]{2,}", line):
            return True
        return any(word.lower() in line.lower() for word in KNOWN_EVENT_WORDS)

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
            time_value = range_match.group("start")
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
        reminder_phrase = reminder_at.strftime("%d %b %Y %I:%M %p")
        return ImportedMeeting(
            title=title,
            meeting_start=start_dt,
            reminder_at=reminder_at,
            reminder_time_phrase=reminder_phrase,
            source_line=line,
        )

    def _strip_time_text(self, line: str, time_text: str) -> str:
        stripped = line.replace(time_text, " ")
        stripped = re.sub(r"\b(?:today|tomorrow|tonight|next\s+\w+|this\s+\w+)\b", " ", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s+", " ", stripped)
        stripped = stripped.strip(" -–—:")
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
                    "TIMEZONE": self.default_timezone,
                    "RETURN_AS_TIMEZONE_AWARE": False,
                    "PREFER_DATES_FROM": "future",
                    "RELATIVE_BASE": base_now,
                },
            )
            if dt is not None:
                return dt
        return None

    def _normalize_time_token(self, value: str) -> str:
        cleaned = value.strip().upper().replace(".", "")
        if re.fullmatch(r"\d{1,2}:\d{2}", cleaned):
            hour, minute = cleaned.split(":", 1)
            hh = int(hour)
            mm = int(minute)
            if 0 <= hh <= 23 and 0 <= mm <= 59:
                base = datetime(2000, 1, 1, hh, mm)
                return base.strftime("%I:%M %p").lstrip("0")
        if re.fullmatch(r"\d{1,2}", cleaned):
            hour = int(cleaned)
            if 0 <= hour <= 23:
                base = datetime(2000, 1, 1, hour, 0)
                return base.strftime("%I:%M %p").lstrip("0")
        return cleaned

    def _meeting_quality(
        self,
        meeting: ImportedMeeting,
        *,
        title_quality: float | None = None,
        box: EventBox | None = None,
        time_rows: list[TimeRow] | None = None,
    ) -> float:
        title_quality = self._title_quality(meeting.title) if title_quality is None else title_quality
        score = title_quality * 0.55
        if meeting.title.startswith("Meeting ("):
            score += 0.12
        if 6 <= meeting.meeting_start.hour <= 22:
            score += 0.18
        else:
            score -= 0.4
        if box is not None:
            if box.width >= 120:
                score += 0.08
            if box.height >= 16:
                score += 0.05
        if time_rows is not None and len(time_rows) >= 2:
            nearest = min(abs(row.y - (box.top if box else row.y)) for row in time_rows)
            if nearest < 35:
                score += 0.08
        return max(0.0, min(1.0, score))

    def _select_best_candidates(self, *, ocr_candidates: list[ImportedMeeting], vision_candidates: list[ImportedMeeting]) -> list[ImportedMeeting]:
        # Favor vision candidates when OCR extraction creates too many noisy meetings.
        if not ocr_candidates:
            return vision_candidates
        ocr_quality = sum(self._meeting_quality(item) for item in ocr_candidates) / len(ocr_candidates)
        vision_quality = sum(self._meeting_quality(item) for item in vision_candidates) / len(vision_candidates)
        if vision_quality >= ocr_quality or len(ocr_candidates) > len(vision_candidates) + 2:
            return vision_candidates
        return ocr_candidates

    def _keep_best_per_slot(self, meetings: list[ImportedMeeting]) -> list[ImportedMeeting]:
        bucketed: dict[tuple[str, str], ImportedMeeting] = {}
        for meeting in meetings:
            bucket = (
                meeting.meeting_start.strftime("%Y-%m-%d %H:%M"),
                re.sub(r"\s+", " ", meeting.title.lower()).strip(),
            )
            current = bucketed.get(bucket)
            if current is None or len(meeting.title) > len(current.title):
                bucketed[bucket] = meeting
        result = sorted(bucketed.values(), key=lambda m: m.meeting_start)
        # If many vague generic meetings remain, collapse by 30-minute slot to keep only one per slot.
        collapsed: dict[str, ImportedMeeting] = {}
        for meeting in result:
            slot = meeting.meeting_start.strftime("%Y-%m-%d %H:%M")
            current = collapsed.get(slot)
            if current is None or self._title_quality(meeting.title) > self._title_quality(current.title):
                collapsed[slot] = meeting
        return sorted(collapsed.values(), key=lambda m: m.meeting_start)[:6]

    def _dedupe_meetings(self, meetings: list[ImportedMeeting], *, limit: int = 12) -> list[ImportedMeeting]:
        deduped: list[ImportedMeeting] = []
        seen = set()
        for meeting in meetings:
            key = (
                meeting.meeting_start.strftime("%Y-%m-%d %H:%M"),
                re.sub(r"\s+", " ", meeting.title.lower()).strip(),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(meeting)
        return deduped[:limit]
