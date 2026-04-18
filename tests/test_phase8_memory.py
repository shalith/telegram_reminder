from app.ai.schemas import FollowUp, InterpretationEnvelope, PreferencePatch, ReminderDraft, TargetSelector
from app.phase8 import MemoryProfileStore, MemoryReasoner
from app.models import TaskMemoryProfile


def test_memory_profile_store_remembers_values(session):
    store = MemoryProfileStore()
    row = store.remember_from_values(
        session,
        telegram_user_id=123,
        task="Go for gym",
        hour_local=19,
        minute_local=0,
        recurrence_type="once",
        confirmed=True,
    )
    assert row is not None
    assert row.task_key == "go for gym"
    assert row.preferred_time_of_day == "evening"
    assert row.confirmed_count == 1


def test_memory_reasoner_suggests_time_for_known_task(session):
    store = MemoryProfileStore()
    store.remember_from_values(
        session,
        telegram_user_id=123,
        task="Sony headset repair",
        hour_local=14,
        minute_local=0,
        recurrence_type="once",
        confirmed=True,
    )
    matches = store.find_matches(session, telegram_user_id=123, message_text="Remind me about Sony headset repair")
    envelope = InterpretationEnvelope(
        action="create_reminder",
        confidence=0.55,
        reminder=ReminderDraft(task="Sony headset repair", datetime_text=None, recurrence_text=None, timezone=None, is_wake_up=False, requires_ack=False, priority="normal"),
        target=TargetSelector(),
        preferences=PreferencePatch(),
        follow_up=FollowUp(needed=False, question=None, missing_fields=[]),
        user_message_summary="create",
        reasoning_tags=[],
        deadline_offsets=[],
    )
    result = MemoryReasoner().apply(envelope=envelope, message_text="Remind me about Sony headset repair", matched_profiles=matches)
    assert result.follow_up_text is not None
    assert "usually set it around 2:00 PM" in result.follow_up_text


def test_memory_reasoner_boosts_confidence_for_matching_period(session):
    store = MemoryProfileStore()
    store.remember_from_values(
        session,
        telegram_user_id=123,
        task="Go for gym",
        hour_local=19,
        minute_local=0,
        recurrence_type="once",
        confirmed=True,
    )
    matches = store.find_matches(session, telegram_user_id=123, message_text="Remind me to go for gym today evening 7")
    envelope = InterpretationEnvelope(
        action="create_reminder",
        confidence=0.50,
        reminder=ReminderDraft(task="Go for gym", datetime_text="today evening 7", recurrence_text=None, timezone=None, is_wake_up=False, requires_ack=False, priority="normal"),
        target=TargetSelector(),
        preferences=PreferencePatch(),
        follow_up=FollowUp(needed=False, question=None, missing_fields=[]),
        user_message_summary="create",
        reasoning_tags=[],
        deadline_offsets=[],
    )
    result = MemoryReasoner().apply(envelope=envelope, message_text="Remind me to go for gym today evening 7", matched_profiles=matches)
    assert result.adjusted_confidence > 0.50
    assert "memory_period_match" in result.reasons
