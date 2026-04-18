# sgparl/cli.py
import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd

from datetime import datetime, timedelta

from sgparl.api import fetch, check_sitting, NoSittingError
from sgparl.parse import parse_sittings, parse_attendance, parse_topics, parse_speeches, extract_adjournment_time


def _seeds_path():
    """Path to seeds/dates.csv, relative to this repo."""
    return Path(__file__).parent.parent / "seeds" / "dates.csv"


def _members_path():
    """Path to seeds/member.csv, relative to this repo."""
    return Path(__file__).parent.parent / "seeds" / "member.csv"


def _load_members(parliament=None):
    """Load member.csv as a lookup dict: name -> {party, gender}.

    If parliament number is given, filter to that parliament term.
    This handles MPs who changed party between terms (e.g. NMP -> PAP).
    Falls back to the most recent entry if no parliament match found.
    """
    members_file = _members_path()
    if not members_file.exists():
        return {}
    df = pd.read_csv(members_file)

    if parliament and "parliament" in df.columns:
        # Filter to matching parliament, fall back to latest entry
        result = {}
        for name in df["mp_name"].unique():
            mp_rows = df[df["mp_name"] == name]
            match = mp_rows[mp_rows["parliament"] == parliament]
            if len(match):
                row = match.iloc[0]
            else:
                row = mp_rows.sort_values("parliament").iloc[-1]
            result[name] = {"party": row["party"], "gender": row["gender"]}
        return result
    else:
        # No parliament filter — use latest entry per MP
        result = {}
        for _, row in df.iterrows():
            result[row["mp_name"]] = {"party": row["party"], "gender": row["gender"]}
        return result


def _enrich_with_members(dataframes):
    """Add party and gender columns to attendance and speeches DataFrames."""
    # Get parliament number from sittings data
    parliament = None
    if "sittings" in dataframes and len(dataframes["sittings"]):
        parliament = int(dataframes["sittings"]["parliament"].iloc[0])

    members = _load_members(parliament=parliament)
    if not members:
        return dataframes

    for key in ("attendance", "speeches"):
        if key in dataframes:
            df = dataframes[key]
            name_col = "member_name"
            df["party"] = df[name_col].map(lambda n: members.get(n, {}).get("party", ""))
            df["gender"] = df[name_col].map(lambda n: members.get(n, {}).get("gender", ""))
            dataframes[key] = df

    return dataframes


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


def update_seeds():
    """Scan for new sitting dates from the last known date to today.

    Checks each weekday (Mon-Fri) against the Parliament API and appends
    any new sitting dates to seeds/dates.csv.
    """
    seeds_file = _seeds_path()
    if not seeds_file.exists():
        print(f"Error: {seeds_file} not found.")
        sys.exit(1)

    seed_df = pd.read_csv(seeds_file)
    known_dates = set(seed_df["Sitting_Date"].tolist())
    last_date = max(known_dates)

    start = datetime.strptime(last_date, "%Y-%m-%d") + timedelta(days=1)
    end = datetime.today()
    today_str = end.strftime("%Y-%m-%d")

    print(f"Scanning for new sittings from {start.strftime('%Y-%m-%d')} to {today_str}...")

    # Collect weekdays to check
    weekdays = []
    current = start
    while current <= end:
        if current.weekday() < 5:  # Mon-Fri
            weekdays.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)

    print(f"Checking {len(weekdays)} weekdays...")

    new_dates = []
    for i, date in enumerate(weekdays):
        if date in known_dates:
            continue
        has_sitting = check_sitting(date)
        if has_sitting:
            new_dates.append(date)
            print(f"  Found sitting: {date}")
        if (i + 1) % 50 == 0:
            print(f"  Checked {i + 1}/{len(weekdays)} dates, found {len(new_dates)} so far...")

    if not new_dates:
        print("No new sitting dates found.")
        return

    # Append to seeds/dates.csv
    new_rows = pd.DataFrame({
        "Sitting_Date": new_dates,
        "Version": [2] * len(new_dates),
        "Date_Added": [today_str] * len(new_dates),
    })
    updated_df = pd.concat([seed_df, new_rows], ignore_index=True)
    updated_df = updated_df.sort_values("Sitting_Date").drop_duplicates(subset="Sitting_Date")
    updated_df.to_csv(seeds_file, index=False)

    print(f"\nAdded {len(new_dates)} new sitting date(s) to {seeds_file}")
    for d in new_dates:
        print(f"  {d}")


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
        "--update-seeds", action="store_true",
        help="Scan for new sitting dates and update seeds/dates.csv",
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

    if args.update_seeds:
        update_seeds()
        return

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

            sitting_df = parse_sittings(data["metadata"])
            end_time = extract_adjournment_time(date, data["takesSectionVOList"])
            sitting_df["end_time"] = end_time or ""
            if end_time and sitting_df["datetime"].iloc[0]:
                try:
                    start = datetime.strptime(sitting_df["datetime"].iloc[0], "%Y-%m-%dT%H:%M:%S")
                    end_dt = datetime.strptime(f"{date} {end_time}", "%Y-%m-%d %I:%M %p")
                    duration = (end_dt - start).total_seconds() / 3600
                    sitting_df["duration_hours"] = round(duration, 2)
                except ValueError:
                    sitting_df["duration_hours"] = ""
            else:
                sitting_df["duration_hours"] = ""

            all_sittings.append(sitting_df)
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

    output = _enrich_with_members(output)

    print(f"\nSaving to {args.output}/")
    save_output(output, args.output, args.fmt)
    print("Done!")
