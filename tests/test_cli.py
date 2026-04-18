# tests/test_cli.py
import json
from pathlib import Path
from unittest.mock import patch

from sgparl.cli import resolve_dates, save_output


FIXTURES = Path(__file__).parent / "fixtures"
SEEDS = Path(__file__).parent.parent / "seeds" / "dates.csv"


class TestResolveDates:
    def test_explicit_dates_returned_as_is(self):
        result = resolve_dates(dates=["2024-05-07", "2024-02-05"], date_from=None, date_to=None)
        assert result == ["2024-02-05", "2024-05-07"]  # sorted

    def test_date_range_filters_seed_dates(self):
        result = resolve_dates(dates=None, date_from="2024-01-01", date_to="2024-01-31")
        # Should include known sitting dates in January 2024
        assert all("2024-01" in d for d in result)
        # Should not include non-sitting dates
        assert len(result) < 31

    def test_date_range_with_no_sittings_returns_empty(self):
        # Christmas week — unlikely to have sittings
        result = resolve_dates(dates=None, date_from="2024-12-24", date_to="2024-12-31")
        assert result == []


class TestSaveOutput:
    def test_save_csv(self, tmp_path):
        import pandas as pd
        data = {"sittings": pd.DataFrame({"date": ["2024-05-07"], "parliament": [14]})}
        save_output(data, str(tmp_path), "csv")
        assert (tmp_path / "sittings.csv").exists()
        df = pd.read_csv(tmp_path / "sittings.csv")
        assert len(df) == 1

    def test_save_json(self, tmp_path):
        import pandas as pd
        data = {"sittings": pd.DataFrame({"date": ["2024-05-07"], "parliament": [14]})}
        save_output(data, str(tmp_path), "json")
        assert (tmp_path / "sittings.json").exists()
        with open(tmp_path / "sittings.json") as f:
            records = json.load(f)
        assert len(records) == 1

    def test_save_both(self, tmp_path):
        import pandas as pd
        data = {"sittings": pd.DataFrame({"date": ["2024-05-07"], "parliament": [14]})}
        save_output(data, str(tmp_path), "both")
        assert (tmp_path / "sittings.csv").exists()
        assert (tmp_path / "sittings.json").exists()
