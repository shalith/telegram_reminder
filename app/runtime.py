from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock
from typing import Any


def utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class RuntimeSnapshot:
    started_at: str
    last_update_received_at: str | None
    last_outbound_sent_at: str | None
    last_scheduler_activity_at: str | None
    inbound_message_count: int
    callback_count: int
    outbound_message_count: int
    reminder_fire_count: int
    daily_agenda_sent_count: int
    error_count: int
    last_error: str | None
    bot_started: bool


@dataclass(slots=True)
class RuntimeState:
    started_at: str = field(default_factory=utcnow_iso)
    last_update_received_at: str | None = None
    last_outbound_sent_at: str | None = None
    last_scheduler_activity_at: str | None = None
    inbound_message_count: int = 0
    callback_count: int = 0
    outbound_message_count: int = 0
    reminder_fire_count: int = 0
    daily_agenda_sent_count: int = 0
    error_count: int = 0
    last_error: str | None = None
    bot_started: bool = False
    _lock: Lock = field(default_factory=Lock, repr=False)

    def mark_bot_started(self) -> None:
        with self._lock:
            self.bot_started = True

    def record_inbound_message(self) -> None:
        with self._lock:
            self.inbound_message_count += 1
            self.last_update_received_at = utcnow_iso()

    def record_callback(self) -> None:
        with self._lock:
            self.callback_count += 1
            self.last_update_received_at = utcnow_iso()

    def record_outbound_message(self) -> None:
        with self._lock:
            self.outbound_message_count += 1
            self.last_outbound_sent_at = utcnow_iso()

    def record_scheduler_activity(self, *, reminder_fired: bool = False, daily_agenda_sent: bool = False) -> None:
        with self._lock:
            self.last_scheduler_activity_at = utcnow_iso()
            if reminder_fired:
                self.reminder_fire_count += 1
            if daily_agenda_sent:
                self.daily_agenda_sent_count += 1

    def record_error(self, message: str) -> None:
        with self._lock:
            self.error_count += 1
            self.last_error = message

    def snapshot(self) -> RuntimeSnapshot:
        with self._lock:
            return RuntimeSnapshot(
                started_at=self.started_at,
                last_update_received_at=self.last_update_received_at,
                last_outbound_sent_at=self.last_outbound_sent_at,
                last_scheduler_activity_at=self.last_scheduler_activity_at,
                inbound_message_count=self.inbound_message_count,
                callback_count=self.callback_count,
                outbound_message_count=self.outbound_message_count,
                reminder_fire_count=self.reminder_fire_count,
                daily_agenda_sent_count=self.daily_agenda_sent_count,
                error_count=self.error_count,
                last_error=self.last_error,
                bot_started=self.bot_started,
            )

    def as_dict(self) -> dict[str, Any]:
        snap = self.snapshot()
        return {
            "started_at": snap.started_at,
            "last_update_received_at": snap.last_update_received_at,
            "last_outbound_sent_at": snap.last_outbound_sent_at,
            "last_scheduler_activity_at": snap.last_scheduler_activity_at,
            "inbound_message_count": snap.inbound_message_count,
            "callback_count": snap.callback_count,
            "outbound_message_count": snap.outbound_message_count,
            "reminder_fire_count": snap.reminder_fire_count,
            "daily_agenda_sent_count": snap.daily_agenda_sent_count,
            "error_count": snap.error_count,
            "last_error": snap.last_error,
            "bot_started": snap.bot_started,
        }
