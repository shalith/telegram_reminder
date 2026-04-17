from __future__ import annotations

from app.telemetry.otel import get_tracer


tracer = get_tracer()


def span_interpret():
    return tracer.start_as_current_span("ai.interpret")


def span_check():
    return tracer.start_as_current_span("ai.check")


def span_execute_tool(action: str):
    return tracer.start_as_current_span(f"tool.execute.{action}")
