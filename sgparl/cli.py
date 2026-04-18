# sgparl/cli.py
import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd

from sgparl.api import fetch, NoSittingError
from sgparl.parse import parse_sittings, parse_attendance, parse_topics, parse_speeches


def _seeds_path():
    """Path to seeds/dates.csv, relative to this repo."""
    return Path(__file__).parent.parent / "seeds" / "dates.csv"


def resolve_dates(dates=None, date_from=None, date_to=None):
    """Resolve which dates to scrape.

    If explicit dates given, return them sorted.
    If date_from/date_to given, filter seeds/dates.csv for sitting dates in range.
    """
    if dates:
        return sorted(dates)

    seeds_file = _seeds_path()
    if not seeds_file.exists():
        print(f"Warning: {seeds_file} not found. Cannot resolve date range.")
        return []

    seed_df = pd.read_csv(seeds_file)
    all_dates = sorted(seed_df["Sitting_Date"].tolist())

    filtered = [d for d in all_dates if date_from <= d <= date_to]
    return filtered


def save_output(dataframes, output_dir, fmt):
    """Save DataFrames to output directory as CSV and/or JSON."""
    os.makedirs(output_dir, exist_ok=True)

    for name, df in dataframes.items():
        if fmt in ("csv", "both"):
            path = os.path.join(output_dir, f"{name}.csv")
            df.to_csv(path, index=False)
            print(f"  Saved {path} ({len(df)} rows)")

        if fmt in ("json", "both"):
            path = os.path.join(output_dir, f"{name}.json")
            with open(path, "w") as f:
                json.dump(df.to_dict(orient="records"), f, indent=2)
            print(f"  Saved {path} ({len(df)} rows)")


def main():
    parser = argparse.ArgumentParser(
        prog="sgparl",
        description="Scrape Singapore Parliament Hansard speeches to local files.",
    )
    parser.add_argument(
        "--date", nargs="+", help="One or more sitting dates (YYYY-MM-DD)"
    )
    parser.add_argument("--from", dest="date_from", help="Start date for range (YYYY-MM-DD)")
    parser.add_argument("--to", dest="date_to", help="End date for range (YYYY-MM-DD)")
    parser.add_argument(
        "--output", default="data", help="Output directory (default: data)"
    )
    parser.add_argument(
        "--format",
        dest="fmt",
        choices=["csv", "json", "both"],
        default="csv",
        help="Output format (default: csv)",
    )
    args = parser.parse_args()

    if not args.date and not (args.date_from and args.date_to):
        parser.error("Provide --date or both --from and --to")

    if (args.date_from and not args.date_to) or (args.date_to and not args.date_from):
        parser.error("Both --from and --to are required for date ranges")

    dates = resolve_dates(
        dates=args.date, date_from=args.date_from, date_to=args.date_to
    )

    if not dates:
        print("No sitting dates found for the given range.")
        sys.exit(0)

    print(f"Scraping {len(dates)} date(s): {', '.join(dates)}")

    all_sittings = []
    all_attendance = []
    all_topics = []
    all_speeches = []

    for date in dates:
        try:
            data = fetch(date)
            print(f"  [{date}] Parsing...")

            all_sittings.append(parse_sittings(data["metadata"]))
            all_attendance.append(parse_attendance(date, data["attendanceList"]))
            all_topics.append(parse_topics(date, data["takesSectionVOList"]))
            all_speeches.append(parse_speeches(date, data["takesSectionVOList"]))

            print(f"  [{date}] Done")

        except NoSittingError:
            print(f"  [{date}] No sitting found, skipping")
        except Exception as e:
            print(f"  [{date}] Error: {e}")

    if not all_sittings:
        print("No data scraped.")
        sys.exit(0)

    output = {
        "sittings": pd.concat(all_sittings, ignore_index=True),
        "attendance": pd.concat(all_attendance, ignore_index=True),
        "topics": pd.concat(all_topics, ignore_index=True),
        "speeches": pd.concat(all_speeches, ignore_index=True),
    }

    print(f"\nSaving to {args.output}/")
    save_output(output, args.output, args.fmt)
    print("Done!")
