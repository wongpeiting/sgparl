"""Integration test — hits the real Parliament API. Run with: pytest tests/test_integration.py -v -m integration"""
import pytest

from sgparl.api import fetch
from sgparl.parse import parse_sittings, parse_attendance, parse_topics, parse_speeches


@pytest.mark.integration
def test_full_pipeline_for_known_date():
    """Fetch and parse a known sitting date (7 May 2024) end-to-end."""
    date = "2024-05-07"
    data = fetch(date)

    sittings = parse_sittings(data["metadata"])
    assert len(sittings) == 1
    assert sittings["date"].iloc[0] == date

    attendance = parse_attendance(date, data["attendanceList"])
    assert len(attendance) > 0
    assert "member_name" in attendance.columns

    topics = parse_topics(date, data["takesSectionVOList"])
    assert len(topics) > 0

    speeches = parse_speeches(date, data["takesSectionVOList"])
    assert len(speeches) > 0
    assert speeches["num_words"].sum() > 0
    # No HTML tags in cleaned text
    for text in speeches["text"].head(10):
        assert "<p>" not in text
        assert "<strong>" not in text
