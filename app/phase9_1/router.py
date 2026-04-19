from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from app.ai.interpreter import Groq
from app.config import Settings

RouteKind = Literal['general_chat', 'reminder_conversation', 'confirmation_reply', 'repair_conversation']


class RoutePayload(BaseModel):
    route: RouteKind = 'general_chat'
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    should_use_existing_thread: bool = False
    reason: str | None = None


@dataclass(slots=True)
class ConversationRouteDecision:
    route: RouteKind
    confidence: float
    should_use_existing_thread: bool = False
    reason: str | None = None


class LLMConversationRouter:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = Groq(api_key=settings.groq_api_key) if settings.groq_enabled and Groq is not None else None

    def route(
        self,
        *,
        message_text: str,
        has_active_thread: bool,
        has_pending_confirmation: bool,
        has_pending_follow_up: bool,
    ) -> ConversationRouteDecision:
        fallback = self._heuristic_route(
            message_text=message_text,
            has_active_thread=has_active_thread,
            has_pending_confirmation=has_pending_confirmation,
            has_pending_follow_up=has_pending_follow_up,
        )
        if self.client is None:
            return fallback
        try:
            response = self.client.chat.completions.create(
                model=self.settings.groq_model,
                temperature=0,
                messages=[
                    {
                        'role': 'system',
                        'content': (
                            'You route Telegram assistant messages. '
                            'Classify whether a message is general chat, reminder conversation, confirmation reply, or repair conversation. '
                            'Use reminder_conversation for scheduling, reminders, wake-ups, time changes, and task planning. '
                            'Use confirmation_reply for short replies like yes/no/confirm/cancel when a confirmation is pending. '
                            'Use repair_conversation for corrections like "I meant 2 PM", "not tomorrow", "that is wrong". '
                            'Respond with JSON only.'
                        ),
                    },
                    {
                        'role': 'developer',
                        'content': json.dumps(
                            {
                                'message_text': message_text,
                                'has_active_thread': has_active_thread,
                                'has_pending_confirmation': has_pending_confirmation,
                                'has_pending_follow_up': has_pending_follow_up,
                            }
                        ),
                    },
                ],
                response_format={'type': 'json_object'},
            )
            raw = response.choices[0].message.content or '{}'
            payload = json.loads(raw)
            parsed = RoutePayload.model_validate(payload)
            llm_decision = ConversationRouteDecision(
                route=parsed.route,
                confidence=parsed.confidence,
                should_use_existing_thread=parsed.should_use_existing_thread,
                reason=parsed.reason,
            )
            # Prefer the deterministic reminder path when the message clearly looks operational.
            if fallback.route != 'general_chat' and llm_decision.route == 'general_chat':
                return fallback
            if has_pending_confirmation and fallback.route == 'confirmation_reply' and llm_decision.route != 'confirmation_reply':
                return fallback
            return llm_decision
        except Exception:
            return fallback

    def _heuristic_route(
        self,
        *,
        message_text: str,
        has_active_thread: bool,
        has_pending_confirmation: bool,
        has_pending_follow_up: bool,
    ) -> ConversationRouteDecision:
        text = ' '.join((message_text or '').strip().split())
        lowered = text.lower()

        if has_pending_confirmation and lowered in {'yes', 'y', 'confirm', 'ok', 'okay', 'no', 'n', 'cancel', 'edit'}:
            return ConversationRouteDecision('confirmation_reply', 0.96, should_use_existing_thread=True, reason='pending_confirmation_reply')

        repair_markers = (
            'i meant', 'not ', 'wrong', 'typed it wrongly', 'that is wrong', 'change only', 'make it ', 'instead', 'no,', 'no ',
        )
        if any(marker in lowered for marker in repair_markers):
            return ConversationRouteDecision('repair_conversation', 0.86, should_use_existing_thread=has_active_thread or has_pending_follow_up or has_pending_confirmation, reason='repair_marker')

        if self._looks_like_reminder_message(lowered):
            return ConversationRouteDecision('reminder_conversation', 0.9, should_use_existing_thread=has_active_thread or has_pending_follow_up, reason='reminder_pattern')

        if has_active_thread or has_pending_follow_up:
            if self._looks_like_time_or_task_reply(text):
                return ConversationRouteDecision('reminder_conversation', 0.8, should_use_existing_thread=True, reason='thread_continuation')

        return ConversationRouteDecision('general_chat', 0.72, should_use_existing_thread=False, reason='general_fallback')

    def _looks_like_reminder_message(self, lowered: str) -> bool:
        if not lowered:
            return False
        reminder_prefixes = (
            'remind me', 'wake me up', 'wake up', 'i need to', 'i need ', 'i want to', 'need to ', 'tomorrow remind me',
            'today remind me', 'move ', 'change ', 'reschedule ', 'cancel ', 'delete ', 'remove ', 'set my ',
            'tomorrow i have', 'today i have', 'help me plan',
        )
        if lowered.startswith(reminder_prefixes):
            return True
        time_words = ('today', 'tomorrow', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday', 'morning', 'afternoon', 'evening', 'night', 'am', 'pm')
        return any(word in lowered for word in time_words) and any(kw in lowered for kw in ('remind', 'wake', 'need', 'want', 'have to', 'schedule', 'plan'))

    def _looks_like_time_or_task_reply(self, text: str) -> bool:
        lowered = text.lower()
        if re.search(r'\b\d{1,2}(:\d{2})?\s*(am|pm)?\b', lowered):
            return True
        return any(token in lowered for token in ('today', 'tomorrow', 'morning', 'afternoon', 'evening', 'night')) or len(text.split()) <= 6
