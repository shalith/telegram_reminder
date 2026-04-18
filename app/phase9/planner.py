from __future__ import annotations

import re
from typing import Iterable

from pydantic import BaseModel, Field

from app.ai.time_normalizer import looks_like_time_phrase, normalize_time_phrase
from app.parser import parse_schedule_components, split_task_and_time_phrase


DATE_PREFIX_RE = re.compile(
    r'^\s*(today|tomorrow|tonight|next\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|this\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b',
    re.IGNORECASE,
)

LEADING_MULTI_RE = [
    re.compile(r'^(?P<context>today|tomorrow|tonight|next\s+\w+|this\s+\w+)\s+remind me(?:\s+about|\s+to)?\s+(?P<body>.+)$', re.IGNORECASE),
    re.compile(r'^(?P<context>today|tomorrow|tonight|next\s+\w+|this\s+\w+)\s+i\s+(?:have|need to|want to)\s+(?P<body>.+)$', re.IGNORECASE),
    re.compile(r'^remind me(?:\s+about|\s+to)?\s+(?P<body>.+)$', re.IGNORECASE),
    re.compile(r'^(?:i\s+have|i\s+need to|i\s+want to)\s+(?P<body>.+)$', re.IGNORECASE),
]

TIME_CLAUSE_RE = re.compile(
    r'\b(?:at\s+.+|in\s+the\s+(?:morning|afternoon|evening|night)|(?:today|tomorrow|tonight|next\s+\w+|this\s+\w+)\b.+|morning\b.+|afternoon\b.+|evening\b.+|night\b.+)\s*$',
    re.IGNORECASE,
)


class MultiPlanItem(BaseModel):
    task: str
    time_phrase: str
    requires_ack: bool = False


class MultiPlanProposal(BaseModel):
    source_message_text: str
    items: list[MultiPlanItem] = Field(default_factory=list)
    confidence: float = 0.0
    shared_context: str | None = None

    def summary_lines(self) -> list[str]:
        return [f"• {item.task} — {item.time_phrase}" for item in self.items]


class MultiPlanConfirmationState(BaseModel):
    source_message_text: str
    items: list[MultiPlanItem] = Field(default_factory=list)
    confidence: float = 0.0
    shared_context: str | None = None


class MultiReminderPlanner:
    def detect(self, message_text: str, *, timezone_name: str) -> MultiPlanProposal | None:
        raw = ' '.join((message_text or '').strip().split())
        if not raw:
            return None
        if raw.lower().startswith(('wake me up', 'wake up me', 'wake up')):
            return None

        body, shared_context = self._extract_body_and_context(raw)
        if body is None:
            return None

        parts = self._split_items(body)
        if len(parts) < 2:
            return None

        items: list[MultiPlanItem] = []
        for part in parts:
            parsed = self._parse_item(part, shared_context=shared_context, timezone_name=timezone_name)
            if parsed is None:
                return None
            items.append(parsed)

        if len(items) < 2:
            return None
        confidence = 0.84 if shared_context else 0.76
        return MultiPlanProposal(
            source_message_text=raw,
            items=items,
            confidence=confidence,
            shared_context=shared_context,
        )

    def _extract_body_and_context(self, raw: str) -> tuple[str | None, str | None]:
        for pattern in LEADING_MULTI_RE:
            match = pattern.match(raw)
            if match:
                body = (match.groupdict().get('body') or '').strip(' .')
                context = match.groupdict().get('context')
                return body, context.strip() if context else None
        if raw.count(',') >= 1 and re.search(r'\band\b', raw, re.IGNORECASE):
            context_match = DATE_PREFIX_RE.match(raw)
            context = context_match.group(1) if context_match else None
            body = raw[context_match.end():].strip(' ,') if context_match else raw
            return body, context
        return None, None

    def _split_items(self, body: str) -> list[str]:
        normalized = body.strip(' .')
        normalized = re.sub(r'\s+,\s+', ',', normalized)
        normalized = re.sub(r',\s+and\s+', ',', normalized, flags=re.IGNORECASE)
        normalized = re.sub(r'\s+and\s+', ',', normalized, flags=re.IGNORECASE)
        parts = [self._cleanup_item(part) for part in normalized.split(',') if part.strip()]
        return [part for part in parts if part]

    def _cleanup_item(self, value: str) -> str:
        cleaned = value.strip(' .')
        cleaned = re.sub(r'^(also\s+|then\s+)', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'^(about|to)\s+', '', cleaned, flags=re.IGNORECASE)
        return cleaned.strip(' .')

    def _parse_item(self, chunk: str, *, shared_context: str | None, timezone_name: str) -> MultiPlanItem | None:
        task, time_phrase = split_task_and_time_phrase(chunk)
        if not task or not time_phrase:
            task, time_phrase = self._fallback_extract(chunk, shared_context=shared_context)
        if not task or not time_phrase:
            return None
        normalized_time = self._merge_shared_context(time_phrase, shared_context)
        parsed = parse_schedule_components(
            task=task,
            time_phrase=normalized_time,
            timezone_name=timezone_name,
            requires_ack=False,
        )
        if not parsed.ok:
            return None
        return MultiPlanItem(task=task, time_phrase=normalized_time)

    def _fallback_extract(self, chunk: str, *, shared_context: str | None) -> tuple[str | None, str | None]:
        value = chunk.strip(' .')
        match = re.match(r'^(?P<task>.+?)\s+(?P<time>(?:at\s+)?(?:\d{1,2}(?::\d{2})?\s*(?:am|pm)?|morning(?:\s+\d{1,2}(?::\d{2})?)?|evening(?:\s+\d{1,2}(?::\d{2})?)?|afternoon(?:\s+\d{1,2}(?::\d{2})?)?|night(?:\s+\d{1,2}(?::\d{2})?)?|today\b.+|tomorrow\b.+|tonight\b.+|next\s+\w+\b.+|this\s+\w+\b.+|in\s+the\s+(?:morning|afternoon|evening|night)))$', value, re.IGNORECASE)
        if match:
            return match.group('task').strip(' .'), match.group('time').strip(' .')
        if shared_context and TIME_CLAUSE_RE.search(value):
            task, time_phrase = split_task_and_time_phrase(f"{value}")
            if task and time_phrase:
                return task, time_phrase
        return None, None

    def _merge_shared_context(self, time_phrase: str, shared_context: str | None) -> str:
        normalized = normalize_time_phrase(time_phrase)
        if not shared_context:
            return normalized
        lowered = normalized.lower()
        if lowered.startswith(('today', 'tomorrow', 'tonight', 'next ', 'this ')):
            return normalized
        return normalize_time_phrase(f"{shared_context} {normalized}")
