from app.ai.time_normalizer import normalize_time_phrase
from app.services.interpretation_service import InterpretationService


def test_reverse_period_normalization():
    assert normalize_time_phrase("10:30 morning") == "10:30 AM"
    assert normalize_time_phrase("today 8 morning") == "today at 8 AM"


def test_wakeup_full_request_detection():
    svc = InterpretationService.__new__(InterpretationService)
    assert svc._looks_like_new_request("Wake me up at 10:30 morning") is True
