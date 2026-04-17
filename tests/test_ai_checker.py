from app.ai.checker import InterpretationChecker
from app.ai.schemas import InterpretationEnvelope, ReminderDraft


def test_checker_blocks_create_without_datetime():
    checker = InterpretationChecker()
    env = InterpretationEnvelope(action="create_reminder", reminder=ReminderDraft(task="pay rent"))
    result = checker.check(envelope=env, open_reminders=[])
    assert not result.ok
    assert result.follow_up_text
