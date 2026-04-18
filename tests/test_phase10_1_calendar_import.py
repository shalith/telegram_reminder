
from datetime import datetime

from app.phase10_1.calendar_import import CalendarScreenshotImporter


def test_extract_meetings_from_ocr_text_like_lines():
    importer = CalendarScreenshotImporter(default_timezone='Asia/Singapore', lead_minutes=10, fallback_to_today=True)
    raw_text = """
    Tomorrow
    9:00 AM - 9:30 AM Team Sync
    11:00 AM Client Review
    2:00 PM Engineering Catchup
    """
    meetings = importer._extract_meetings(raw_text=raw_text, caption_text='tomorrow')
    assert len(meetings) >= 2
    assert meetings[0].title.lower().startswith('team sync')
    assert meetings[0].reminder_time_phrase
