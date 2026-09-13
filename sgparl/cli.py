# sgparl/cli.py
import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd

from datetime import datetime, timedelta

from sgparl import api
from sgparl.api import fetch, check_sitting, NoSittingError
from sgparl.enrich import resolve_role_titles
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


def _correct_attendance(dataframes):
    """Cross-check attendance against speeches and fix false absences.

    The Hansard attendance list appears to be a roll call at the start of
    the sitting. Ministers and MPs who arrive after roll call are marked
    absent even if they speak later. We override is_present to True for
    any MP who gave a speech that day.
    """
    if "attendance" not in dataframes or "speeches" not in dataframes:
        return dataframes

    att = dataframes["attendance"]
    speeches = dataframes["speeches"]

    # Build set of (member_name, date) pairs where the MP spoke
    spoke_pairs = set(
        speeches[["member_name", "date"]].apply(tuple, axis=1)
    )

    # Override: if MP spoke that day, they were present
    original_absent = (~att["is_present"]).sum()
    att["is_present"] = att.apply(
        lambda row: True if (row["member_name"], row["date"]) in spoke_pairs else row["is_present"],
        axis=1,
    )
    corrected_absent = (~att["is_present"]).sum()
    flipped = original_absent - corrected_absent

    if flipped > 0:
        print(f"  Attendance corrected: {flipped} absent record(s) overridden by speech data")

    dataframes["attendance"] = att
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


def append_csv(dataframes, output_dir):
    """Append each DataFrame to its CSV, writing the header only for new files.

    This makes long, multi-date scrapes crash-safe and resumable: each sitting's
    rows land on disk as soon as it's parsed, so an interruption loses at most
    the date in flight.
    """
    os.makedirs(output_dir, exist_ok=True)
    for name, df in dataframes.items():
        path = os.path.join(output_dir, f"{name}.csv")
        header = not os.path.exists(path)
        df.to_csv(path, mode="a", header=header, index=False)


def _already_scraped_dates(output_dir):
    """Dates already present in an existing speeches.csv (for --resume)."""
    path = os.path.join(output_dir, "speeches.csv")
    if not os.path.exists(path):
        return set()
    try:
        return set(pd.read_csv(path, usecols=["date"])["date"].astype(str).unique())
    except Exception:
        return set()


def _process_one_date(date):
    """Fetch, parse and enrich a single sitting. Returns a dict of DataFrames,
    or None if the date has no sitting."""
    data = fetch(date)
    print(f"  [{date}] Parsing...")

    sitting_df = parse_sittings(data["metadata"])
    end_time = extract_adjournment_time(date, data["takesSectionVOList"])
    sitting_df["end_time"] = end_time or ""
    if end_time and sitting_df["datetime"].iloc[0]:
        try:
            start = datetime.strptime(sitting_df["datetime"].iloc[0], "%Y-%m-%dT%H:%M:%S")
            end_dt = datetime.strptime(f"{date} {end_time}", "%Y-%m-%d %I:%M %p")
            sitting_df["duration_hours"] = round((end_dt - start).total_seconds() / 3600, 2)
        except ValueError:
            sitting_df["duration_hours"] = ""
    else:
        sitting_df["duration_hours"] = ""

    dfs = {
        "sittings": sitting_df,
        "attendance": parse_attendance(date, data["attendanceList"]),
        "topics": parse_topics(date, data["takesSectionVOList"]),
        "speeches": parse_speeches(date, data["takesSectionVOList"]),
    }

    dfs = _enrich_with_members(dfs)
    dfs["speeches"] = resolve_role_titles(dfs["speeches"])
    dfs = _correct_attendance(dfs)
    return dfs


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
        "--fresh", action="store_true",
        help="Delete existing output and re-scrape from scratch "
             "(default: resume, skipping dates already scraped)",
    )
    parser.add_argument(
        "--workers", type=int, default=1,
        help="Parallel report-content downloads per sitting (default: 1). "
             "Use 4-6 to speed up large scrapes; higher loads the API more.",
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

    if args.workers and args.workers > 1:
        api.WORKERS = args.workers

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

    # Resumable by default: skip dates already present in the output. Each date
    # is written to disk as it completes, so an interrupted multi-hour scrape can
    # be re-run and picks up where it left off. Pass --fresh to start over.
    if args.fresh:
        for name in ("sittings", "attendance", "topics", "speeches"):
            for ext in ("csv", "json"):
                p = os.path.join(args.output, f"{name}.{ext}")
                if os.path.exists(p):
                    os.remove(p)

    done = _already_scraped_dates(args.output)
    todo = [d for d in dates if d not in done]
    if done:
        print(f"Resuming: {len(done)} date(s) already scraped, {len(todo)} to go.")
    print(f"Scraping {len(todo)} date(s)"
          + (f": {', '.join(todo)}" if len(todo) <= 12 else f" ({todo[0]} ... {todo[-1]})"))

    scraped = 0
    for date in todo:
        try:
            dfs = _process_one_date(date)
            append_csv(dfs, args.output)
            scraped += 1
            print(f"  [{date}] Done "
                  f"({len(dfs['speeches'])} speeches, {len(dfs['topics'])} topics)")
        except NoSittingError:
            print(f"  [{date}] No sitting found, skipping")
        except Exception as e:
            print(f"  [{date}] Error: {e} -- will retry on next run")

    if scraped == 0 and not done:
        print("No data scraped.")
        sys.exit(0)

    # CSVs are the source of truth (written incrementally). If JSON was asked
    # for, regenerate it once at the end from the complete CSVs.
    if args.fmt in ("json", "both"):
        print("\nWriting JSON output from CSVs...")
        for name in ("sittings", "attendance", "topics", "speeches"):
            csv_path = os.path.join(args.output, f"{name}.csv")
            if os.path.exists(csv_path):
                df = pd.read_csv(csv_path)
                with open(os.path.join(args.output, f"{name}.json"), "w") as f:
                    json.dump(df.to_dict(orient="records"), f, indent=2)
        if args.fmt == "json":
            for name in ("sittings", "attendance", "topics", "speeches"):
                p = os.path.join(args.output, f"{name}.csv")
                if os.path.exists(p):
                    os.remove(p)

    print(f"\nDone! Output in {args.output}/")
