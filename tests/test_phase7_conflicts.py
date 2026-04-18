from datetime import datetime
from zoneinfo import ZoneInfo

from app.ai.checker import CheckerResult
from app.ai.schemas import FollowUp, InterpretationEnvelope, ReminderDraft, TargetSelector, PreferencePatch
from app.models import Reminder, ReminderStatus
from app.phase7 import EvaluatorAgent, SemanticConflictDetector


def build_reminder(reminder_id: int, task: str, iso_local: str) -> Reminder:
    dt = datetime.fromisoformat(iso_local).replace(tzinfo=ZoneInfo("Asia/Singapore")).astimezone(ZoneInfo("UTC"))
    return Reminder(
        id=reminder_id,
        telegram_user_id=1,
        chat_id=1,
        task=task,
        original_text=task,
        next_run_at_utc=dt,
        timezone="Asia/Singapore",
        status=ReminderStatus.ACTIVE.value,
        job_id=f"job-{reminder_id}",
    )


def test_detects_overlap_conflict():
    detector = SemanticConflictDetector()
    envelope = InterpretationEnvelope(
        action="create_reminder",
        confidence=0.9,
        reminder=ReminderDraft(task="gym", datetime_text="tomorrow at 2 PM"),
        target=TargetSelector(),
        preferences=PreferencePatch(),
        follow_up=FollowUp(),
        reasoning_tags=[],
        deadline_offsets=[],
    )
    open_reminders = [build_reminder(2, "Sony repair", "2026-04-19T14:00:00")]
    conflicts = detector.detect(envelope=envelope, open_reminders=open_reminders, timezone_name="Asia/Singapore")
    assert any(item.code == "time_overlap" for item in conflicts)


def test_evaluator_forces_confirmation_on_conflict():
    evaluator = EvaluatorAgent()
    envelope = InterpretationEnvelope(
        action="create_reminder",
        confidence=0.88,
        reminder=ReminderDraft(task="gym", datetime_text="tomorrow at 2 PM"),
        target=TargetSelector(),
        preferences=PreferencePatch(),
        follow_up=FollowUp(),
        reasoning_tags=[],
        deadline_offsets=[],
    )
    checker = CheckerResult(ok=True, envelope=envelope, confidence=0.88)
    open_reminders = [build_reminder(2, "Sony repair", "2026-04-19T14:00:00")]
    decision = evaluator.evaluate(envelope=envelope, checker_result=checker, open_reminders=open_reminders, timezone_name="Asia/Singapore")
    assert decision.force_confirmation is True
    assert decision.confirmation_text
