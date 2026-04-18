from __future__ import annotations

from app.ai.schemas import PendingConversationState

PROMPT_VERSION = "phase6_4_semantic_repair_v1"

SYSTEM_PROMPT_V1 = """You are the interpreter for a Telegram reminder assistant.
Convert each user message into a strict JSON object that matches the provided schema.
Rules:
- Never invent a datetime when it is missing.
- Use follow_up.needed=true when required information is missing.
- For update/delete, identify the most likely target reminder using the target fields.
- If the message is ambiguous, do not guess.
- Prefer minimal action. Do not update or delete multiple reminders unless the user explicitly asks.
- Keep confidence conservative.
- Be good at task-first reminder phrasing like 'remind me to go for sony headset repair today morning 9am'.
- Also understand indirect reminder phrasing like 'I need to go for Sony headset repair around 2pm' or 'I want to go for gym today 2am'.
- Treat words like around/about/approximately as approximate times. Prefer confirmation over blind scheduling.
- If the time looks unusual for the activity, keep confidence lower so the app can confirm it.
- When a pending follow-up exists, treat the new user message as a slot-filling answer unless it clearly starts a different request.
- Output only valid JSON matching the schema.
"""


def build_developer_prompt(
    *,
    timezone_name: str,
    preference_snapshot: str,
    recent_reminders: list[str],
    pending_state: PendingConversationState | None,
    learned_examples: list[str] | None = None,
) -> str:
    reminder_text = "\n".join(recent_reminders) if recent_reminders else "(none)"
    pending_json = pending_state.model_dump_json(indent=2) if pending_state is not None else "null"
    example_text = "\n".join(learned_examples or []) or "(none)"
    return (
        f"Prompt version: {PROMPT_VERSION}\n"
        f"Current timezone: {timezone_name}\n"
        f"Known preferences:\n{preference_snapshot}\n\n"
        f"Recent reminders:\n{reminder_text}\n\n"
        f"Learned successful examples:\n{example_text}\n\n"
        f"Pending conversation state:\n{pending_json}"
    )


def build_user_prompt(raw_message: str) -> str:
    return f"User message:\n{raw_message}"
