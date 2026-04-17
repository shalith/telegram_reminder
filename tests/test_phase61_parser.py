from app.parser import parse_schedule_components, split_task_and_time_phrase


def test_split_task_first_reminder_phrase():
    task, time_phrase = split_task_and_time_phrase("to Go for Sony headset repair today morning 9am")
    assert task == "Go for Sony headset repair"
    assert time_phrase is not None


def test_parse_today_morning_time_phrase():
    parsed = parse_schedule_components(task="Go for Sony headset repair", time_phrase="today morning 9", timezone_name="Asia/Singapore")
    assert parsed.ok is True
    assert parsed.next_run_at_utc is not None


def test_parse_date_with_morning_phrase():
    parsed = parse_schedule_components(task="Sony headset repair", time_phrase="18th apr morning 8", timezone_name="Asia/Singapore")
    assert parsed.ok is True
    assert parsed.next_run_at_utc is not None
