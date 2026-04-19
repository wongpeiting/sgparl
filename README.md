# Singapore Parliament Hansard data from the command line

A CLI tool for pulling Singapore Parliament Hansard data into structured CSV/JSON files — built for those who want to work with parliamentary speech data without wading through the Hansard website or setting up cloud infrastructure.

## What this is for

Singapore's Parliament publishes Hansard transcripts online, but they're not easy to work with at scale. If you want to know what a minister said about housing across 20 sittings, or compare how much airtime different MPs get on a topic, you're stuck clicking through pages one by one.

[parleh-mate/singapore-parliament-speeches](https://github.com/parleh-mate/singapore-parliament-speeches) solved the scraping problem but built it as a cloud pipeline — BigQuery, Google Drive, Docker, Cloud Functions. This fork strips all of that out and gives you a single command that dumps the data to your laptop.

## What you get

Run the scraper on a sitting date and you get four CSV files:

| File | What's in it |
|------|-------------|
| `speeches.csv` | Every speech paragraph — who said it, party, gender, what they said, word/syllable/sentence counts |
| `topics.csv` | What was discussed — oral answers (OA), bills (BI), motions (MO), etc. |
| `attendance.csv` | Which MPs showed up and which didn't, with party and gender |
| `sittings.csv` | Sitting metadata — parliament number, session, start/end time, duration |

The scraper cleans up the raw Hansard HTML: it identifies speakers, strips procedural boilerplate, standardises MP names (so "The Minister for Health (Mr Ong Ye Kung)" becomes "Ong Ye Kung"), and merges consecutive paragraphs from the same speaker into single entries.

Each speech also gets basic text metrics — word count, character count, sentence count, syllable count — which you can use for readability analysis or just to gauge how much someone said.

### Example: what speeches.csv looks like

| Column | Example |
|--------|---------|
| `date` | 2024-05-07 |
| `speech_id` | 2024-05-07-T-001-S-00002 |
| `topic_id` | 2024-05-07-T-001 |
| `member_name_original` | The Minister for Health (Mr Ong Ye Kung) |
| `member_name` | Ong Ye Kung |
| `text` | Mr Speaker, Sir, my response today will also address... |
| `num_words` | 186 |
| `num_sentences` | 9 |
| `party` | PAP |
| `gender` | M |

## Setup

```bash
pip install -r requirements.txt
```

## Usage

```bash
# Single sitting date
python -m sgparl --date 2024-05-07

# Multiple dates
python -m sgparl --date 2024-05-07 2024-02-05

# Date range (uses seeds/dates.csv for known sitting dates — see note below)
python -m sgparl --from 2024-01-01 --to 2024-03-31

# Save to a specific folder (default: data/)
python -m sgparl --date 2024-05-07 --output my-data/

# Output as JSON instead of CSV, or both
python -m sgparl --date 2024-05-07 --format json
python -m sgparl --date 2024-05-07 --format both

# Update the list of known sitting dates (needed for --from/--to)
python -m sgparl --update-seeds
```

When scraping multiple dates, all results are combined into a single set of files.

### Keeping sitting dates up to date

`--from`/`--to` uses `seeds/dates.csv` (a list of known sitting dates going back to 1955) to figure out which days Parliament actually sat. To bring this list up to date:

```bash
python -m sgparl --update-seeds
```

This scans every weekday from the last known date to today, checks the API, and appends any new sitting dates it finds. Takes a few minutes on the first run (it has to check hundreds of weekdays), but is fast for incremental updates.

After updating, `--from`/`--to` will work for recent dates too.

## Test run: 8 April 2026 sitting (15th Parliament)

```
$ python -m sgparl --date 2026-04-08
Scraping 1 date(s): 2026-04-08
Fetching: https://sprs.parl.gov.sg/search/getHansardReport/?sittingDate=08-04-2026
  [2026-04-08] Parsing...
  [2026-04-08] Done

Saving to data/
  Saved data/sittings.csv (1 rows)
  Saved data/attendance.csv (108 rows)
  Saved data/topics.csv (141 rows)
  Saved data/speeches.csv (465 rows)
Done!
```

Results: 465 speeches from 74 speakers across 141 topics. 95 of 108 MPs present.

Top speakers by word count:

| MP | Speeches | Words |
|----|----------|-------|
| Alvin Tan | 13 | 6,125 |
| Janil Puthucheary | 4 | 3,171 |
| Yip Hon Weng | 7 | 3,142 |
| David Hoe | 8 | 2,746 |
| Neo Kok Beng | 7 | 2,623 |
| Tan See Leng | 14 | 2,149 |
| Chua Kheng Wee Louis | 6 | 2,138 |
| Jamus Jerome Lim | 11 | 2,090 |
| Sun Xueling | 5 | 2,042 |
| Chee Hong Tat | 14 | 1,953 |

## What you can do with this

- **Track what a minister said about a topic over time** — scrape a range of sittings, filter speeches by `member_name` and keywords
- **Compare speaking patterns** — who dominates debate? Word counts and speech counts per MP, party, or gender across a session
- **Find all questions on a topic** — filter topics by `section_type = "OA"` (oral answers) and search titles
- **Attendance tracking** — which MPs were absent most often across sittings
- **Monitor a bill's progress** — filter by `section_type = "BI"` across dates to follow readings and debates
- **Readability analysis** — syllable and sentence counts let you calculate Flesch-Kincaid scores
- **Sitting duration** — `sittings.csv` includes start time, end time (extracted from the Hansard adjournment record), and duration in hours. Budget weeks can run 10+ hours; regular sittings are typically 6-8

The CSVs load straight into Excel, Google Sheets, pandas, R, or whatever you normally use.

## Notes

- All dates use `YYYY-MM-DD` format.
- **`--from`/`--to` date ranges rely on `seeds/dates.csv`.** Run `--update-seeds` to bring it up to date — this checks every weekday against the API, so it takes a few minutes.
- **Speaker name cleaning is regex-based and imperfect.** The scraper handles common formats (Mr/Mrs/Dr/Prof prefixes, ministerial titles) but edge cases may produce odd results — e.g. "Mr Speaker" (a procedural role) gets cleaned to just "Speaker" rather than the actual person's name.
- **Some speech paragraphs are silently dropped** when the HTML structure doesn't match expected patterns (e.g. paragraphs without a `<strong>` tag at the start of a topic). These are mostly procedural text, but some content may be lost.
- **Attendance data may not match who actually spoke.** The Hansard attendance list is a snapshot from the start of the sitting. Ministers who arrive late may be marked absent even though they spoke — e.g. Tan See Leng was marked absent on 8 April 2026 but gave 14 speeches. Cross-check against `speeches.csv`.
- **Colons are stripped from speech text.** The upstream parsing replaces all colons with spaces, so ratios like "1:20" become "1 20" and times like "9:00" become "9 00". Be aware of this if you're searching for specific figures.
- **"The Chairman" speeches have empty `member_name`.** During Committee of Supply debates, speeches are chaired by "The Chairman" instead of the Speaker. The name cleaner doesn't recognise this format, so those rows have blank `member_name` and no party/gender.
- **Procedural entries are included in the data.** Rows where the Speaker calls on the next MP (e.g. "Mr Yip.", "Please proceed.") are captured as speeches. Filter out rows with 3 words or fewer, or where `member_name` is "Speaker" or "Deputy Speaker", if you only want substantive speeches.

## Credits

Built on the parsing logic from [parleh-mate/singapore-parliament-speeches](https://github.com/parleh-mate/singapore-parliament-speeches) by Jeremy Chia ([@jeremychia](https://github.com/jeremychia)), Jon Foong ([@jonfoong](https://github.com/jonfoong)), and Royce Hoe ([@roycehoe](https://github.com/roycehoe)). See the upstream repo for the full cloud pipeline, dbt models, and research references.
