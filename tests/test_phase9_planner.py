from app.phase9 import MultiReminderPlanner, ProactiveSuggester


class DummyReminder:
    def __init__(self, reminder_id: int, task: str, next_run_at_utc=None):
        self.id = reminder_id
        self.task = task
        self.next_run_at_utc = next_run_at_utc


def test_multi_planner_detects_two_or_more_items():
    planner = MultiReminderPlanner()
    proposal = planner.detect(
        'Tomorrow remind me about gym at 7, dentist at 11, and call mom in the evening',
        timezone_name='Asia/Singapore',
    )
    assert proposal is not None
    assert len(proposal.items) == 3
    assert proposal.items[0].task.lower() == 'gym'


def test_multi_planner_rejects_single_item():
    planner = MultiReminderPlanner()
    proposal = planner.detect('Remind me to pay rent tomorrow at 7 PM', timezone_name='Asia/Singapore')
    assert proposal is None


def test_agenda_suggestion_for_busy_day():
    suggester = ProactiveSuggester()
    reminders = [DummyReminder(1, 'gym'), DummyReminder(2, 'dentist'), DummyReminder(3, 'call mom')]
    suggestions = suggester.suggestions_for_agenda(reminders)
    assert suggestions
