from types import SimpleNamespace

from app.ai.resolver import TargetResolver


def make_reminder(rid, task, requires_ack=False):
    return SimpleNamespace(id=rid, task=task, requires_ack=requires_ack, recurrence_type="once", recurrence_day_of_week=None)


def test_resolver_finds_single_id_match():
    resolver = TargetResolver()
    reminders = [make_reminder(1, "pay rent")]
    result = resolver.resolve(selector_text=None, reminder_id=1, reminders=reminders)
    assert result.status == "single"
