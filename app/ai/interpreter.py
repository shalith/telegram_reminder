from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.agent import RuleBasedInterpreter, build_reminder_context
from app.agent_schema import AgentDecision, PendingState
from app.ai.prompts import PROMPT_VERSION, SYSTEM_PROMPT_V1, build_developer_prompt, build_user_prompt
from app.ai.schemas import FollowUp, InterpretationEnvelope, PendingConversationState, ReminderDraft, TargetSelector, PreferencePatch, get_interpretation_schema
from app.config import Settings
from app.models import Reminder

logger = logging.getLogger(__name__)

try:
    from groq import Groq
except Exception:  # pragma: no cover
    Groq = None  # type: ignore[assignment]


@dataclass(slots=True)
class InterpreterResult:
    envelope: InterpretationEnvelope
    raw_response_text: str | None
    model_name: str
    validation_ok: bool
    error_message: str | None = None


class StructuredInterpreter:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.rule_fallback = RuleBasedInterpreter()
        self.client = Groq(api_key=settings.groq_api_key) if settings.groq_enabled and Groq is not None else None


    def _coerce_raw_payload(self, raw_text: str) -> dict:
        try:
            payload = json.loads(raw_text or "{}")
        except Exception:
            return {}
        if not isinstance(payload, dict):
            return {}
        if payload.get("reminder") is None or not isinstance(payload.get("reminder"), dict):
            payload["reminder"] = {}
        if payload.get("target") is None or not isinstance(payload.get("target"), dict):
            payload["target"] = {}
        if payload.get("preferences") is None or not isinstance(payload.get("preferences"), dict):
            payload["preferences"] = {}
        if payload.get("follow_up") is None or not isinstance(payload.get("follow_up"), dict):
            payload["follow_up"] = {}
        if payload.get("reasoning_tags") is None or not isinstance(payload.get("reasoning_tags"), list):
            payload["reasoning_tags"] = []
        if payload.get("deadline_offsets") is None or not isinstance(payload.get("deadline_offsets"), list):
            payload["deadline_offsets"] = []
        return payload

    def interpret(
        self,
        *,
        message_text: str,
        timezone_name: str,
        pending_state: PendingConversationState | None,
        open_reminders: list[Reminder],
        preference_snapshot: str,
        learned_examples: list[str] | None = None,
        memory_profile_lines: list[str] | None = None,
    ) -> InterpreterResult:
        fallback_envelope = self._fallback_envelope(message_text=message_text, pending_state=pending_state)
        if self.client is None:
            return InterpreterResult(envelope=fallback_envelope, raw_response_text=None, model_name="rule-fallback", validation_ok=True)

        try:
            reminder_lines = build_reminder_context(open_reminders, timezone_name)
            developer_prompt = build_developer_prompt(
                timezone_name=timezone_name,
                preference_snapshot=preference_snapshot,
                recent_reminders=reminder_lines,
                pending_state=pending_state,
                learned_examples=learned_examples,
                memory_profile_lines=memory_profile_lines,
            )
            user_prompt = build_user_prompt(message_text)
            schema = get_interpretation_schema()
            response = self.client.chat.completions.create(
                model=self.settings.groq_model,
                temperature=0,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT_V1},
                    {"role": "developer", "content": developer_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "interpretation_envelope",
                        "schema": schema,
                    },
                },
            )
            raw = response.choices[0].message.content or "{}"
            payload = self._coerce_raw_payload(raw)
            envelope = InterpretationEnvelope.model_validate(payload)
            if envelope.action == "clarify" and fallback_envelope.action != "clarify":
                return InterpreterResult(envelope=fallback_envelope, raw_response_text=raw, model_name=self.settings.groq_model, validation_ok=True)
            return InterpreterResult(envelope=envelope, raw_response_text=raw, model_name=self.settings.groq_model, validation_ok=True)
        except Exception as exc:  # pragma: no cover
            logger.warning("Structured interpreter failed; falling back to rules: %s", exc)
            return InterpreterResult(envelope=fallback_envelope, raw_response_text=None, model_name="rule-fallback", validation_ok=False, error_message=str(exc))

    def _fallback_envelope(self, *, message_text: str, pending_state: PendingConversationState | None) -> InterpretationEnvelope:
        pending = None
        if pending_state is not None:
            pending = self._to_legacy_pending_state(pending_state)
        decision = self.rule_fallback.interpret(message_text=message_text, pending_state=pending)
        return self._from_legacy_decision(decision)

    def _to_legacy_pending_state(self, pending_state: PendingConversationState) -> PendingState | None:
        action_map = {
            "create_reminder": "create",
            "update_reminder": "update",
            "delete_reminder": "delete",
            "deadline_chain": "deadline_chain",
            "set_preferences": "set_preference",
        }
        legacy_intent = action_map.get(pending_state.action)
        if legacy_intent is None:
            return None
        preference_name = None
        preference_value = None
        prefs = pending_state.preferences
        if prefs.daily_agenda_time is not None:
            preference_name = "daily_agenda_time"
            preference_value = prefs.daily_agenda_time
        elif prefs.daily_agenda_enabled is not None:
            preference_name = "daily_agenda_enabled"
            preference_value = prefs.daily_agenda_enabled
        elif prefs.snooze_minutes is not None:
            preference_name = "default_snooze_minutes"
            preference_value = prefs.snooze_minutes
        elif prefs.wake_retry_minutes is not None:
            preference_name = "wakeup_retry_interval_minutes"
            preference_value = prefs.wake_retry_minutes
        elif prefs.wake_max_attempts is not None:
            preference_name = "wakeup_max_attempts"
            preference_value = prefs.wake_max_attempts
        elif prefs.missed_summary_enabled is not None:
            preference_name = "missed_summary_enabled"
            preference_value = prefs.missed_summary_enabled
        return PendingState(
            intent=legacy_intent,
            task=pending_state.reminder.task,
            time_phrase=pending_state.reminder.datetime_text,
            target_reminder_id=pending_state.target.reminder_id,
            target_hint=pending_state.target.selector_text,
            requires_ack=pending_state.reminder.requires_ack,
            ask_user=pending_state.follow_up.question,
            deadline_phrase=pending_state.reminder.datetime_text if pending_state.action == "deadline_chain" else None,
            deadline_offsets=[],
            preference_name=preference_name,
            preference_value=preference_value,
        )

    def _from_legacy_decision(self, decision: AgentDecision) -> InterpretationEnvelope:
        action_map = {
            "create": "create_reminder",
            "list": "list_reminders",
            "delete": "delete_reminder",
            "update": "update_reminder",
            "today_summary": "today_agenda",
            "set_preference": "set_preferences",
            "help": "help",
            "unknown": "clarify",
            "missed_summary": "missed_summary",
            "deadline_chain": "deadline_chain",
        }
        prefs = PreferencePatch()
        if decision.preference_name == "daily_agenda_time":
            prefs.daily_agenda_time = str(decision.preference_value) if decision.preference_value is not None else None
        elif decision.preference_name == "daily_agenda_enabled":
            prefs.daily_agenda_enabled = bool(decision.preference_value) if decision.preference_value is not None else None
        elif decision.preference_name == "default_snooze_minutes":
            prefs.snooze_minutes = int(decision.preference_value) if decision.preference_value is not None else None
        elif decision.preference_name == "wakeup_retry_interval_minutes":
            prefs.wake_retry_minutes = int(decision.preference_value) if decision.preference_value is not None else None
        elif decision.preference_name == "wakeup_max_attempts":
            prefs.wake_max_attempts = int(decision.preference_value) if decision.preference_value is not None else None
        elif decision.preference_name == "missed_summary_enabled":
            prefs.missed_summary_enabled = bool(decision.preference_value) if decision.preference_value is not None else None
        reminder = ReminderDraft(
            task=decision.task,
            datetime_text=decision.time_phrase or decision.deadline_phrase,
            recurrence_text=decision.time_phrase,
            timezone=None,
            is_wake_up=bool(decision.requires_ack),
            requires_ack=decision.requires_ack,
            priority="high" if decision.requires_ack else "normal",
        )
        target = TargetSelector(
            selector_text=decision.target_hint,
            reminder_id=decision.target_reminder_id,
            task_hint=decision.target_hint,
        )
        return InterpretationEnvelope(
            action=action_map.get(decision.intent, "clarify"),
            confidence=0.82 if decision.intent != "unknown" else 0.35,
            reminder=reminder,
            target=target,
            preferences=prefs,
            follow_up=FollowUp(needed=bool(decision.missing_fields), question=decision.ask_user, missing_fields=list(decision.missing_fields)),
            user_message_summary=decision.intent,
            reasoning_tags=["rule_fallback"],
            deadline_offsets=[],
        )
