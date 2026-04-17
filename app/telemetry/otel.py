from __future__ import annotations

try:
    from opentelemetry import metrics, trace  # type: ignore
except Exception:  # pragma: no cover
    metrics = None
    trace = None


class _NoopCounter:
    def add(self, amount: int = 1, attributes=None):
        return None


class _NoopTracer:
    class _Span:
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
        def set_attribute(self, key, value):
            return None
    def start_as_current_span(self, name: str):
        return self._Span()


_tracer = _NoopTracer()
_meter_counter_cache = {}


def init_otel(app_name: str, endpoint: str | None = None) -> None:
    return None


def get_tracer():
    if trace is None:
        return _tracer
    return trace.get_tracer("telegram_reminder_mvp_phase6")


def get_counter(name: str):
    if name in _meter_counter_cache:
        return _meter_counter_cache[name]
    if metrics is None:
        counter = _NoopCounter()
    else:
        meter = metrics.get_meter("telegram_reminder_mvp_phase6")
        counter = meter.create_counter(name)
    _meter_counter_cache[name] = counter
    return counter
