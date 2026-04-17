from app.health_server import build_metrics_payload
from app.runtime import RuntimeState


def test_runtime_state_counters() -> None:
    state = RuntimeState()
    state.mark_bot_started()
    state.record_inbound_message()
    state.record_callback()
    state.record_outbound_message()
    state.record_scheduler_activity(reminder_fired=True, daily_agenda_sent=True)
    snapshot = state.as_dict()

    assert snapshot["bot_started"] is True
    assert snapshot["inbound_message_count"] == 1
    assert snapshot["callback_count"] == 1
    assert snapshot["outbound_message_count"] == 1
    assert snapshot["reminder_fire_count"] == 1
    assert snapshot["daily_agenda_sent_count"] == 1


def test_metrics_payload_includes_scheduler_jobs() -> None:
    state = RuntimeState()
    payload = build_metrics_payload(state, lambda: 5)
    assert payload["metrics"]["scheduler_job_count"] == 5
