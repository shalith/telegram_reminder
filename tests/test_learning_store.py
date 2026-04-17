from app.ai.time_normalizer import normalize_time_phrase


def test_normalize_time_phrase_examples():
    assert normalize_time_phrase("today morning 9") == "today at 9 AM"
    assert normalize_time_phrase("18th apr morning 8") == "18th apr at 8 AM"
