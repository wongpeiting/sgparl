# tests/test_parse.py
import json
from pathlib import Path

from sgparl.parse import parse_sittings, parse_attendance, parse_topics, parse_speeches


FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture():
    return json.loads((FIXTURES / "sample_response.json").read_text())


class TestParseSittings:
    def test_returns_dataframe_with_expected_columns(self):
        data = _load_fixture()
        df = parse_sittings(data["metadata"])
        assert list(df.columns) == [
            "date", "datetime", "parliament", "session", "volume", "sittings"
        ]
        assert len(df) == 1

    def test_date_format_is_yyyy_mm_dd(self):
        data = _load_fixture()
        df = parse_sittings(data["metadata"])
        date_val = df["date"].iloc[0]
        assert len(date_val) == 10
        assert date_val[4] == "-"


class TestParseAttendance:
    def test_returns_dataframe_with_expected_columns(self):
        data = _load_fixture()
        df = parse_attendance("2024-05-07", data["attendanceList"])
        assert list(df.columns) == ["date", "member_name", "is_present"]
        assert len(df) > 0

    def test_all_rows_have_same_date(self):
        data = _load_fixture()
        df = parse_attendance("2024-05-07", data["attendanceList"])
        assert (df["date"] == "2024-05-07").all()


class TestParseTopics:
    def test_returns_dataframe_with_expected_columns(self):
        data = _load_fixture()
        df = parse_topics("2024-05-07", data["takesSectionVOList"])
        assert list(df.columns) == [
            "topic_id", "date", "topic_order", "title", "section_type"
        ]
        assert len(df) > 0

    def test_topic_id_format(self):
        data = _load_fixture()
        df = parse_topics("2024-05-07", data["takesSectionVOList"])
        first_id = df["topic_id"].iloc[0]
        assert first_id.startswith("2024-05-07-T-")


class TestParseSpeeches:
    def test_returns_dataframe_with_expected_columns(self):
        data = _load_fixture()
        df = parse_speeches("2024-05-07", data["takesSectionVOList"])
        expected_cols = [
            "date", "speech_id", "topic_id", "speech_order",
            "member_name_original", "member_name", "text",
            "num_words", "num_characters", "num_sentences", "num_syllables",
        ]
        assert list(df.columns) == expected_cols
        assert len(df) > 0

    def test_speech_id_format(self):
        data = _load_fixture()
        df = parse_speeches("2024-05-07", data["takesSectionVOList"])
        first_id = df["speech_id"].iloc[0]
        assert "-T-" in first_id
        assert "-S-" in first_id

    def test_text_has_no_html_tags(self):
        data = _load_fixture()
        df = parse_speeches("2024-05-07", data["takesSectionVOList"])
        for text in df["text"].head(20):
            assert "<p>" not in text
            assert "<strong>" not in text
