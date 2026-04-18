from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable
from zoneinfo import ZoneInfo

import dateparser

from app.ai.schemas import FollowUp, InterpretationEnvelope, ReminderDraft, TargetSelector, PreferencePatch
from app.ai.time_normalizer import contains_approximate_time_language, looks_like_time_phrase, normalize_time_phrase
from app.parser import cleanup_task_prefix, split_task_and_time_phrase

INDIRECT_PREFIXES = (
    "i need to ",
    "i need ",
    "need to ",
    "need ",
    "i have to ",
    "have to ",
    "i want to ",
    "want to ",
    "i gotta ",
    "gotta ",
    "i should ",
    "should ",
)

DAYTIME_TASK_KEYWORDS = {
    "gym",
    "workout",
    "repair",
    "doctor",
    "dentist",
    "meeting",
    "bank",
    "office",
    "shopping",
    "call",
    "pickup",
    "drop off",
    "submit",
    "buy",
    "sony",
}

REPAIR_FULL_RE = re.compile(
    r"(?:i\s+meant|meant|actually\s+meant)\s+(?P<correct>(?:around\s+|about\s+|approximately\s+)?[\w\s:]+?(?:am|pm))\s*,?\s*(?:not|instead\s+of)\s+(?P<wrong>(?:around\s+|about\s+|approximately\s+)?[\w\s:]+?(?:am|pm))",
    re.IGNORECASE,
)
REPAIR_SIMPLE_RE = re.compile(
    r"(?:not|wrong|wrongly|typo|mistake|i typed it wrongly|i said .* wrongly)", re.IGNORECASE
)
TIME_IN_TEXT_RE = re.compile(r"(?:around\s+|about\s+|approximately\s+)?(?:today|tomorrow|tonight)?\s*(?:morning|afternoon|evening|night)?\s*\d{1,2}(?::\d{2})?\s*(?:am|pm)", re.IGNORECASE)


@dataclass(slots=True)
class RepairSignal:
    corrected_time_phrase: str | None = None
    mistaken_time_phrase: str | None = None
    needs_follow_up: bool = False
    ask_user: str | None = None


def infer_indirect_reminder(text: str) -> InterpretationEnvelope | None:
    cleaned = " ".join((text or "").strip().split())
    lowered = cleaned.lower()
    if not cleaned or lowered.startswith("remind me") or lowered.startswith("wake"):
        return None

    prefix = next((p for p in INDIRECT_PREFIXES if lowered.startswith(p)), None)
    if prefix is None:
        return None

    remainder = cleaned[len(prefix):].strip(" .")
    if not remainder:
        return None

    task, time_phrase = split_task_and_time_phrase(remainder)
    if time_phrase is None and looks_like_time_phrase(remainder):
        time_phrase = remainder
        task = None

    if not time_phrase:
        return None

    final_task = cleanup_task_prefix(task or remainder)
    final_task = re.sub(r"\b(?:around|about|approximately|approx(?:\.)?|ish)\b\s*$", "", final_task, flags=re.IGNORECASE).strip(" .")
    if not final_task:
        return None

    tags = ["indirect_intent"]
    if contains_approximate_time_language(time_phrase):
        tags.append("approximate_time")

    return InterpretationEnvelope(
        action="create_reminder",
        confidence=0.60,
        reminder=ReminderDraft(
            task=final_task,
            datetime_text=time_phrase,
            recurrence_text=time_phrase,
            is_wake_up=False,
            requires_ack=False,
            priority="normal",
        ),
        target=TargetSelector(),
        preferences=PreferencePatch(),
        follow_up=FollowUp(needed=False, question=None, missing_fields=[]),
        user_message_summary="indirect_reminder_request",
        reasoning_tags=tags,
        deadline_offsets=[],
    )


def apply_semantic_judgment(message_text: str, envelope: InterpretationEnvelope, timezone_name: str) -> InterpretationEnvelope:
    tags = list(envelope.reasoning_tags)
    lowered = (message_text or "").lower()

    if envelope.action in {"create_reminder", "update_reminder"}:
        if contains_approximate_time_language(message_text) or contains_approximate_time_language(envelope.reminder.datetime_text or ""):
            _append_tag(tags, "approximate_time")
            envelope.confidence = min(envelope.confidence, 0.64)

        if _is_suspicious_time_for_task(
            task=envelope.reminder.task or "",
            time_phrase=envelope.reminder.datetime_text or "",
            timezone_name=timezone_name,
        ):
            _append_tag(tags, "suspicious_time")
            _append_tag(tags, "am_pm_risk")
            envelope.confidence = min(envelope.confidence, 0.58)

        if envelope.reminder.is_wake_up:
            _append_tag(tags, "wake_up_semantic")

    if "wrongly" in lowered or "mistake" in lowered or "typo" in lowered:
        _append_tag(tags, "repair_hint")

    envelope.reasoning_tags = tags
    return envelope


def detect_repair_signal(text: str) -> RepairSignal | None:
    cleaned = " ".join((text or "").strip().split())
    if not cleaned:
        return None

    full = REPAIR_FULL_RE.search(cleaned)
    if full:
        corrected = normalize_time_phrase(full.group("correct"))
        wrong = normalize_time_phrase(full.group("wrong"))
        return RepairSignal(corrected_time_phrase=corrected, mistaken_time_phrase=wrong, needs_follow_up=False)

    if REPAIR_SIMPLE_RE.search(cleaned):
        return RepairSignal(needs_follow_up=True, ask_user="Understood — what time should I use instead?")

    return None


def should_confirm_for_semantics(envelope: InterpretationEnvelope) -> bool:
    tags = set(envelope.reasoning_tags)
    return bool(tags & {"approximate_time", "suspicious_time", "am_pm_risk", "indirect_intent", "repair_conversation"})


def build_semantic_confirmation_text(envelope: InterpretationEnvelope) -> str | None:
    tags = set(envelope.reasoning_tags)
    if not tags:
        return None

    task = envelope.reminder.task or "this task"
    when = envelope.reminder.datetime_text or "that time"

    display_when = when
    if "approximate_time" in tags and not contains_approximate_time_language(display_when):
        display_when = f"about {display_when}"

    if "suspicious_time" in tags and "am_pm_risk" in tags:
        return f"I understood this as a reminder to {task} at {display_when}, but that time seems unusual. Confirm or edit it before I schedule it?"
    if "approximate_time" in tags:
        return f"I understood this as a reminder to {task} at {display_when}. Confirm the time before I schedule it?"
    if "indirect_intent" in tags:
        return f"It sounds like you want a reminder to {task} at {display_when}. Should I create it?"
    if "repair_conversation" in tags:
        return f"I understood this as updating your reminder to {when}. Confirm the change?"
    return None


def _append_tag(tags: list[str], tag: str) -> None:
    if tag not in tags:
        tags.append(tag)


def _is_suspicious_time_for_task(*, task: str, time_phrase: str, timezone_name: str) -> bool:
    lowered_task = task.lower()
    if not any(keyword in lowered_task for keyword in DAYTIME_TASK_KEYWORDS):
        return False
    if not time_phrase:
        return False
    parsed = dateparser.parse(
        normalize_time_phrase(time_phrase),
        settings={
            "PREFER_DATES_FROM": "future",
            "TIMEZONE": timezone_name,
            "RETURN_AS_TIMEZONE_AWARE": True,
        },
    )
    if parsed is None:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
    hour = parsed.astimezone(ZoneInfo(timezone_name)).hour
    return 0 <= hour < 6
