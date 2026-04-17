from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ReminderStatus(StrEnum):
    ACTIVE = "active"
    PENDING_ACK = "pending_ack"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    MISSED = "missed"


class RecurrenceType(StrEnum):
    ONCE = "once"
    DAILY = "daily"
    WEEKDAY = "weekday"
    WEEKLY = "weekly"


class PendingIntent(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    DEADLINE_CHAIN = "deadline_chain"
    PREFERENCE = "preference"


class Reminder(Base):
    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    task: Mapped[str] = mapped_column(String(500))
    original_text: Mapped[str] = mapped_column(Text())
    next_run_at_utc: Mapped[datetime | None] = mapped_column(DateTime(), index=True, nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Singapore")
    status: Mapped[str] = mapped_column(String(32), default=ReminderStatus.ACTIVE.value, index=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    recurrence_type: Mapped[str] = mapped_column(String(32), default=RecurrenceType.ONCE.value)
    recurrence_day_of_week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hour_local: Mapped[int | None] = mapped_column(Integer, nullable=True)
    minute_local: Mapped[int | None] = mapped_column(Integer, nullable=True)

    requires_ack: Mapped[bool] = mapped_column(Boolean, default=False)
    retry_interval_minutes: Mapped[int] = mapped_column(Integer, default=2)
    max_attempts: Mapped[int] = mapped_column(Integer, default=10)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_triggered_at_utc: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)

    group_token: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    missed_reported: Mapped[bool] = mapped_column(Boolean, default=False)

    normalized_task: Mapped[str | None] = mapped_column(String(500), nullable=True, index=True)
    semantic_key: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    last_ai_confidence: Mapped[float] = mapped_column(Float, default=0)
    last_interpretation_json: Mapped[str | None] = mapped_column(Text(), nullable=True)
    last_target_selector_json: Mapped[str | None] = mapped_column(Text(), nullable=True)
    source_mode: Mapped[str] = mapped_column(String(32), default="rule")

    created_at: Mapped[datetime] = mapped_column(DateTime(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(), server_default=func.now(), onupdate=func.now())


class ConversationState(Base):
    __tablename__ = "conversation_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    pending_intent: Mapped[str] = mapped_column(String(32), index=True)
    state_json: Mapped[str] = mapped_column(Text())
    created_at: Mapped[datetime] = mapped_column(DateTime(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(), server_default=func.now(), onupdate=func.now())


class UserPreference(Base):
    __tablename__ = "user_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Singapore")
    default_snooze_minutes: Mapped[int] = mapped_column(Integer, default=5)
    wakeup_retry_interval_minutes: Mapped[int] = mapped_column(Integer, default=2)
    wakeup_max_attempts: Mapped[int] = mapped_column(Integer, default=10)
    daily_agenda_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    daily_agenda_hour_local: Mapped[int | None] = mapped_column(Integer, nullable=True)
    daily_agenda_minute_local: Mapped[int | None] = mapped_column(Integer, nullable=True)
    missed_summary_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(), server_default=func.now(), onupdate=func.now())


class AiRun(Base):
    __tablename__ = "ai_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), server_default=func.now(), index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    message_text: Mapped[str] = mapped_column(Text())
    system_prompt_version: Mapped[str] = mapped_column(String(64), default="phase6_v1")
    model_name: Mapped[str] = mapped_column(String(128))
    raw_response_text: Mapped[str | None] = mapped_column(Text(), nullable=True)
    parsed_json: Mapped[str | None] = mapped_column(Text(), nullable=True)
    validation_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    checker_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    final_action: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text(), nullable=True)


class TargetResolutionCandidate(Base):
    __tablename__ = "target_resolution_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ai_run_id: Mapped[int] = mapped_column(ForeignKey("ai_runs.id"), index=True)
    reminder_id: Mapped[int] = mapped_column(ForeignKey("reminders.id"), index=True)
    score: Mapped[float] = mapped_column(Float)
    match_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
    action_name: Mapped[str] = mapped_column(String(64), default="update_reminder")


class ActionAuditLog(Base):
    __tablename__ = "action_audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), server_default=func.now(), index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    reminder_id: Mapped[int | None] = mapped_column(ForeignKey("reminders.id"), nullable=True)
    action_name: Mapped[str] = mapped_column(String(64))
    action_args_json: Mapped[str | None] = mapped_column(Text(), nullable=True)
    executor_result_json: Mapped[str | None] = mapped_column(Text(), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")


class EvalCase(Base):
    __tablename__ = "eval_cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    label: Mapped[str] = mapped_column(String(128))
    input_text: Mapped[str] = mapped_column(Text())
    expected_action: Mapped[str] = mapped_column(String(64))
    expected_json: Mapped[str | None] = mapped_column(Text(), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
