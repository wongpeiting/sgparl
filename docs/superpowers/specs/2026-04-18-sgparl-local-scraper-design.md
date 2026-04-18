# Design: `sgparl` — Local Singapore Parliament Speech Scraper

## Goal

Strip the existing `parleh-mate/singapore-parliament-speeches` pipeline down to a lean CLI tool that scrapes Singapore Parliament Hansard data and saves structured CSV/JSON files locally. No Google Cloud, BigQuery, Google Drive, Telegram, or Docker dependencies.

## CLI interface

```bash
# Single date
python -m sgparl --date 2024-01-15

# Multiple dates
python -m sgparl --date 2024-01-15 2024-02-05

# Date range
python -m sgparl --from 2024-01-01 --to 2024-03-31

# Options
python -m sgparl --date 2024-01-15 --output data/     # default: data/
python -m sgparl --date 2024-01-15 --format csv        # csv, json, or both (default: csv)
python -m sgparl --date 2024-01-15 --format both
```

### Date range behaviour

When `--from` / `--to` is used, the tool consults `seeds/dates.csv` (1,693 known sitting dates from 1955–2024) to find sitting dates in that range, avoiding unnecessary API calls on non-sitting days. If a date isn't in the seed file, the tool still tries the API — this handles newly added sittings not yet in the seed file.

## Output

Four files per run, saved to the output directory:

| File | One row per | Columns |
|------|------------|---------|
| `sittings.csv` | sitting | date, datetime, parliament, session, volume, sittings |
| `attendance.csv` | MP × sitting | date, member_name, is_present |
| `topics.csv` | topic | topic_id, date, topic_order, title, section_type |
| `speeches.csv` | speech paragraph | date, speech_id, topic_id, speech_order, member_name_original, member_name, text, num_words, num_characters, num_sentences, num_syllables |

When fetching multiple dates, results are appended into the same set of files. JSON output mirrors the same structure (one JSON file per table, containing an array of records).

## Package structure

```
sgparl/
  __init__.py      # version
  cli.py           # argparse entry point
  api.py           # fetch from Parliament Hansard API
  parse.py         # transform raw JSON → structured DataFrames
  utils.py         # speaker name cleaning, text metrics (syllable/word/sentence counts)
seeds/
  dates.csv        # known sitting dates (carried over from original repo)
```

### Module responsibilities

**`api.py`** — Thin wrapper around the Parliament Hansard API.
- `fetch(date: str) -> dict` — hits `https://sprs.parl.gov.sg/search/getHansardReport/?sittingDate={dd-mm-yyyy}`, returns parsed JSON response.
- Handles date format conversion (YYYY-MM-DD → DD-MM-YYYY).
- Raises clear errors on non-200 responses or empty results.

**`parse.py`** — Transforms raw API JSON into four pandas DataFrames. Ported directly from the existing `transform/` and `load/*.py` modules:
- `parse_sittings(metadata: dict) -> pd.DataFrame`
- `parse_attendance(date: str, attendance_list: list) -> pd.DataFrame`
- `parse_topics(date: str, topics_list: list) -> pd.DataFrame`
- `parse_speeches(date: str, topics_list: list) -> pd.DataFrame`

The speech parsing includes:
- HTML content extraction with BeautifulSoup (from `<p>` and `<strong>` tags)
- Speaker identification and consecutive-speech merging
- Text cleaning (HTML tags, "proc text", page numbers, non-breaking spaces)
- Text metrics: word count, character count, sentence count, syllable count (using NLTK tokenizer)

**`utils.py`** — Shared helpers:
- `get_mp_name(raw_name: str) -> str` — regex-based speaker name extraction (handles Mr/Mrs/Ms/Dr/Prof prefixes, Speaker titles, etc.). Ported from `transform/__init__.py`.
- `count_syllables(word: str) -> int` — vowel-based syllable counter. Ported from `transform/speeches.py`.

**`cli.py`** — Entry point:
- Parses arguments (`--date`, `--from`, `--to`, `--output`, `--format`)
- Loads `seeds/dates.csv` for date range filtering
- Loops over dates, calling `api.fetch()` then `parse.*()` for each
- Saves output files (CSV via pandas `to_csv`, JSON via `to_json`)
- Prints progress to stdout

## What we port from the original repo

| Original file | What we keep | Where it goes |
|---------------|-------------|---------------|
| `extract/parl_json.py` | `parliament_url()`, `date_yyyymmdd_to_ddmmyyyy()`, `get_json()` | `api.py` |
| `transform/__init__.py` | `get_mp_name()` | `utils.py` |
| `transform/speeches.py` | `process_content()`, `clean_rows()`, `topic_dataframe()`, `count_words_and_characters()`, `calc_number_of_sentences()`, `count_syllables()`, `calc_number_of_syllables()`, `speech_cid()` | `parse.py` + `utils.py` |
| `transform/sittings.py` | `date_str()`, `datetime_str()` | `parse.py` |
| `transform/topics.py` | `topic_cid()` | `parse.py` |
| `transform/attendance.py` | `clean_mp_name()` | `parse.py` (calls `utils.get_mp_name()`) |
| `load/sittings.py`, `load/attendance.py`, `load/topics.py`, `load/speeches.py` | DataFrame assembly logic | `parse.py` |
| `seeds/dates.csv` | The full file | `seeds/dates.csv` |

## What we strip out

- `extract/check_new_date.py` — queries BigQuery for latest date
- `extract/parl_json.py: upload_json()` — uploads to Google Drive
- `load/__init__.py: save_incremental_model_gbq()` — BigQuery writes
- `utils/__init__.py: send_telebot()` — Telegram notifications
- `sgparl_api/` — alternative API module (experimental, not used in main pipeline)
- `Dockerfile`, `Makefile`, `.github/workflows/` — cloud deployment
- `main.py` — replaced by `sgparl/cli.py`
- `nltk_req.py` — NLTK download handled inline in `utils.py`

## Dependencies

```
beautifulsoup4
nltk
pandas
requests
```

No `pandas-gbq`, `google-api-python-client`, `selenium`, `webdriver_manager`, or `dotenv`.

## Error handling

- **No sitting on date**: API returns empty/minimal JSON → log warning, skip date, continue.
- **API unreachable**: Raise error with clear message.
- **Malformed HTML in speech content**: The existing BeautifulSoup parsing already handles this with try/except per `<p>` tag — we keep that.

## NLTK setup

On first run, if the `punkt` or `punkt_tab` tokenizer isn't found, download it automatically via `nltk.download()`. This is the only external data dependency.
