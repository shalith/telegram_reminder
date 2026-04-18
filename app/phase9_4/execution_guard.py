from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(slots=True)
class _SeenRecord:
    expires_at: float
    response_text: str | None = None


class ExecutionGuard:
    """Lightweight in-memory idempotency guard for callbacks and rapid repeat messages."""

    def __init__(self, *, callback_ttl_seconds: int = 180, message_ttl_seconds: int = 12):
        self.callback_ttl_seconds = callback_ttl_seconds
        self.message_ttl_seconds = message_ttl_seconds
        self._seen_callbacks: dict[str, _SeenRecord] = {}
        self._seen_messages: dict[str, _SeenRecord] = {}

    def _cleanup(self) -> None:
        now = time.monotonic()
        self._seen_callbacks = {k: v for k, v in self._seen_callbacks.items() if v.expires_at > now}
        self._seen_messages = {k: v for k, v in self._seen_messages.items() if v.expires_at > now}

    def mark_callback_started(self, *, callback_query_id: str, callback_data: str | None, chat_id: int) -> bool:
        self._cleanup()
        key = f"{chat_id}:{callback_query_id}:{callback_data or ''}"
        if key in self._seen_callbacks:
            return False
        self._seen_callbacks[key] = _SeenRecord(expires_at=time.monotonic() + self.callback_ttl_seconds)
        return True

    def remember_callback_result(self, *, callback_query_id: str, callback_data: str | None, chat_id: int, response_text: str | None = None) -> None:
        key = f"{chat_id}:{callback_query_id}:{callback_data or ''}"
        self._seen_callbacks[key] = _SeenRecord(
            expires_at=time.monotonic() + self.callback_ttl_seconds,
            response_text=response_text,
        )

    def get_callback_result(self, *, callback_query_id: str, callback_data: str | None, chat_id: int) -> str | None:
        self._cleanup()
        key = f"{chat_id}:{callback_query_id}:{callback_data or ''}"
        record = self._seen_callbacks.get(key)
        return None if record is None else record.response_text

    def should_skip_repeated_message(self, *, chat_id: int, message_text: str) -> bool:
        self._cleanup()
        compact = " ".join((message_text or "").strip().lower().split())
        if not compact:
            return False
        key = f"{chat_id}:{compact}"
        if key in self._seen_messages:
            return True
        self._seen_messages[key] = _SeenRecord(expires_at=time.monotonic() + self.message_ttl_seconds)
        return False
