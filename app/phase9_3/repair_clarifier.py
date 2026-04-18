from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.ai.time_normalizer import looks_like_time_phrase, normalize_time_phrase


@dataclass(slots=True)
class RepairRewrite:
    message_text: str
    reason: str
    handled_as_follow_up: bool = False


@dataclass(slots=True)
class ClarificationRequest:
    text: str
    reminder_ids: list[int] = field(default_factory=list)
    reason: str | None = None


class ConversationRepairAndClarifier:
    TIME_ONLY_PATTERNS = (
        'change only the time',
        'keep the task, change the time',
        'keep same task change the time',
        'same task, change the time',
        'same task change time',
        'keep the task',
    )
    DATE_ONLY_PATTERNS = (
        'change only the date',
        'keep the task, change the date',
        'keep same task change the date',
        'same task, change the date',
        'same task change date',
        'use tomorrow instead',
        'use today instead',
    )
    SAME_TIME_PATTERNS = ('same time', 'keep same time', 'keep the same time')
    SAME_TASK_PATTERNS = ('same task', 'keep same task', 'keep the same task')

    def maybe_rewrite(self, text: str, *, current_task: str | None, current_time_phrase: str | None) -> RepairRewrite | None:
        cleaned = ' '.join((text or '').strip().split())
        lowered = cleaned.lower()
        if not cleaned:
            return None

        m = re.search(r'\b(?:no,?\s*)?i meant\s+(.+)$', cleaned, re.IGNORECASE)
        if m:
            candidate = m.group(1).strip(' .')
            if looks_like_time_phrase(candidate):
                return RepairRewrite(message_text=candidate, reason='meant_time', handled_as_follow_up=True)
            if current_task:
                return RepairRewrite(message_text=f'{current_task} {candidate}', reason='meant_rewrite')

        if any(p in lowered for p in self.TIME_ONLY_PATTERNS):
            return RepairRewrite(message_text='__CHANGE_TIME_ONLY__', reason='time_only_change')
        if any(p in lowered for p in self.DATE_ONLY_PATTERNS):
            return RepairRewrite(message_text='__CHANGE_DATE_ONLY__', reason='date_only_change')
        if any(p == lowered for p in self.SAME_TIME_PATTERNS) and current_task:
            return RepairRewrite(message_text=current_task, reason='same_time_keep_task')
        if any(p == lowered for p in self.SAME_TASK_PATTERNS) and current_time_phrase:
            return RepairRewrite(message_text=current_time_phrase, reason='same_task_keep_time', handled_as_follow_up=True)

        m = re.search(r'\b(?:no,?\s*)?not\s+(today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b', lowered)
        if m and current_time_phrase:
            wrong = m.group(1)
            swapped = self._swap_day_phrase(current_time_phrase, wrong)
            if swapped and swapped != current_time_phrase:
                return RepairRewrite(message_text=swapped, reason='day_swap', handled_as_follow_up=True)

        if lowered.startswith('change it to ') and current_task:
            candidate = cleaned[13:].strip()
            if looks_like_time_phrase(candidate):
                return RepairRewrite(message_text=candidate, reason='change_it_to_time', handled_as_follow_up=True)
            return RepairRewrite(message_text=f'update reminder to {candidate}', reason='change_it_to_rewrite')

        return None

    def build_reference_clarification(self, *, text: str, candidate_reminders: list) -> ClarificationRequest | None:
        lowered = ' '.join((text or '').strip().lower().split())
        if not candidate_reminders:
            return None
        if 'not that one' in lowered or 'which one' in lowered:
            return ClarificationRequest(
                text=self._clarification_text(candidate_reminders, intro='Which reminder do you mean?'),
                reminder_ids=[r.id for r in candidate_reminders],
                reason='explicit_not_that_one',
            )
        if any(token in lowered for token in ('that one', 'this one', 'it', 'the one')) and len(candidate_reminders) > 1:
            return ClarificationRequest(
                text=self._clarification_text(candidate_reminders, intro='I found multiple matching reminders.'),
                reminder_ids=[r.id for r in candidate_reminders],
                reason='ambiguous_reference',
            )
        return None

    def _clarification_text(self, reminders: list, *, intro: str) -> str:
        lines = [intro, 'Reply with the reminder number, for example: 2']
        for idx, reminder in enumerate(reminders[:5], start=1):
            when = reminder.next_run_at_utc.isoformat(sep=' ', timespec='minutes') if reminder.next_run_at_utc else 'no time'
            lines.append(f'{idx}. #{reminder.id} — {reminder.task} ({when})')
        return '\n'.join(lines)

    def _swap_day_phrase(self, current: str, wrong_day: str) -> str | None:
        mapping = {
            'today': 'tomorrow',
            'tomorrow': 'today',
        }
        if wrong_day in mapping:
            return re.sub(rf'\b{re.escape(wrong_day)}\b', mapping[wrong_day], current, flags=re.IGNORECASE)
        return None
