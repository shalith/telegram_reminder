from __future__ import annotations

from dataclasses import dataclass, field

from app.ai.normalizer import normalize_task
from app.ai.schemas import FollowUp, InterpretationEnvelope
from app.ai.time_normalizer import normalize_time_phrase
from app.phase8.memory_profile import MemoryMatch, MemoryProfileStore


@dataclass(slots=True)
class MemoryReasoningResult:
    adjusted_confidence: float
    reasons: list[str] = field(default_factory=list)
    follow_up_text: str | None = None
    memory_summary: str | None = None


class MemoryReasoner:
    def __init__(self) -> None:
        self.profile_store = MemoryProfileStore()

    def apply(
        self,
        *,
        envelope: InterpretationEnvelope,
        message_text: str,
        matched_profiles: list[MemoryMatch],
    ) -> MemoryReasoningResult:
        if not matched_profiles:
            return MemoryReasoningResult(adjusted_confidence=envelope.confidence)

        top = matched_profiles[0].profile
        top_time = self.profile_store.suggest_time_text(top)
        adjusted = float(envelope.confidence)
        reasons: list[str] = []
        follow_up_text: str | None = None
        memory_summary = f"You often mean '{top.sample_task or top.task_key}' around {top_time or top.preferred_time_of_day or 'that time'}."

        task_norm = normalize_task(envelope.reminder.task)
        if envelope.action == 'create_reminder' and task_norm and task_norm == (top.task_key or ''):
            adjusted = min(0.97, adjusted + 0.08)
            reasons.append('memory_task_match')

        if envelope.action == 'create_reminder' and not envelope.reminder.datetime_text:
            if top_time:
                follow_up_text = f"When should I remind you about {envelope.reminder.task or top.sample_task}? You usually set it around {top_time}."
            elif top.preferred_time_of_day:
                follow_up_text = f"When should I remind you about {envelope.reminder.task or top.sample_task}? You often set it in the {top.preferred_time_of_day}."
            if follow_up_text:
                reasons.append('memory_time_suggestion')

        if envelope.action == 'create_reminder' and envelope.reminder.datetime_text:
            normalized_time = normalize_time_phrase(envelope.reminder.datetime_text)
            lower_time = normalized_time.lower()
            preferred_period = (top.preferred_time_of_day or '').lower()
            if preferred_period and preferred_period in lower_time:
                adjusted = min(0.97, adjusted + 0.05)
                reasons.append('memory_period_match')
            elif preferred_period and top.use_count and int(top.use_count) >= 3:
                adjusted = max(0.25, adjusted - 0.06)
                reasons.append('memory_period_mismatch')

        return MemoryReasoningResult(
            adjusted_confidence=max(0.0, min(1.0, adjusted)),
            reasons=reasons,
            follow_up_text=follow_up_text,
            memory_summary=memory_summary,
        )
