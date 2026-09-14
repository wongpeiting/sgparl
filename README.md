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
| `attendance.csv` | Which MPs showed up and which didn't, with party and gender. Corrected using speech data. *(Pre-2012 only — the post-2012 API no longer exposes the roll call, so this is empty for post-2012 sittings.)* |
| `sittings.csv` | Sitting metadata — parliament number, session, volume, sitting number. *(Post-2012 `datetime`/`duration_hours` are blank: the current API doesn't expose the sitting start time.)* |

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

# Faster large scrapes: download a sitting's reports in parallel
# (4-6 workers is a good, polite range)
python -m sgparl --from 2012-09-01 --to 2026-12-31 --workers 5

# Save to a specific folder (default: data/)
python -m sgparl --date 2024-05-07 --output my-data/

# Output as JSON instead of CSV, or both
python -m sgparl --date 2024-05-07 --format json
python -m sgparl --date 2024-05-07 --format both

# Update the list of known sitting dates (needed for --from/--to)
python -m sgparl --update-seeds
```

When scraping multiple dates, all results are combined into a single set of files.

**Scrapes are resumable.** Each sitting is written to the output CSVs as soon as
it's parsed, and re-running the same command skips dates already present — so an
interrupted multi-hour scrape picks up where it left off. Pass `--fresh` to wipe
the output and start over.

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

**Why a separate scraper?** Historically both eras were served by the same
`getHansardReport` endpoint in different formats (structured JSON post-2012, a
single `htmlFullContent` HTML blob pre-2012). Parliament has since retired that
endpoint for the current portal — the post-2012 track now uses the sprs3 POST
endpoints (see below), while the pre-2012 track still relies on
`getHansardReport/htmlFullContent`. Different data formats, different parsers.

### The sprs3 API (post-2012)

Parliament rebuilt the Hansard site as a single-page app and retired the old
`GET /search/getHansardReport` endpoint — it now returns **HTTP 500 for every
date**. The post-2012 scraper was rewired onto the endpoints the live site uses:

- **`POST /search/searchResult`** — enumerate the reports for a sitting, filtered
  by a Solr day-range. The endpoint is load-balanced across two backend nodes
  that report slightly different totals, so `sgparl` probes the count, then
  sweeps and dedupes by `reportId` until the full set is collected.
- **`POST /search/getHansardTopic`** — fetch one report's HTML content, which the
  existing parser already understands.

`fetch(date)` stitches these back into the old `{metadata, attendanceList,
takesSectionVOList}` shape, so the rest of the pipeline is unchanged. Two fields
the old date-level report carried are **not** exposed by the new API: the
roll-call attendance list (so `attendance.csv` is empty post-2012) and the
sitting start time (so `sittings.csv` `datetime`/`duration_hours` are blank).
Speeches, topics, names, party/gender and text metrics are fully recovered.

### Known limitations (open)

**Post-2012 attendance and sitting start time are not available.** These are a
consequence of the sprs3 migration above, not a scrape bug, and remain open:

- **`attendance.csv` is empty for post-2012 sittings.** The roll call (present/
  absent per MP) is not served by `searchResult` or `getHansardTopic` — the
  `attendanceList`/`atbpList`/`ptbaList` fields come back empty.
- **`sittings.csv` `datetime` (start time) and `duration_hours` are blank.** The
  API doesn't publish when the House convened. The `end_time` *is* recovered (from
  the "Adjourned accordingly at…" line), as are parliament/session/volume/sitting
  numbers — only the start, and therefore the duration, is missing. The start time
  can't be reliably read from the transcript either: the `<h6>` clock-marks only
  appear inside some debate reports, not Question Time, so the earliest mark is a
  debate timestamp, not the convening time (e.g. 6 Jan 2020's earliest mark is
  3:16 PM, though the House sat well before that).

**Intended fix:** backfill both from Parliament's **Votes & Proceedings** pages
(`parliament.gov.sg/parliamentary-business/votes-and-proceedings`), which publish
per-sitting attendance and convening times independently of the Hansard API —
best as a small standalone module (e.g. `sgparl/votes_proceedings.py`) that
enriches `attendance.csv` and the `datetime`/`duration_hours` columns without
touching the Hansard scrape. Analyses that rely only on `speeches.csv` are
unaffected.

### Keeping sitting dates up to date

`--from`/`--to` uses `seeds/dates.csv` (a list of known sitting dates going back to 1955) to figure out which days Parliament actually sat. To bring this list up to date:

```bash
python -m sgparl --update-seeds
```

This scans every weekday from the last known date to today, checks the API, and appends any new sitting dates it finds.

## Architecture

### Two-track scraper

The two tracks now call **different** endpoints (Parliament retired the shared
one — see the sprs3 note below):

```
                                seeds/dates.csv
                                (1,747 sitting dates, 1955-2026)
                                       |
                  +--------------------+--------------------+
                  |                                         |
            Post-2012 dates                          Pre-2012 dates
            (Version 2, 414 dates)                   (Version 1, 1,333 dates)
                  |                                         |
        python -m sgparl                     python -m sgparl.pre2012
                  |                                         |
    sprs3 POST endpoints:                    getHansardReport returns:
    - searchResult: list a day's             - htmlFullContent (raw HTML)
      reports (dedup across nodes)             Contains ALL topics as
    - getHansardTopic: each report's           concatenated <html> blocks,
      content HTML                             plus PRESENT/ABSENT attendance
      (fetch() rebuilds the old
       metadata/topics shape)
                  |                                         |
    sgparl/parse.py                          sgparl/pre2012.py
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
                              python -m sgparl.reparse --all
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
python -m sgparl.reparse --all
  -> re-applies get_mp_name + resolve_role_titles
  -> outputs speeches_all.csv, topics_all.csv
```

## Key modules

| File | Purpose |
|------|---------|
| `sgparl/api.py` | sprs3 API client — enumerates a sitting's reports (`searchResult`) and fetches each report's content (`getHansardTopic`), then rebuilds the legacy per-sitting shape for the parser |
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
- **Pre-2012 HTML uses a different format** (inline `<b>` tags rather than `<p><strong>` blocks). After multi-speaker splitting, orphan stitching, and HTML refetch recovery, only 0.03% of total words remain unrecoverable — mostly Ministry Addenda, Budget section headers, and short fragments.
- **Attendance is corrected using speech data (pre-2012).** Where an attendance roll call is available, ministers who arrive late are marked absent even if they speak later — 22% of all "absent" records are contradicted by speech data. The scraper overrides `is_present` to `True` for any MP who spoke that day. *Note: the post-2012 sprs3 API no longer exposes the roll call, so `attendance.csv` is empty for post-2012 sittings.*
- **Deputy Speaker chairing speeches are flagged.** When an MP chairs proceedings as Deputy Speaker, Hansard records their procedural utterances under their personal name with a `[Deputy Speaker (Mr X) in the Chair]` tag. These are flagged with `is_chairing = True`. Without this, Christopher de Souza's word count is inflated by 42K words (15%), Charles Chong's by 53K (76%).
- **"The Chairman" speeches are flagged but not name-resolved.** During Committee of Supply debates, "The Chairman" is whoever is chairing — typically the Deputy Speaker or an appointed MP, NOT the Speaker of Parliament. These are flagged `is_chairing = True` and `is_appointment = True` but `member_name` is left empty because the role rotates and we lack per-sitting chair data.
- **Only leading colons are stripped from speech text.** The colon after a speaker's name (the separator) is removed, but colons within the text content (times like "1:20 pm", ratios) are preserved.
- **Anonymous interjections are flagged as noise.** "An hon. Member", "Some hon. Members" = unidentified shouts from the chamber. "ADJOURNMENT", "(Motion)", "ANNUAL BUDGET STATEMENT" = procedural markers. These are flagged with `is_noise = True`. Filter with `speeches[~speeches['is_noise']]`.
- **Multi-speaker blocks are split and stitched.** The pre-2012 HTML parser often lumped entire debates into a single row. These are detected by finding 2+ inline speaker patterns (e.g. "Mr Foo (West Coast):") in the text, then split into individual speeches. Split speech IDs use a `-SPLIT-{N}` suffix. Orphan text fragments produced by imperfect splitting (where the regex cut at a quoted reference like "The Prime Minister said..." rather than a new speaker) are stitched back to the previous speaker — 98,311 fragments (13M words) recovered this way.
- **Pre-2012 data uses full HTML content.** The `pre2012` scraper bypasses the API's broken search pagination by using the `htmlFullContent` endpoint, which returns the complete sitting report as raw HTML. This provides full coverage including all topics per sitting.

## Credits

Built on the parsing logic from [parleh-mate/singapore-parliament-speeches](https://github.com/parleh-mate/singapore-parliament-speeches) by Jeremy Chia ([@jeremychia](https://github.com/jeremychia)), Jon Foong ([@jonfoong](https://github.com/jonfoong)), and Royce Hoe ([@roycehoe](https://github.com/roycehoe)). See the upstream repo for the full cloud pipeline, dbt models, and research references.
