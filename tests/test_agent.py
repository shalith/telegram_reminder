from __future__ import annotations

from app.agent import RuleBasedInterpreter
from app.agent_schema import PendingState


def test_rule_based_today_summary_intent() -> None:
    interpreter = RuleBasedInterpreter()
    decision = interpreter.interpret(message_text="What do I have today", pending_state=None)
    assert decision.intent == "today_summary"


def test_rule_based_daily_agenda_preference() -> None:
    interpreter = RuleBasedInterpreter()
    decision = interpreter.interpret(message_text="Send my daily agenda every day at 8 AM", pending_state=None)
    assert decision.intent == "set_preference"
    assert decision.preference_name == "daily_agenda_time"
    assert decision.preference_value == "8 AM"


def test_rule_based_deadline_chain_intent() -> None:
    interpreter = RuleBasedInterpreter()
    decision = interpreter.interpret(
        message_text="My report deadline is April 30 at 5 PM, remind me 7 days before and 2 hours before",
        pending_state=None,
    )
    assert decision.intent == "deadline_chain"
    assert decision.task == "report"
    assert len(decision.deadline_offsets) == 2


def test_pending_create_follow_up_fills_time() -> None:
    interpreter = RuleBasedInterpreter()
    pending = PendingState(intent="create", task="pay rent", time_phrase=None, ask_user="When should I remind you?")
    decision = interpreter.interpret(message_text="tomorrow at 7 PM", pending_state=pending)
    assert decision.intent == "create"
    assert decision.task == "pay rent"
    assert decision.time_phrase == "tomorrow at 7 PM"
    assert decision.missing_fields == []
