# Singapore Parliament from the command line

A CLI tool for pulling Singapore Parliament Hansard data into structured CSV/JSON files — built for those who want to work with parliamentary speech data without wading through the Hansard website or setting up cloud infrastructure.

## What this is for

Singapore's Parliament publishes Hansard transcripts online, but they're not easy to work with at scale. If you want to know what a minister said about housing across 20 sittings, or compare how much airtime different MPs get on a topic, you're stuck clicking through pages one by one.

[parleh-mate/singapore-parliament-speeches](https://github.com/parleh-mate/singapore-parliament-speeches) solved the scraping problem but built it as a cloud pipeline — BigQuery, Google Drive, Docker, Cloud Functions. This fork strips all of that out and gives you a single command that dumps the data to your laptop.

## What you get

Run the scraper on a sitting date and you get four CSV files:

| File | What's in it |
|------|-------------|
| `speeches.csv` | Every speech paragraph — who said it, party, gender, what they said, word/syllable/sentence counts, chairing flag, appointment flag, noise flag |
| `topics.csv` | What was discussed — oral answers (OA), bills (BI), motions (MO), etc. |
| `attendance.csv` | Which MPs showed up and which didn't, with party and gender. Corrected using speech data. |
| `sittings.csv` | Sitting metadata — parliament number, session, start/end time, duration |

The scraper cleans up the raw Hansard HTML: it identifies speakers, strips procedural boilerplate, standardises MP names (so "The Minister for Health (Mr Ong Ye Kung)" becomes "Ong Ye Kung"), and merges consecutive paragraphs from the same speaker into single entries.

Each speech also gets basic text metrics — word count, character count, sentence count, syllable count — which you can use for readability analysis or just to gauge how much someone said.

Party and gender data comes from `seeds/member.csv`, which tracks each MP per parliament term. Coverage is comprehensive for the 12th–15th Parliaments (2011 onwards) and selective for earlier parliaments (opposition MPs, key PAP figures, NMPs).

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
| `is_chairing` | False |
| `is_noise` | False |
| `is_appointment` | True |

## Setup

```bash
pip install -r requirements.txt
```

## Usage

### Scraping (post-2012 sittings)

```bash
# Single sitting date
python -m sgparl --date 2024-05-07

# Multiple dates
python -m sgparl --date 2024-05-07 2024-02-05

# Date range (uses seeds/dates.csv for known sitting dates)
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

### Re-parsing names (no rescraping needed)

After improving the name parser (`sgparl/utils.py:get_mp_name`) or the role-title resolver (`sgparl/enrich.py`), re-apply to existing data:

```bash
# Reparse the main dataset
python -m sgparl.reparse

# Reparse a specific file
python -m sgparl.reparse data/all/speeches_post2012.csv

# Reparse all speech CSVs and rebuild speeches_all.csv
python -m sgparl.reparse --all
```

This re-runs `get_mp_name()` on every row's `member_name_original` column (which is preserved from the original scrape) and applies role-title resolution. No API calls.

### Scraping pre-2012 sittings

The main CLI (`python -m sgparl`) only works for post-2012 sittings. For pre-2012 data (1955-2011), use:

```bash
python -m sgparl.pre2012
```

This uses the same `getHansardReport` endpoint, but parses the `htmlFullContent` field (raw HTML of the full sitting report) instead of the structured JSON that post-2012 returns. Uses HTML comments (`<!-- MP_NAME:Name -->`) as the primary speaker source, with bold tags as fallback. Outputs to `data/all/pre2012_v2/`. Takes ~5 hours for all 1,333 dates. Checkpoints every 20 dates.

**Why a separate scraper?** The Parliament API uses the same endpoint for all dates, but the response format changed in September 2012. Post-2012 returns structured JSON (`metadata`, `attendanceList`, `takesSectionVOList`). Pre-2012 returns a single `htmlFullContent` field containing the entire sitting as concatenated HTML blocks. The parsers are different because the data format is different.

### Keeping sitting dates up to date

`--from`/`--to` uses `seeds/dates.csv` (a list of known sitting dates going back to 1955) to figure out which days Parliament actually sat. To bring this list up to date:

```bash
python -m sgparl --update-seeds
```

This scans every weekday from the last known date to today, checks the API, and appends any new sitting dates it finds.

## Architecture

### Two-track scraper

Both tracks call the same API endpoint (`getHansardReport/?sittingDate=`) but parse different response formats:

```
                                seeds/dates.csv
                                (1,747 sitting dates, 1955-2026)
                                       |
                  +--------------------+--------------------+
                  |                                         |
            Post-2012 dates                          Pre-2012 dates
            (Version 2, 414 dates)                   (Version 1, 1,333 dates)
                  |                                         |
        python -m sgparl                     python scrape_pre2012_v2.py
                  |                                         |
    getHansardReport returns:                getHansardReport returns:
    - metadata (JSON)                        - htmlFullContent (raw HTML)
    - attendanceList (JSON array)              Contains ALL topics as
    - takesSectionVOList (JSON array)          concatenated <html> blocks,
      with per-topic content HTML              plus PRESENT/ABSENT attendance
                  |                                         |
    sgparl/parse.py                          scrape_pre2012_v2.py
    (structured JSON parser)                 (HTML parser)
                  |                                         |
    data/all/                                data/all/pre2012_v2/
      speeches_post2012.csv                    speeches.csv
      topics_post2012.csv                      topics.csv
      attendance_post2012.csv                  attendance.csv
      sittings_post2012.csv                    sittings.csv
                  |                                         |
                  +--------------------+--------------------+
                                       |
                              python reparse_names.py --all
                              (shared post-processing)
                                       |
                              +--------+--------+
                              |                 |
                         get_mp_name()    resolve_role_titles()
                         (sgparl/utils.py)  (sgparl/enrich.py)
                              |                 |
                              +--------+--------+
                                       |
                                speeches_all.csv  (merged, cleaned)
                                topics_all.csv
```

### Post-processing pipeline

After scraping, both tracks go through the same cleaning:

```
1. Name parsing      -> sgparl/utils.py:get_mp_name()
                        Handles 20+ prefixes (Mr/Dr/BG/Tun/Maj/etc.)
                        Strips titles, constituencies, initials

2. Role resolution   -> sgparl/enrich.py:resolve_role_titles()
                        "The Prime Minister" -> Lee Kuan Yew (by date)
                        Flags is_appointment, is_chairing, is_noise

3. Multi-speaker     -> Split blocks with 2+ inline speakers
   splitting            Stitch orphan fragments to previous speaker

4. Deduplication     -> Remove exact (date + speaker + text) duplicates

5. Party enrichment  -> Match against seeds/member.csv
                        Verified against parliament.gov.sg official list

6. Section type      -> Normalise pre-2012 codes (OR->OA, WR->WA, etc.)
   normalisation        via section_type_normalised column
```

Then merge and re-enrich:

```
python reparse_names.py --all
  -> re-applies get_mp_name + resolve_role_titles
  -> outputs speeches_all.csv, topics_all.csv
```

## Key modules

| File | Purpose |
|------|---------|
| `sgparl/api.py` | API client — fetches Hansard reports from sprs.parl.gov.sg |
| `sgparl/parse.py` | Parses API responses into DataFrames (sittings, attendance, topics, speeches) |
| `sgparl/utils.py` | Name cleaning (`get_mp_name`), syllable counting, text metrics |
| `sgparl/enrich.py` | Post-processing: resolves role titles to names by date, flags appointment-capacity speeches |
| `sgparl/cli.py` | CLI entry point, member enrichment, attendance correction |
| `sgparl/reparse.py` | Re-applies name parsing + enrichment to existing CSVs without rescraping |
| `sgparl/pre2012.py` | Pre-2012 scraper using `getHansardReport/htmlFullContent` (full coverage, includes attendance) |
| `seeds/dates.csv` | Known sitting dates (1955-2026) |
| `seeds/member.csv` | MP party/gender lookup by parliament term |

## Name parsing

The `get_mp_name()` function in `sgparl/utils.py` handles:

- Standard prefixes: Mr, Mrs, Ms, Mdm, Miss, Dr, Prof
- Military/honorary: BG, RAdm, Tun, Dato, Tuan, Er, Ir
- Gendered: Madam, Encik, Inche, Cik
- Academic: Assoc. Prof.
- Initialled names: "Mr D. S. Marshall (Cairnhill)" → "D. S. Marshall"
- Ministerial titles: "The Minister for Health (Mr Ong Ye Kung)" → "Ong Ye Kung"
- Constituency stripping: "Mr Pritam Singh (Aljunied GRC)" → "Pritam Singh"
- Two-speaker merges: "Mr Wee Toon Boon and Dr Toh Chin Chye" → "Wee Toon Boon"

Role-title resolution (`sgparl/enrich.py`) additionally maps:

- "The Prime Minister" → Lee Kuan Yew / Goh Chok Tong / Lee Hsien Loong / Lawrence Wong (by date)
- "The Chief Minister" → David Marshall / Lim Yew Hock (pre-independence, by date)
- All ministerial/appointment speeches flagged with `is_appointment = True`

## Notes

- All dates use `YYYY-MM-DD` format.
- **`--from`/`--to` date ranges rely on `seeds/dates.csv`.** Run `--update-seeds` to bring it up to date.
- **Pre-2012 HTML uses a different format** (inline `<b>` tags rather than `<p><strong>` blocks). After multi-speaker splitting, orphan stitching, and HTML refetch recovery, only 0.03% of total words remain unrecoverable — mostly Ministry Addenda, Budget section headers, and fragments from topics where the API search limit prevented finding the original HTML.
- **Attendance is corrected using speech data.** The Hansard attendance list is a roll call at the start of the sitting. Ministers who arrive late are marked absent even if they speak later — 22% of all "absent" records are contradicted by speech data. The scraper overrides `is_present` to `True` for any MP who spoke that day.
- **Deputy Speaker chairing speeches are flagged.** When an MP chairs proceedings as Deputy Speaker, Hansard records their procedural utterances under their personal name with a `[Deputy Speaker (Mr X) in the Chair]` tag. These are flagged with `is_chairing = True`. Without this, Christopher de Souza's word count is inflated by 42K words (15%), Charles Chong's by 53K (76%).
- **"The Chairman" speeches are flagged but not name-resolved.** During Committee of Supply debates, "The Chairman" is whoever is chairing — typically the Deputy Speaker or an appointed MP, NOT the Speaker of Parliament. These are flagged `is_chairing = True` and `is_appointment = True` but `member_name` is left empty because the role rotates and we lack per-sitting chair data.
- **Colons are stripped from speech text.** The upstream parsing replaces all colons with spaces ("1:20" becomes "1 20").
- **Anonymous interjections are flagged as noise.** "An hon. Member", "Some hon. Members" = unidentified shouts from the chamber. "ADJOURNMENT", "(Motion)", "ANNUAL BUDGET STATEMENT" = procedural markers. These are flagged with `is_noise = True`. Filter with `speeches[~speeches['is_noise']]`.
- **Multi-speaker blocks are split and stitched.** The pre-2012 HTML parser often lumped entire debates into a single row. These are detected by finding 2+ inline speaker patterns (e.g. "Mr Foo (West Coast):") in the text, then split into individual speeches. Split speech IDs use a `-SPLIT-{N}` suffix. Orphan text fragments produced by imperfect splitting (where the regex cut at a quoted reference like "The Prime Minister said..." rather than a new speaker) are stitched back to the previous speaker — 98,311 fragments (13M words) recovered this way.
- **Pre-2012 data is capped at 20 topics per sitting.** The Parliament API's search pagination is broken (returns the same 20 results regardless of offset). Most sittings have <20 topics; for busier days (e.g. Budget debates) coverage is partial.

## ParlDesk — Newsroom frontend

This repo also includes **ParlDesk**, a newsroom intelligence terminal built on top of the scraped data. It ingests the CSVs into SQLite, runs anomaly detection, and serves a FastAPI dashboard with MP profiles, leaderboards, party breakdowns, and full-text search.

See [`parldesk/README.md`](parldesk/README.md) for full details, or run:

```bash
pip install fastapi uvicorn jinja2
python -m parldesk.ingest    # build SQLite DB (~4 min)
uvicorn parldesk.app:app --reload
open http://localhost:8000
```

## Credits

Built on the parsing logic from [parleh-mate/singapore-parliament-speeches](https://github.com/parleh-mate/singapore-parliament-speeches) by Jeremy Chia ([@jeremychia](https://github.com/jeremychia)), Jon Foong ([@jonfoong](https://github.com/jonfoong)), and Royce Hoe ([@roycehoe](https://github.com/roycehoe)). See the upstream repo for the full cloud pipeline, dbt models, and research references.
