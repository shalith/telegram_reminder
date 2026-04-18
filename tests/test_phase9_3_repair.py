from app.phase9_3 import ConversationRepairAndClarifier



def test_meant_rewrite_extracts_time_reply():
    clarifier = ConversationRepairAndClarifier()
    rewrite = clarifier.maybe_rewrite('No, I meant 2 PM', current_task='go to gym', current_time_phrase='today 2 AM')
    assert rewrite is not None
    assert rewrite.handled_as_follow_up is True
    assert '2 PM' in rewrite.message_text



def test_change_only_time_marker():
    clarifier = ConversationRepairAndClarifier()
    rewrite = clarifier.maybe_rewrite('Keep the task, change the time', current_task='go to church', current_time_phrase='tomorrow morning')
    assert rewrite is not None
    assert rewrite.message_text == '__CHANGE_TIME_ONLY__'



def test_build_reference_clarification_for_ambiguous_that_one():
    class Reminder:
        def __init__(self, id, task):
            self.id = id
            self.task = task
            self.next_run_at_utc = None

    clarifier = ConversationRepairAndClarifier()
    req = clarifier.build_reference_clarification(
        text='not that one',
        candidate_reminders=[Reminder(1, 'wake up'), Reminder(2, 'church')],
    )
    assert req is not None
    assert 'Which reminder do you mean?' in req.text
