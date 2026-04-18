from datetime import datetime
from zoneinfo import ZoneInfo

from app.models import Reminder, ReminderStatus
from app.services.duplicate_detection_service import DuplicateDetectionService


def _reminder(task: str, dt: datetime) -> Reminder:
    r = Reminder(
        telegram_user_id=1,
        chat_id=1,
        task=task,
        original_text=task,
        next_run_at_utc=dt.astimezone(ZoneInfo('UTC')).replace(tzinfo=ZoneInfo('UTC')),
        timezone='Asia/Singapore',
        status=ReminderStatus.ACTIVE.value,
        job_id=f'job-{task}',
    )
    return r


def test_duplicate_requires_close_time():
    svc = DuplicateDetectionService()
    existing = _reminder('wake up', datetime(2026, 4, 19, 8, 30, tzinfo=ZoneInfo('Asia/Singapore')))
    duplicates = svc.find_possible_duplicates(
        reminders=[existing],
        task='wake up',
        due_repr='today 4 pm',
        recurrence=None,
        timezone_name='Asia/Singapore',
    )
    assert duplicates == []
