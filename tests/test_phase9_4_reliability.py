from app.phase9_4 import ExecutionGuard
from app.services.duplicate_detection_service import DuplicateDetectionService


class DummyReminder:
    def __init__(self, *, rid: int, task: str, semantic_key: str | None = None, next_run_at_utc=None):
        self.id = rid
        self.task = task
        self.semantic_key = semantic_key
        self.next_run_at_utc = next_run_at_utc


def test_execution_guard_blocks_same_callback_once():
    guard = ExecutionGuard(callback_ttl_seconds=60)
    assert guard.mark_callback_started(callback_query_id="abc", callback_data="confirm:yes", chat_id=1) is True
    guard.remember_callback_result(callback_query_id="abc", callback_data="confirm:yes", chat_id=1, response_text="done")
    assert guard.mark_callback_started(callback_query_id="abc", callback_data="confirm:yes", chat_id=1) is False
    assert guard.get_callback_result(callback_query_id="abc", callback_data="confirm:yes", chat_id=1) == "done"


def test_execution_guard_blocks_immediate_repeat_message():
    guard = ExecutionGuard(message_ttl_seconds=60)
    assert guard.should_skip_repeated_message(chat_id=5, message_text="Wake me up tomorrow at 8") is False
    assert guard.should_skip_repeated_message(chat_id=5, message_text="Wake me up tomorrow at 8") is True


def test_duplicate_detection_finds_same_task_and_time_key():
    svc = DuplicateDetectionService()
    reminders = [DummyReminder(rid=2, task="wake up", semantic_key="wake up|today at 4 pm|none")]
    matches = svc.find_possible_duplicates(
        reminders=reminders,
        task="wake up",
        due_repr="today at 4 pm",
        recurrence=None,
        timezone_name="Asia/Singapore",
    )
    assert matches and matches[0].id == 2
