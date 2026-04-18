# tests/test_api.py
import json
from pathlib import Path
from unittest.mock import patch, Mock

from sgparl.api import fetch, _to_ddmmyyyy, NoSittingError


FIXTURES = Path(__file__).parent / "fixtures"


class TestDateConversion:
    def test_converts_yyyy_mm_dd_to_dd_mm_yyyy(self):
        assert _to_ddmmyyyy("2024-05-07") == "07-05-2024"

    def test_converts_different_date(self):
        assert _to_ddmmyyyy("1955-04-22") == "22-04-1955"


class TestFetch:
    def test_returns_parsed_json(self):
        sample = json.loads((FIXTURES / "sample_response.json").read_text())
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = sample

        with patch("sgparl.api.requests.get", return_value=mock_resp) as mock_get:
            result = fetch("2024-05-07")

        mock_get.assert_called_once_with(
            "https://sprs.parl.gov.sg/search/getHansardReport/?sittingDate=07-05-2024",
            timeout=30,
        )
        assert "metadata" in result
        assert "takesSectionVOList" in result

    def test_raises_on_non_200(self):
        mock_resp = Mock()
        mock_resp.status_code = 500
        mock_resp.raise_for_status.side_effect = Exception("Server Error")

        with patch("sgparl.api.requests.get", return_value=mock_resp):
            try:
                fetch("2024-05-07")
                assert False, "Should have raised"
            except Exception:
                pass

    def test_raises_no_sitting_on_empty_response(self):
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {}

        with patch("sgparl.api.requests.get", return_value=mock_resp):
            try:
                fetch("2024-01-01")
                assert False, "Should have raised NoSittingError"
            except NoSittingError:
                pass
