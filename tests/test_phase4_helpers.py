from __future__ import annotations

from app.assistant_features import parse_deadline_offsets


def test_parse_deadline_offsets() -> None:
    offsets = parse_deadline_offsets("7 days before and 2 hours before")
    assert len(offsets) == 2
    assert offsets[0].value == 7
    assert offsets[0].unit == "days"
    assert offsets[1].value == 2
    assert offsets[1].unit == "hours"
