# How to use this scraper

A step-by-step guide. All commands are run from Terminal, inside the project folder.

## First-time setup

### 1. Open Terminal and go to the project folder

```bash
cd ~/singapore-parliament-speeches
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

This installs four Python packages: `requests` (to call the API), `beautifulsoup4` (to parse HTML), `pandas` (to structure the data), and `nltk` (for text metrics like syllable counts). You only need to do this once.

### 3. Update the list of sitting dates

The scraper ships with a list of known Parliament sitting dates (`seeds/dates.csv`), but it may be out of date. Run this to bring it up to today:

```bash
python -m sgparl --update-seeds
```

This checks every weekday from the last known sitting to today against the Parliament API. It takes a few minutes the first time. After that, you only need to run it again when you think there have been new sittings since your last update.

---

## Scraping speeches

> **API migration note (sprs3).** Parliament retired the old
> `getHansardReport` endpoint the main scraper used to call — it now returns
> HTTP 500 for every date. The scraper was rewritten against the current sprs3
> POST endpoints (`searchResult` to list a sitting's reports, `getHansardTopic`
> to fetch each report's text). Two knock-on effects for post-2012 data:
>
> - **`attendance.csv` is empty** — the roll-call attendance list is no longer
>   exposed by the API. (Speeches, topics and sittings are unaffected.)
> - **`sittings.csv` has no `datetime`/`duration_hours`** — the sitting start
>   time is not exposed either, so these are left blank.
>
> Everything that matters for speech analysis — who said what, party, gender,
> word/sentence counts — is fully recovered.

### Post-2012 sittings (main scraper)

You need to know the date Parliament sat. Dates are always in `YYYY-MM-DD` format.

```bash
# Single sitting
python -m sgparl --date 2026-04-08

# Multiple specific dates
python -m sgparl --date 2026-04-07 2026-04-08

# Date range (auto-skips non-sitting days)
python -m sgparl --from 2026-01-01 --to 2026-04-18

# Faster: download reports in parallel (4-6 is a good, polite range)
python -m sgparl --from 2012-09-01 --to 2026-12-31 --workers 5

# Resumable by default — re-running skips dates already in the output.
# Use --fresh to wipe the output and start over.
python -m sgparl --from 2012-09-01 --to 2026-12-31 --workers 5 --fresh

# Save to a different folder
python -m sgparl --date 2026-04-08 --output my-folder/

# Get JSON instead of CSV (or both)
python -m sgparl --date 2026-04-08 --format json
python -m sgparl --date 2026-04-08 --format both
```

This creates four CSV files in the output folder: `speeches.csv`, `topics.csv`, `attendance.csv`, `sittings.csv`.

### Pre-2012 sittings (standalone scraper)

The main scraper only works for post-2012 dates (the sprs3 API). For older sittings (1955-2011), use:

```bash
python -m sgparl.pre2012
```

This uses the `getHansardReport` endpoint and parses the raw HTML. Uses HTML comments as the primary speaker source, with bold tags as fallback. Takes ~5 hours for all 1,333 pre-2012 sitting dates. Checkpoints every 20 dates, so you can interrupt and resume.

Output goes to `data/all/pre2012_v2/`.

### Re-parsing names without rescraping

If you improve the name parser or role-title resolver, you don't need to rescrape — just re-run the parser on the existing data:

```bash
# Reparse the combined dataset
python -m sgparl.reparse

# Reparse a specific file
python -m sgparl.reparse data/all/speeches_post2012.csv

# Reparse all speech CSVs and rebuild speeches_all.csv
python -m sgparl.reparse --all
```

This re-applies `get_mp_name()` (from `sgparl/utils.py`) to every row's `member_name_original` column, then runs role-title resolution (from `sgparl/enrich.py`). The raw `member_name_original` is never modified — only `member_name`, `is_appointment`, and `is_chairing` are updated.

---

## Understanding the output files

### speeches.csv

This is the main file. One row per speech paragraph.

| Column | What it is | Example |
|--------|-----------|---------|
| `date` | Sitting date | 2026-04-08 |
| `speech_id` | Unique ID for each speech | 2026-04-08-T-001-S-00002 |
| `topic_id` | Which topic this speech belongs to | 2026-04-08-T-001 |
| `speech_order` | Order of speech in the sitting | 2 |
| `member_name_original` | Raw name from Hansard (never modified) | The Minister for Health (Mr Ong Ye Kung) |
| `member_name` | Cleaned name | Ong Ye Kung |
| `text` | What they said | Mr Speaker, Sir, my response today... |
| `num_words` | Word count | 186 |
| `num_characters` | Character count (letters only) | 915 |
| `num_sentences` | Sentence count | 9 |
| `num_syllables` | Syllable count | 304 |
| `party` | Political party | PAP |
| `gender` | M or F | M |
| `is_chairing` | Procedural chairing speech? | False |
| `is_appointment` | Speech in official capacity (minister, PM, etc.)? | True |
| `is_noise` | Not a real speech (interjection, procedural marker)? | False |

**`is_chairing`**: `True` for Deputy Speaker chairing utterances (tagged `[Deputy Speaker ... in the Chair]` in Hansard) and "The Chairman" speeches during Committee proceedings. Filter these out for MP activity analysis.

**`is_appointment`**: `True` for any speech made as PM, Minister, Minister of State, Parliamentary Secretary, Deputy PM, Chief Minister, or committee chair. Use `df[~df['is_appointment']]` for backbench-only analysis. Note that some ministers speak in general debate without their title — these are NOT flagged, so `is_appointment` undercounts by ~10-20%.

### topics.csv

One row per topic discussed.

| Column | What it is | Example |
|--------|-----------|---------|
| `topic_id` | Unique ID | 2026-04-08-T-001 |
| `date` | Sitting date | 2026-04-08 |
| `topic_order` | Order in sitting | 1 |
| `title` | Topic title | Policy on Charging Patients who Choose Different Ward Classes |
| `section_type` | Type of business | OA |

**Section types:**
- `OA` = Oral answer (MP asks a question, minister answers)
- `WA` = Written answer
- `WANA` = Written answer not available
- `BI` = Bill (legislation being debated)
- `BP` = Budget/President's Address debate
- `OS` = Other (ministerial statements, etc.)

### attendance.csv

One row per MP per sitting. Post-2012 only.

| Column | What it is | Example |
|--------|-----------|---------|
| `date` | Sitting date | 2026-04-08 |
| `member_name` | MP name | Pritam Singh |
| `is_present` | Whether they attended (corrected) | True |
| `party` | Political party | WP |
| `gender` | M or F | M |

**Attendance correction**: The raw Hansard attendance list is a roll call at the start of the sitting. If an MP gave a speech that day but was marked absent (this happens for 22% of all absences, mostly ministers arriving late from Cabinet), `is_present` is overridden to `True`.

### sittings.csv

One row per sitting (metadata). Post-2012 only.

| Column | What it is | Example |
|--------|-----------|---------|
| `date` | Sitting date | 2026-04-08 |
| `datetime` | Date and start time | 2026-04-08T12:00:00 |
| `parliament` | Parliament number | 15 |
| `session` | Session number | 1 |
| `volume` | Volume number | 95 |
| `sittings` | Sitting number | 135 |
| `end_time` | Adjournment time (from Hansard) | 6:05 PM |
| `duration_hours` | How long the sitting lasted | 6.08 |

---

## The full dataset (data/all/)

The `data/all/` folder contains the merged full-history dataset:

| File | Coverage | Rows |
|------|----------|------|
| `speeches_all.csv` | 1955-2026 | ~906K |
| `topics_all.csv` | 1955-2026 | ~38K |
| `speeches_post2012.csv` | 2011-2026 | ~104K |
| `pre2012_v2/speeches.csv` | 1955-2011 | ~144K |
| `sittings_post2012.csv` | 2012-2026 | 414 |
| `attendance_post2012.csv` | 2012-2026 | ~42K |

`speeches_all.csv` is built by merging the pre-2012 and post-2012 files. Run `python -m sgparl.reparse --all` to rebuild it after any changes.

---

## Common tasks

### "What did Minister X say about topic Y?"

1. Scrape the sittings you're interested in
2. Open `speeches.csv` in Excel or pandas
3. Filter `member_name` for the minister
4. Search the `text` column for your keyword

### "How much did each party speak this sitting?"

1. Open `speeches.csv`
2. Create a pivot table: rows = `party`, values = sum of `num_words`

### "Who was absent?"

1. Open `attendance.csv`
2. Filter `is_present` = False

The attendance data is already corrected — MPs who spoke but were marked absent have been overridden to present.

### "Separate backbencher activity from ministerial activity"

Use the `is_appointment` column:

```python
# Backbench speeches only
backbench = df[~df['is_appointment']]

# Ministerial/appointment speeches only
frontbench = df[df['is_appointment']]
```

Note: some MPs transition between backbencher and minister within a parliament. The `is_appointment` flag is per-speech, not per-MP, so it correctly handles these transitions.

### "Who is 'The Chairman'?"

During Committee of Supply / Committee of the Whole House debates, "The Chairman" refers to whoever is chairing the committee proceedings — typically the Deputy Speaker or an appointed MP. This is NOT the Speaker of Parliament. The role rotates, and we cannot map it to a specific name without per-sitting appointment data. These speeches are flagged `is_chairing = True` and `is_appointment = True`.

### "Give me everything from the current Parliament"

```bash
python -m sgparl --from 2025-09-01 --to 2026-04-18 --output data/15th-parliament --format both
```

---

## Troubleshooting

### "No module named sgparl"

You're not in the right folder. Run `cd ~/singapore-parliament-speeches`.

### "No sitting dates found for the given range"

Your `seeds/dates.csv` might be out of date. Run `python -m sgparl --update-seeds`.

### The API returns an error for a date I know had a sitting

The Parliament API occasionally returns 500 errors. Wait a few minutes and try again.

### Some speeches have empty `member_name`

This happens for:
- Procedural entries: "Speaker", "Deputy Speaker" → cleaned to those role names
- "The Chairman" → left empty (rotating role, can't name-resolve)
- "An hon. Member" → anonymous interjections
- Unrecognised title prefixes → empty (check `member_name_original`)
- Anonymous interjections ("An hon. Member") → flagged `is_noise = True`
- Procedural markers ("ADJOURNMENT", "(Motion)") → flagged `is_noise = True`

### Colons are missing from speech text

The scraper strips all colons from speech text (inherited from the upstream project). Ratios like "1:20" become "1 20". Check the original Hansard to confirm before quoting.

### Lots of very short "speeches" (2-3 words)

The Speaker calling on the next MP ("Mr Yip.", "Please proceed.") is captured as a speech row. Filter with `df[df['num_words'] > 3]`.

### The warnings about "parse error at paragraph"

Harmless. Some topics have non-standard HTML. The scraper skips those paragraphs. Actual speech content is unaffected.
