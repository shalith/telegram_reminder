from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


ActionName = Literal[
    "create_reminder",
    "list_reminders",
    "update_reminder",
    "delete_reminder",
    "today_agenda",
    "set_preferences",
    "help",
    "clarify",
    "missed_summary",
    "deadline_chain",
]


class StrictBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReminderDraft(StrictBaseModel):
    task: str | None = None
    datetime_text: str | None = None
    recurrence_text: str | None = None
    timezone: str | None = None
    is_wake_up: bool = False
    requires_ack: bool | None = None
    priority: Literal["low", "normal", "high"] | None = None


class TargetSelector(StrictBaseModel):
    selector_text: str | None = None
    reminder_id: int | None = None
    date_hint: str | None = None
    task_hint: str | None = None
    recurrence_hint: str | None = None


class PreferencePatch(StrictBaseModel):
    snooze_minutes: int | None = None
    wake_retry_minutes: int | None = None
    wake_max_attempts: int | None = None
    quiet_hours_start: str | None = None
    quiet_hours_end: str | None = None
    daily_agenda_time: str | None = None
    daily_agenda_enabled: bool | None = None
    missed_summary_enabled: bool | None = None


class DeadlineOffset(StrictBaseModel):
    value: int
    unit: Literal["minutes", "hours", "days"]


class FollowUp(StrictBaseModel):
    needed: bool = False
    question: str | None = None
    missing_fields: list[str] = Field(default_factory=list)


class InterpretationEnvelope(StrictBaseModel):
    action: ActionName = "clarify"
    confidence: float = Field(default=0.0, ge=0, le=1)
    reminder: ReminderDraft = Field(default_factory=ReminderDraft)
    target: TargetSelector = Field(default_factory=TargetSelector)
    preferences: PreferencePatch = Field(default_factory=PreferencePatch)
    follow_up: FollowUp = Field(default_factory=FollowUp)
    user_message_summary: str | None = None
    reasoning_tags: list[str] = Field(default_factory=list)
    deadline_offsets: list[DeadlineOffset] = Field(default_factory=list)


class PendingConversationState(StrictBaseModel):
    action: ActionName
    reminder: ReminderDraft = Field(default_factory=ReminderDraft)
    target: TargetSelector = Field(default_factory=TargetSelector)
    preferences: PreferencePatch = Field(default_factory=PreferencePatch)
    follow_up: FollowUp = Field(default_factory=FollowUp)
    user_message_summary: str | None = None
    deadline_offsets: list[DeadlineOffset] = Field(default_factory=list)


class CandidateChoice(StrictBaseModel):
    reminder_id: int
    score: float
    label: str
    match_reason: str | None = None


class EvalCaseRecord(StrictBaseModel):
    label: str
    input_text: str
    expected_action: ActionName
    expected_json: dict | None = None


def get_interpretation_schema() -> dict:
    return InterpretationEnvelope.model_json_schema()
