from datetime import datetime

from app.phase10_1.calendar_import import CalendarScreenshotImporter, EventBox, DayColumn, TimeRow


def test_assign_day_column_prefers_matching_range():
    importer = CalendarScreenshotImporter(default_timezone='Asia/Singapore')
    columns = [
        DayColumn(label='20 Monday', day_phrase='20 April 2026', x_left=0, x_right=200, x_center=100),
        DayColumn(label='21 Tuesday', day_phrase='21 April 2026', x_left=201, x_right=400, x_center=300),
    ]
    box = EventBox(left=250, top=100, right=350, bottom=140)
    chosen = importer._assign_day_column(box, columns)
    assert chosen is not None
    assert chosen.label == '21 Tuesday'


def test_format_hour_supports_half_hour_offsets():
    importer = CalendarScreenshotImporter(default_timezone='Asia/Singapore')
    assert importer._format_hour(8, 30) == '8:30 AM'
    assert importer._format_hour(15, 0) == '3:00 PM'


def test_normalize_time_token_adds_suffix_for_plain_hour():
    importer = CalendarScreenshotImporter(default_timezone='Asia/Singapore')
    assert importer._normalize_time_token('9') == '9:00 AM'
    assert importer._normalize_time_token('14:00') == '2:00 PM'
