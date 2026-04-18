from app.semantic_judgment import (
    apply_semantic_judgment,
    detect_repair_signal,
    infer_indirect_reminder,
    should_confirm_for_semantics,
)


def test_infer_indirect_reminder_statement():
    envelope = infer_indirect_reminder("I need to go for Sony headset repair around 2pm")
    assert envelope is not None
    assert envelope.action == "create_reminder"
    assert envelope.reminder.task == "go for Sony headset repair"
    assert envelope.reminder.datetime_text == "around 2pm"
    assert "indirect_intent" in envelope.reasoning_tags


def test_semantic_judgment_marks_suspicious_gym_time():
    envelope = infer_indirect_reminder("I want to go for gym today 2am")
    assert envelope is not None
    judged = apply_semantic_judgment("I want to go for gym today 2am", envelope, "Asia/Singapore")
    assert "suspicious_time" in judged.reasoning_tags
    assert should_confirm_for_semantics(judged)


def test_detect_repair_signal_for_meant_not():
    signal = detect_repair_signal("I meant 2 PM, not 2 AM")
    assert signal is not None
    assert signal.corrected_time_phrase == "2 PM"
