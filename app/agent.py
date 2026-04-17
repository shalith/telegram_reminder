from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.agent_schema import AgentDecision, DeadlineOffset, PendingState
from app.assistant_features import parse_deadline_offsets
from app.ai.time_normalizer import looks_like_time_phrase, normalize_time_phrase
from app.parser import split_task_and_time_phrase
from app.config import Settings
from app.models import Reminder
from app.recurrence import format_dt_for_user, recurrence_label

logger = logging.getLogger(__name__)

try:
    from groq import Groq
except Exception:  # pragma: no cover
    Groq = None  # type: ignore[assignment]


HELP_HINTS = (
    "Try things like: remind me tomorrow at 7 PM to pay rent, "
    "wake me up every weekday at 6 AM, show my reminders, move my wake-up to 7 AM, "
    "what do I have today, send my daily agenda every day at 8 AM, "
    "or my report deadline is April 30 at 5 PM, remind me 7 days before and 2 hours before."
)


@dataclass(slots=True)
class ReminderContextItem:
    reminder_id: int
    task: str
    when_label: str
    recurrence: str
    requires_ack: bool

    def as_prompt_line(self) -> str:
        ack = "ack required" if self.requires_ack else "no ack"
        return f"#{self.reminder_id} | task={self.task} | next={self.when_label} | recurrence={self.recurrence} | {ack}"


class RuleBasedInterpreter:
    def interpret(
        self,
        *,
        message_text: str,
        pending_state: PendingState | None,
    ) -> AgentDecision:
        text = " ".join((message_text or "").strip().split())
        lower = text.lower()

        if pending_state is not None:
            merged = self._continue_pending(text=text, lower=lower, pending_state=pending_state)
            if merged is not None:
                return merged

        if not text:
            return AgentDecision(intent="unknown", ask_user=HELP_HINTS)

        if lower in {"help", "what can you do", "what do you support"}:
            return AgentDecision(intent="help")

        if lower.startswith("/"):
            return AgentDecision(intent="unknown")

        if any(phrase in lower for phrase in ["show my reminders", "list my reminders", "what are my reminders", "show my tasks", "list reminders"]):
            return AgentDecision(intent="list")

        if any(phrase in lower for phrase in ["what do i have today", "what's on today", "today agenda", "today's reminders", "my agenda today"]):
            return AgentDecision(intent="today_summary")

        if any(phrase in lower for phrase in ["what did i miss", "show missed reminders", "missed reminders", "what reminders did i miss"]):
            return AgentDecision(intent="missed_summary")

        preference_decision = self._parse_preference_intent(text, lower)
        if preference_decision is not None:
            return preference_decision

        deadline_decision = self._parse_deadline_chain_intent(text)
        if deadline_decision is not None:
            return deadline_decision

        if lower.startswith(("delete ", "cancel ", "remove ")) or " cancel my " in f" {lower} ":
            return self._parse_delete_intent(text)

        if lower.startswith(("move ", "change ", "reschedule ", "update ")):
            return self._parse_update_intent(text)

        if lower.startswith("wake me up"):
            time_phrase = text[len("wake me up") :].strip(" .")
            if not time_phrase:
                return AgentDecision(
                    intent="create",
                    task="wake up",
                    requires_ack=True,
                    missing_fields=["time_phrase"],
                    ask_user="What time should I wake you up?",
                )
            return AgentDecision(intent="create", task="wake up", time_phrase=time_phrase, requires_ack=True)

        if lower.startswith("remind me"):
            decision = self._parse_create_intent(text)
            if decision is not None:
                return decision

        if lower.startswith("every ") and " remind me" in lower:
            match = re.match(r"^(.+?) remind me(?: to)? (.+)$", text, re.IGNORECASE)
            if match:
                return AgentDecision(intent="create", task=match.group(2).strip(" ."), time_phrase=match.group(1).strip())

        return AgentDecision(intent="unknown")

    def _continue_pending(self, *, text: str, lower: str, pending_state: PendingState) -> AgentDecision | None:
        if pending_state.intent == "create":
            task = pending_state.task
            time_phrase = pending_state.time_phrase
            candidate_text = text.strip(" .")

            if candidate_text:
                parsed_task, parsed_time = split_task_and_time_phrase(candidate_text)
                if task is None and parsed_task:
                    task = parsed_task
                elif task is None and not looks_like_time_phrase(candidate_text):
                    task = candidate_text

                if time_phrase is None and parsed_time:
                    time_phrase = normalize_time_phrase(parsed_time)
                elif time_phrase is None and looks_like_time_phrase(candidate_text):
                    time_phrase = normalize_time_phrase(candidate_text)

            missing: list[str] = []
            ask_user = None
            if not task:
                missing.append("task")
                ask_user = "What should I remind you about?"
            elif not time_phrase:
                missing.append("time_phrase")
                ask_user = "When should I remind you?"
            return AgentDecision(intent="create", task=task, time_phrase=time_phrase, requires_ack=pending_state.requires_ack, missing_fields=missing, ask_user=ask_user)

        if pending_state.intent == "delete":
            return AgentDecision(intent="delete", target_hint=text.strip(" ."))

        if pending_state.intent == "update":
            target_hint = pending_state.target_hint
            time_phrase = pending_state.time_phrase
            if target_hint is None and text:
                target_hint = text.strip(" .")
            elif time_phrase is None and text:
                time_phrase = text.strip(" .")
            missing: list[str] = []
            ask_user = None
            if not target_hint and pending_state.target_reminder_id is None:
                missing.append("target")
                ask_user = "Which reminder should I update?"
            elif not time_phrase:
                missing.append("time_phrase")
                ask_user = "What should I change it to?"
            return AgentDecision(intent="update", target_reminder_id=pending_state.target_reminder_id, target_hint=target_hint, time_phrase=time_phrase, missing_fields=missing, ask_user=ask_user)

        if pending_state.intent == "deadline_chain":
            task = pending_state.task
            deadline_phrase = pending_state.deadline_phrase
            offsets = pending_state.deadline_offsets
            if task is None and text:
                task = text.strip(" .")
            elif deadline_phrase is None and text:
                deadline_phrase = text.strip(" .")
            elif not offsets and text:
                offsets = parse_deadline_offsets(text)
            missing: list[str] = []
            ask_user = None
            if not task:
                missing.append("task")
                ask_user = "What is the deadline for?"
            elif not deadline_phrase:
                missing.append("deadline_phrase")
                ask_user = "When is the deadline?"
            elif not offsets:
                missing.append("offsets")
                ask_user = "How far before the deadline should I remind you?"
            return AgentDecision(intent="deadline_chain", task=task, deadline_phrase=deadline_phrase, deadline_offsets=offsets, missing_fields=missing, ask_user=ask_user)

        if pending_state.intent == "set_preference":
            value: str | int | bool | None = pending_state.preference_value
            if value is None and text:
                value = text.strip(" .")
            missing: list[str] = []
            ask_user = None
            if value is None:
                missing.append("preference_value")
                ask_user = pending_state.ask_user or "What value should I use?"
            return AgentDecision(intent="set_preference", preference_name=pending_state.preference_name, preference_value=value, missing_fields=missing, ask_user=ask_user)
        return None

    def _parse_create_intent(self, text: str) -> AgentDecision | None:
        remainder = re.sub(r"^remind me\s+", "", text, flags=re.IGNORECASE).strip()
        if not remainder:
            return AgentDecision(intent="create", missing_fields=["task", "time_phrase"], ask_user=HELP_HINTS)

        task, time_phrase = split_task_and_time_phrase(remainder)
        missing: list[str] = []
        ask_user = None
        if not task:
            missing.append("task")
            ask_user = "What should I remind you about?"
        if not time_phrase:
            missing.append("time_phrase")
            ask_user = "When should I remind you?"
        return AgentDecision(
            intent="create",
            task=task or None,
            time_phrase=normalize_time_phrase(time_phrase) if time_phrase else None,
            missing_fields=missing,
            ask_user=ask_user,
        )

    def _parse_delete_intent(self, text: str) -> AgentDecision:
        id_match = re.search(r"#?(\d+)", text)
        if id_match:
            return AgentDecision(intent="delete", target_reminder_id=int(id_match.group(1)))

        hint = re.sub(r"^(delete|cancel|remove)\s+", "", text, flags=re.IGNORECASE).strip(" .")
        hint = re.sub(r"^(my|the)\s+", "", hint, flags=re.IGNORECASE).strip(" .")
        hint = re.sub(r"\s+reminder$", "", hint, flags=re.IGNORECASE).strip(" .")
        if not hint:
            return AgentDecision(intent="delete", missing_fields=["target"], ask_user="Which reminder should I cancel?")
        return AgentDecision(intent="delete", target_hint=hint)

    def _parse_update_intent(self, text: str) -> AgentDecision:
        id_match = re.search(r"#?(\d+)", text)
        target_id = int(id_match.group(1)) if id_match else None

        match = re.match(r"^(?:move|change|reschedule|update)\s+(.+?)\s+(?:to|for|at)\s+(.+)$", text, re.IGNORECASE)
        if match:
            target_hint = match.group(1).strip(" .")
            time_phrase = normalize_time_phrase(match.group(2).strip(" ."))
            target_hint = re.sub(r"^(my|the)\s+", "", target_hint, flags=re.IGNORECASE).strip(" .")
            target_hint = re.sub(r"\s+reminder$", "", target_hint, flags=re.IGNORECASE).strip(" .")
            return AgentDecision(intent="update", target_reminder_id=target_id, target_hint=target_hint or None, time_phrase=time_phrase or None)

        if target_id is not None:
            return AgentDecision(intent="update", target_reminder_id=target_id, missing_fields=["time_phrase"], ask_user="What should I change it to?")

        return AgentDecision(intent="update", missing_fields=["target", "time_phrase"], ask_user="Which reminder should I update, and what should I change it to?")

    def _parse_preference_intent(self, text: str, lower: str) -> AgentDecision | None:
        match = re.match(r"^(?:send|set) my daily agenda (?:every day )?(?:at|to) (.+)$", text, re.IGNORECASE)
        if match:
            return AgentDecision(intent="set_preference", preference_name="daily_agenda_time", preference_value=match.group(1).strip())

        if re.match(r"^(?:turn off|disable) my daily agenda$", lower):
            return AgentDecision(intent="set_preference", preference_name="daily_agenda_enabled", preference_value=False)

        if re.match(r"^(?:turn on|enable) my daily agenda$", lower):
            return AgentDecision(intent="set_preference", preference_name="daily_agenda_enabled", preference_value=True)

        match = re.match(r"^(?:set )?(?:my )?snooze(?: duration)? to (\d+) minutes?$", lower)
        if match:
            return AgentDecision(intent="set_preference", preference_name="default_snooze_minutes", preference_value=int(match.group(1)))

        match = re.match(r"^(?:set )?wake(?:-| )up (?:retry interval|retries) to (?:every )?(\d+) minutes?$", lower)
        if match:
            return AgentDecision(intent="set_preference", preference_name="wakeup_retry_interval_minutes", preference_value=int(match.group(1)))

        match = re.match(r"^(?:set )?wake(?:-| )up max attempts to (\d+)$", lower)
        if match:
            return AgentDecision(intent="set_preference", preference_name="wakeup_max_attempts", preference_value=int(match.group(1)))

        if re.match(r"^(?:turn off|disable) missed summary$", lower):
            return AgentDecision(intent="set_preference", preference_name="missed_summary_enabled", preference_value=False)

        if re.match(r"^(?:turn on|enable) missed summary$", lower):
            return AgentDecision(intent="set_preference", preference_name="missed_summary_enabled", preference_value=True)

        return None

    def _parse_deadline_chain_intent(self, text: str) -> AgentDecision | None:
        match = re.match(
            r"^(?:my\s+)?(.+?)\s+deadline is\s+(.+?)(?:,|;)?\s+remind me\s+(.+)$",
            text,
            re.IGNORECASE,
        )
        if not match:
            match = re.match(r"^deadline for\s+(.+?)\s+is\s+(.+?)(?:,|;)?\s+remind me\s+(.+)$", text, re.IGNORECASE)
        if not match:
            return None

        task = match.group(1).strip(" .")
        deadline_phrase = match.group(2).strip(" .")
        offsets = parse_deadline_offsets(match.group(3))
        missing: list[str] = []
        ask_user = None
        if not task:
            missing.append("task")
            ask_user = "What is the deadline for?"
        elif not deadline_phrase:
            missing.append("deadline_phrase")
            ask_user = "When is the deadline?"
        elif not offsets:
            missing.append("offsets")
            ask_user = "How far before the deadline should I remind you?"

        return AgentDecision(intent="deadline_chain", task=task or None, deadline_phrase=deadline_phrase or None, deadline_offsets=offsets, missing_fields=missing, ask_user=ask_user)


class GroqInterpreter:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.fallback = RuleBasedInterpreter()
        self.client = Groq(api_key=settings.groq_api_key) if settings.groq_enabled and Groq is not None else None

    def interpret(
        self,
        *,
        message_text: str,
        timezone_name: str,
        pending_state: PendingState | None,
        open_reminders: list[Reminder],
    ) -> AgentDecision:
        fallback = self.fallback.interpret(message_text=message_text, pending_state=pending_state)
        if self.client is None:
            return fallback

        try:
            reminder_lines = "\n".join(build_reminder_context(open_reminders, timezone_name)) or "(none)"
            pending_json = pending_state.model_dump_json() if pending_state is not None else "null"
            now_local = datetime.now(ZoneInfo(timezone_name)).strftime("%Y-%m-%d %H:%M:%S %Z")
            response = self.client.chat.completions.create(
                model=self.settings.groq_model,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an intent extractor for a Telegram reminder assistant. "
                            "Return only JSON with keys: intent, task, time_phrase, target_reminder_id, "
                            "target_hint, requires_ack, missing_fields, ask_user, deadline_phrase, "
                            "deadline_offsets, preference_name, preference_value. "
                            "Allowed intents: create, list, delete, update, today_summary, missed_summary, deadline_chain, set_preference, help, unknown. "
                            "Deadline offsets must be objects with value and unit (minutes/hours/days). "
                            "For wake-up requests, set task='wake up' and requires_ack=true. "
                            "If a field is missing, add it to missing_fields. "
                            "If there is a pending conversation state, use the user's message to fill it. "
                            "If the message clearly means list today's agenda, use today_summary."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Timezone: {timezone_name}\n"
                            f"Local now: {now_local}\n"
                            f"Pending state: {pending_json}\n"
                            f"Open reminders:\n{reminder_lines}\n\n"
                            f"User message: {message_text}"
                        ),
                    },
                ],
            )
            raw_content = response.choices[0].message.content or "{}"
            data = json.loads(raw_content)
            decision = AgentDecision.model_validate(data)
            if decision.intent == "unknown" and fallback.intent != "unknown":
                return fallback
            return decision
        except (ValidationError, json.JSONDecodeError, Exception) as exc:  # pragma: no cover
            logger.warning("Groq interpreter failed; falling back to rules: %s", exc)
            return fallback


class AgentInterpreter:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.impl = GroqInterpreter(settings) if settings.groq_enabled else RuleBasedInterpreter()

    def interpret(
        self,
        *,
        message_text: str,
        pending_state: PendingState | None,
        open_reminders: list[Reminder],
    ) -> AgentDecision:
        if isinstance(self.impl, GroqInterpreter):
            return self.impl.interpret(message_text=message_text, timezone_name=self.settings.default_timezone, pending_state=pending_state, open_reminders=open_reminders)
        return self.impl.interpret(message_text=message_text, pending_state=pending_state)



def build_reminder_context(reminders: list[Reminder], timezone_name: str) -> list[str]:
    lines: list[str] = []
    for reminder in reminders:
        when_label = "not scheduled"
        if reminder.next_run_at_utc is not None:
            when_label = format_dt_for_user(reminder.next_run_at_utc, timezone_name)
        item = ReminderContextItem(
            reminder_id=reminder.id,
            task=reminder.task,
            when_label=when_label,
            recurrence=recurrence_label(reminder),
            requires_ack=reminder.requires_ack,
        )
        lines.append(item.as_prompt_line())
    return lines
