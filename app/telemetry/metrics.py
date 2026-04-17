from __future__ import annotations

from app.telemetry.otel import get_counter

_ai_parse_success = get_counter("ai_parse_success_total")
_ai_parse_failure = get_counter("ai_parse_failure_total")
_checker_reject = get_counter("checker_reject_total")
_follow_up_issued = get_counter("follow_up_issued_total")
_ambiguous_target = get_counter("ambiguous_target_total")
_duplicate_block = get_counter("duplicate_block_total")
_tool_success = get_counter("tool_execute_success_total")
_tool_failure = get_counter("tool_execute_failure_total")


def inc_parse_success():
    _ai_parse_success.add(1)


def inc_parse_failure():
    _ai_parse_failure.add(1)


def inc_checker_reject(reason: str):
    _checker_reject.add(1, {"reason": reason})


def inc_follow_up():
    _follow_up_issued.add(1)


def inc_ambiguous_target():
    _ambiguous_target.add(1)


def inc_duplicate_block():
    _duplicate_block.add(1)


def inc_tool_success(action: str):
    _tool_success.add(1, {"action": action})


def inc_tool_failure(action: str):
    _tool_failure.add(1, {"action": action})
