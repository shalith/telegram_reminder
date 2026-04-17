from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


IntentName = Literal[
    "create",
    "list",
    "delete",
    "update",
    "help",
    "unknown",
    "today_summary",
    "missed_summary",
    "deadline_chain",
    "set_preference",
]
FieldName = Literal["task", "time_phrase", "target", "deadline_phrase", "offsets", "preference_value"]
PreferenceName = Literal[
    "daily_agenda_time",
    "daily_agenda_enabled",
    "default_snooze_minutes",
    "wakeup_retry_interval_minutes",
    "wakeup_max_attempts",
    "missed_summary_enabled",
]
OffsetUnit = Literal["minutes", "hours", "days"]


class DeadlineOffset(BaseModel):
    value: int
    unit: OffsetUnit


class AgentDecision(BaseModel):
    intent: IntentName = "unknown"
    task: str | None = None
    time_phrase: str | None = None
    target_reminder_id: int | None = None
    target_hint: str | None = None
    requires_ack: bool | None = None
    missing_fields: list[FieldName] = Field(default_factory=list)
    ask_user: str | None = None

    deadline_phrase: str | None = None
    deadline_offsets: list[DeadlineOffset] = Field(default_factory=list)

    preference_name: PreferenceName | None = None
    preference_value: str | int | bool | None = None


class PendingState(BaseModel):
    intent: Literal["create", "update", "delete", "deadline_chain", "set_preference"]
    task: str | None = None
    time_phrase: str | None = None
    target_reminder_id: int | None = None
    target_hint: str | None = None
    requires_ack: bool | None = None
    ask_user: str | None = None

    deadline_phrase: str | None = None
    deadline_offsets: list[DeadlineOffset] = Field(default_factory=list)

    preference_name: PreferenceName | None = None
    preference_value: str | int | bool | None = None
