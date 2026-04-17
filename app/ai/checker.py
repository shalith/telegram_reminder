from __future__ import annotations

from dataclasses import dataclass, field

from app.ai.confidence import compute_final_confidence
from app.ai.schemas import InterpretationEnvelope
from app.models import Reminder


@dataclass(slots=True)
class CheckerResult:
    ok: bool
    envelope: InterpretationEnvelope
    confidence: float
    issues: list[str] = field(default_factory=list)
    follow_up_text: str | None = None
    needs_target_resolution: bool = False


class InterpretationChecker:
    def check(self, *, envelope: InterpretationEnvelope, open_reminders: list[Reminder]) -> CheckerResult:
        issues: list[str] = []
        follow_up_text: str | None = None
        model_conf = envelope.confidence

        if envelope.reminder.is_wake_up:
            envelope.reminder.requires_ack = True

        if envelope.action == "create_reminder":
            if not envelope.reminder.task:
                issues.append("missing_task")
                follow_up_text = "What should I remind you about?"
            elif not envelope.reminder.datetime_text:
                issues.append("missing_datetime")
                follow_up_text = "When should I remind you?"

        elif envelope.action == "update_reminder":
            if envelope.target.reminder_id is None and not envelope.target.selector_text:
                issues.append("missing_target")
                follow_up_text = "Which reminder should I update?"
            elif not envelope.reminder.datetime_text:
                issues.append("missing_datetime")
                follow_up_text = "What should I change it to?"

        elif envelope.action == "delete_reminder":
            if envelope.target.reminder_id is None and not envelope.target.selector_text:
                issues.append("missing_target")
                follow_up_text = "Which reminder should I cancel?"

        elif envelope.action == "set_preferences":
            prefs = envelope.preferences
            if all(
                value is None
                for value in [
                    prefs.snooze_minutes,
                    prefs.wake_retry_minutes,
                    prefs.wake_max_attempts,
                    prefs.daily_agenda_time,
                    prefs.daily_agenda_enabled,
                    prefs.missed_summary_enabled,
                ]
            ):
                issues.append("missing_preference_value")
                follow_up_text = "What preference would you like me to set?"
            if prefs.snooze_minutes is not None and not 1 <= prefs.snooze_minutes <= 120:
                issues.append("invalid_snooze_minutes")
                follow_up_text = "Snooze must be between 1 and 120 minutes."
            if prefs.wake_retry_minutes is not None and not 1 <= prefs.wake_retry_minutes <= 60:
                issues.append("invalid_wake_retry_minutes")
                follow_up_text = "Wake-up retry interval must be between 1 and 60 minutes."
            if prefs.wake_max_attempts is not None and not 1 <= prefs.wake_max_attempts <= 30:
                issues.append("invalid_wake_max_attempts")
                follow_up_text = "Wake-up max attempts must be between 1 and 30."

        checker_penalty = 0.0
        if issues:
            checker_penalty += 0.3
        if envelope.action in {"update_reminder", "delete_reminder"} and envelope.target.reminder_id is None and envelope.target.selector_text and len(open_reminders) > 1:
            checker_penalty += 0.1

        confidence = compute_final_confidence(model_confidence=model_conf, checker_penalty=checker_penalty)
        needs_target_resolution = envelope.action in {"update_reminder", "delete_reminder"} and not issues
        return CheckerResult(
            ok=not issues,
            envelope=envelope,
            confidence=confidence,
            issues=issues,
            follow_up_text=follow_up_text,
            needs_target_resolution=needs_target_resolution,
        )
