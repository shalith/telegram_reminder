from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from app.ai.normalizer import normalize_selector, normalize_task
from app.models import Reminder
from app.recurrence import recurrence_label


@dataclass(slots=True)
class ResolutionCandidate:
    reminder: Reminder
    score: float
    reason: str


@dataclass(slots=True)
class ResolutionResult:
    status: str
    candidates: list[ResolutionCandidate]
    selected: Reminder | None = None
    message: str | None = None


class TargetResolver:
    def resolve(self, *, selector_text: str | None, reminder_id: int | None, reminders: list[Reminder]) -> ResolutionResult:
        if not reminders:
            return ResolutionResult(status="none", candidates=[], message="You don't have any open reminders right now.")

        if reminder_id is not None:
            for reminder in reminders:
                if reminder.id == reminder_id:
                    return ResolutionResult(status="single", candidates=[ResolutionCandidate(reminder, 1.0, "exact id")], selected=reminder)
            return ResolutionResult(status="none", candidates=[], message=f"I couldn't find an open reminder with ID {reminder_id}.")

        hint = normalize_selector(selector_text)
        if not hint:
            if len(reminders) == 1:
                only = reminders[0]
                return ResolutionResult(status="single", candidates=[ResolutionCandidate(only, 0.9, "single open reminder")], selected=only)
            return ResolutionResult(status="ambiguous", candidates=[], message="I need to know which reminder you mean.")

        candidates: list[ResolutionCandidate] = []
        for reminder in reminders:
            haystack = self._target_text(reminder)
            score = SequenceMatcher(None, hint, haystack).ratio()
            reason = "similar text"
            if hint in haystack:
                score += 0.25
                reason = "text match"
            if reminder.requires_ack and "wake" in hint:
                score += 0.2
                reason = "wake-up match"
            if str(reminder.id) == hint.replace('#', ''):
                score = 1.0
                reason = "id-like match"
            candidates.append(ResolutionCandidate(reminder=reminder, score=round(min(score, 1.0), 3), reason=reason))

        candidates.sort(key=lambda item: item.score, reverse=True)
        if not candidates or candidates[0].score < 0.45:
            return ResolutionResult(status="none", candidates=candidates[:3], message="I couldn't match that to one of your open reminders.")

        if len(candidates) == 1 or candidates[0].score - candidates[1].score >= 0.08:
            return ResolutionResult(status="single", candidates=candidates[:3], selected=candidates[0].reminder)

        return ResolutionResult(status="ambiguous", candidates=candidates[:3], message="That matches more than one reminder. Please choose one.")

    def _target_text(self, reminder: Reminder) -> str:
        pieces = [normalize_task(reminder.task), normalize_selector(recurrence_label(reminder)), f"#{reminder.id}"]
        if reminder.requires_ack:
            pieces.append("wake up")
        return " ".join([piece for piece in pieces if piece])
