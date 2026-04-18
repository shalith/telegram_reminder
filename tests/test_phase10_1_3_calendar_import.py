from datetime import datetime, timedelta

from app.phase10_1.calendar_import import CalendarScreenshotImporter, ImportedMeeting


def _meeting(title: str, start: datetime) -> ImportedMeeting:
    return ImportedMeeting(
        title=title,
        meeting_start=start,
        reminder_at=start - timedelta(minutes=10),
        reminder_time_phrase=(start - timedelta(minutes=10)).strftime('%d %b %Y %I:%M %p'),
        source_line='test',
    )


def test_keep_best_per_slot_prefers_better_title():
    importer = CalendarScreenshotImporter(default_timezone='Asia/Singapore')
    start = datetime(2026, 4, 20, 9, 30)
    meetings = [
        _meeting('Meeting (20 Monday)', start),
        _meeting('GPP Team meeting - Daily Standup', start),
    ]
    kept = importer._keep_best_per_slot(meetings)
    assert len(kept) == 1
    assert kept[0].title == 'GPP Team meeting - Daily Standup'


def test_meeting_quality_penalizes_overnight_noise():
    importer = CalendarScreenshotImporter(default_timezone='Asia/Singapore')
    noisy = _meeting('x y z', datetime(2026, 4, 18, 2, 0))
    strong = _meeting('GPP Team meeting - Daily Standup', datetime(2026, 4, 20, 9, 30))
    assert importer._meeting_quality(strong) > importer._meeting_quality(noisy)
