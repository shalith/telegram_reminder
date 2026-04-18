from __future__ import annotations

from dataclasses import dataclass, field

from app.ai.checker import CheckerResult
from app.ai.schemas import InterpretationEnvelope
from app.models import Reminder
from app.phase7.conflict_detector import ConflictItem, SemanticConflictDetector


@dataclass(slots=True)
class EvaluatorDecision:
    adjusted_confidence: float
    force_confirmation: bool = False
    follow_up_text: str | None = None
    confirmation_text: str | None = None
    reasoning_tags: list[str] = field(default_factory=list)
    conflicts: list[ConflictItem] = field(default_factory=list)


class EvaluatorAgent:
    def __init__(self, detector: SemanticConflictDetector | None = None) -> None:
        self.detector = detector or SemanticConflictDetector()

    def evaluate(
        self,
        *,
        envelope: InterpretationEnvelope,
        checker_result: CheckerResult,
        open_reminders: list[Reminder],
        timezone_name: str,
    ) -> EvaluatorDecision:
        confidence = checker_result.confidence
        if checker_result.follow_up_text:
            return EvaluatorDecision(adjusted_confidence=confidence, follow_up_text=checker_result.follow_up_text)

        conflicts = self.detector.detect(envelope=envelope, open_reminders=open_reminders, timezone_name=timezone_name)
        if not conflicts:
            return EvaluatorDecision(adjusted_confidence=confidence)

        tags = ['phase7_evaluated']
        has_duplicate = any(item.code == 'possible_duplicate' for item in conflicts)
        has_overlap = any(item.code == 'time_overlap' for item in conflicts)
        if has_duplicate:
            tags.append('conflict_duplicate')
        if has_overlap:
            tags.append('conflict_overlap')
        if any(item.code == 'wake_up_tight_spacing' for item in conflicts):
            tags.append('conflict_wakeup_spacing')

        adjusted = min(confidence, 0.56 if has_duplicate else 0.62)
        confirmation_text = self._build_confirmation_text(envelope=envelope, conflicts=conflicts)
        return EvaluatorDecision(
            adjusted_confidence=adjusted,
            force_confirmation=True,
            confirmation_text=confirmation_text,
            reasoning_tags=tags,
            conflicts=conflicts,
        )

    def _build_confirmation_text(self, *, envelope: InterpretationEnvelope, conflicts: list[ConflictItem]) -> str:
        task = envelope.reminder.task or 'this task'
        when = envelope.reminder.datetime_text or 'that time'
        primary = conflicts[0].message if conflicts else 'This action may conflict with an existing reminder.'
        if envelope.action == 'create_reminder':
            return f"I understood this as a reminder for {task} at {when}. {primary} Confirm before I schedule it?"
        if envelope.action == 'update_reminder':
            return f"I understood this as an update for {task or 'that reminder'} to {when}. {primary} Confirm before I change it?"
        return f"{primary} Confirm before I continue?"
