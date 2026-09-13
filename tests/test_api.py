# tests/test_api.py
from unittest.mock import patch

from sgparl.api import (
    fetch,
    check_sitting,
    _to_ddmmyyyy,
    _solr_daterange,
    _section_type,
    NoSittingError,
)


class TestDateConversion:
    def test_converts_yyyy_mm_dd_to_dd_mm_yyyy(self):
        assert _to_ddmmyyyy("2024-05-07") == "07-05-2024"

    def test_converts_different_date(self):
        assert _to_ddmmyyyy("1955-04-22") == "22-04-1955"

    def test_solr_daterange_wraps_a_single_day(self):
        assert _solr_daterange("2020-01-06") == (
            "2020-01-06T00:00:00Z TO 2020-01-06T23:59:59Z"
        )


class TestSectionType:
    def test_maps_known_prefixes(self):
        assert _section_type("oral-answer-2101#") == "OA"
        assert _section_type("written-answer-5477#") == "WA"
        assert _section_type("written-answer-na-5494#") == "WANA"
        assert _section_type("bill-753#") == "BI"
        assert _section_type("bill-intro-367#") == "BI"

    def test_unknown_prefix_falls_back_to_os(self):
        assert _section_type("motion-1257#") == "OS"
        assert _section_type("matter-adj-2656#") == "OS"


def _fake_post(endpoint, payload):
    """Stand-in for api._post: one report, one speech."""
    if endpoint == "searchResult":
        return [{
            "reportId": "bill-1#",
            "maxResult": "1",
            "title": "Test Bill",
            "sittingDate": "7-5-2024",
        }]
    if endpoint == "getHansardTopic":
        return {"resultHTML": {
            "content": "<p><strong>Mr Test Speaker (Ang Mo Kio)</strong>: Hello world.</p>",
            "title": "Test Bill",
            "parlNo": "14", "sessionNo": "1", "volumeNo": "95", "sittingNo": "1",
            "sittingDate": "7-5-2024",
        }}
    return None


class TestFetch:
    def test_returns_old_shape_from_new_endpoints(self):
        with patch("sgparl.api._post", side_effect=_fake_post):
            result = fetch("2024-05-07")

        assert set(result) == {"metadata", "attendanceList", "takesSectionVOList"}
        assert result["metadata"]["parlimentNO"] == "14"
        assert result["metadata"]["sittingDate"] == "07-05-2024"
        # Attendance is not exposed by the sprs3 API -> empty, best-effort.
        assert result["attendanceList"] == []
        assert len(result["takesSectionVOList"]) == 1
        topic = result["takesSectionVOList"][0]
        assert topic["sectionType"] == "BI"
        assert "<strong>" in topic["content"]

    def test_raises_no_sitting_when_no_reports(self):
        with patch("sgparl.api._post", return_value={}):
            try:
                fetch("2024-01-01")
                assert False, "Should have raised NoSittingError"
            except NoSittingError:
                pass


class TestCheckSitting:
    def test_true_when_reports_exist(self):
        with patch("sgparl.api._post", side_effect=_fake_post):
            assert check_sitting("2024-05-07") is True

    def test_false_on_error(self):
        with patch("sgparl.api._post", side_effect=RuntimeError("boom")):
            assert check_sitting("2024-05-07") is False
