# SGParl Pulse — Design Spec

**Date:** 2026-05-12
**Status:** Draft
**Purpose:** Internal newsroom intelligence terminal for ST political desk. Daily-use tool that surfaces parliamentary signals, anomalies, and story leads from 71 years of Hansard data (348k speeches).

---

## 1. Architecture

```
sgparl scrape (existing CLI)
    ↓
CSVs (build artifacts, not source of truth)
    ↓
ingest.py (normalize → SQLite, compute aggregates, detect anomalies)
    ↓
sgparl_pulse.db (SQLite + FTS5)
    ↓
FastAPI + Jinja2 templates + HTMX
    ↓
Browser (localhost)
```

### Nightly pipeline (cron or GitHub Action)

```bash
python -m sgparl --update-seeds
python -m sgparl --from $(date -v-7d +%Y-%m-%d) --to $(date +%Y-%m-%d)
python -m sgparl.reparse --all
python -m pulse.ingest           # builds temp.db, then atomic mv → sgparl_pulse.db
```

**Idempotency:** Rerunning the same day is harmless. `ingest.py` builds into a temp DB and atomically swaps (`temp.db` → `sgparl_pulse.db`), so partial failures never corrupt the live database.

**Future migration path:** CSVs are the intermediate format for v1. `pulse/ingest.py` is designed with pluggable readers so a future version can ingest directly from the scraper or from parquet/JSON without changing downstream schema.

---

## 2. Database Schema

### Core tables (mirror existing CSVs)

**speeches**
| Column | Type | Notes |
|--------|------|-------|
| speech_id | TEXT PK | `{date}-T-{topic_order}-S-{speech_order}` |
| date | TEXT | ISO date, indexed |
| topic_id | TEXT FK | |
| speech_order | INTEGER | |
| member_name | TEXT | Cleaned name, indexed |
| member_name_original | TEXT | Raw Hansard, never modified |
| text | TEXT | Full speech content |
| num_words | INTEGER | |
| num_characters | INTEGER | |
| num_sentences | INTEGER | |
| num_syllables | INTEGER | |
| is_chairing | BOOLEAN | |
| is_appointment | BOOLEAN | |
| is_noise | BOOLEAN | |
| is_vernacular_placeholder | BOOLEAN | |

**topics**
| Column | Type |
|--------|------|
| topic_id | TEXT PK |
| date | TEXT, indexed |
| topic_order | INTEGER |
| title | TEXT |
| section_type | TEXT | OA/WA/BI/BP/OS/etc |
| section_type_normalised | TEXT |

**attendance**
| Column | Type |
|--------|------|
| date | TEXT, indexed |
| member_name | TEXT, indexed |
| is_present | BOOLEAN |

**sittings**
| Column | Type |
|--------|------|
| date | TEXT PK |
| datetime | TEXT |
| parliament | TEXT |
| session | TEXT |
| volume | TEXT |
| sittings | TEXT |
| start_time | TEXT |
| end_time | TEXT |
| duration_hours | REAL |

**members**
| Column | Type |
|--------|------|
| member_id | INTEGER PK | Auto-generated |
| mp_name | TEXT |
| party | TEXT |
| gender | TEXT |
| parliament | INTEGER |

Composite unique on (mp_name, parliament).

### Search

**speeches_fts** (FTS5 virtual table)
- Indexed columns: `speech_id`, `text`, `title` (from joined topics)
- Metadata stays in core tables; join on `speech_id` for filtering
- Supports: phrase search, boolean operators, prefix queries

### Aggregate tables (rebuilt by ingest.py)

**mp_stats** — core per-MP per-parliament rollups
| Column | Type | Notes |
|--------|------|-------|
| member_id | INTEGER FK | |
| member_name | TEXT | For display convenience |
| parliament | INTEGER | |
| party | TEXT | |
| gender | TEXT | |
| total_words | INTEGER | Excludes is_noise, is_chairing |
| total_speeches | INTEGER | |
| attendance_rate | REAL | % of sittings attended |
| avg_speech_length | REAL | Words per speech |
| sittings_attended | INTEGER | |
| questions_asked_oral | INTEGER | First speech per OA topic by this MP (questioner heuristic) |
| questions_asked_written | INTEGER | First speech per WA topic by this MP |

**mp_topics** — per-MP per-topic aggregates
| Column | Type |
|--------|------|
| member_name | TEXT |
| parliament | INTEGER |
| topic_title | TEXT |
| section_type | TEXT |
| speech_count | INTEGER |
| word_count | INTEGER |

**sitting_stats** — per-sitting rollups
| Column | Type |
|--------|------|
| date | TEXT PK |
| parliament | TEXT |
| duration_hours | REAL |
| duration_percentile | REAL | Percentile rank vs all sittings |
| total_speeches | INTEGER |
| total_words | INTEGER |
| unique_speakers | INTEGER |
| topics_debated | INTEGER |
| absent_count | INTEGER |

**topic_counts** — topic frequency across sittings
| Column | Type |
|--------|------|
| topic_title | TEXT |
| section_type | TEXT |
| sitting_count | INTEGER |
| total_speeches | INTEGER |
| total_words | INTEGER |

### Signal tables

**anomalies** — pre-computed anomaly rows
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| date | TEXT | When detected / sitting date |
| entity_type | TEXT | `mp`, `topic`, `sitting`, `system` |
| entity_id | TEXT | MP name, topic title, sitting date |
| metric_name | TEXT | e.g. `word_count`, `topic_frequency`, `absence` |
| observed_value | REAL | |
| baseline_value | REAL | |
| z_score | REAL | |
| percentile | REAL | |
| severity | TEXT | `major`, `moderate`, `info` |
| explanation | TEXT | Human-readable: "Housing mentions spiked to 42 (baseline avg: 9)" |

Severity thresholds:
- **Major** (red): z > 3 or first-time-ever events
- **Moderate** (amber): z > 2
- **Info** (grey): z > 1.5

**pulse_metrics** — generic metric store for homepage signals
| Column | Type | Notes |
|--------|------|-------|
| metric_name | TEXT | e.g. `top_speaker`, `longest_speech_minutes` |
| date | TEXT | Sitting date this metric refers to |
| value | TEXT | The metric value (stringified) |
| metadata_json | TEXT | Optional JSON blob for structured extras |

### Meta

**metadata** — key-value operational state
| Key | Example value |
|-----|---------------|
| last_scrape_at | 2025-05-12T08:12:00 |
| last_db_build_at | 2025-05-12T08:14:32 |
| latest_sitting_date | 2025-05-07 |
| total_speeches | 348348 |

Displayed in header: "Last updated: 8:12 AM"

---

## 3. Anomaly Engine

Runs during `ingest.py`. Populates the `anomalies` table.

### v1 anomaly types

| Type | Entity | Detection logic |
|------|--------|-----------------|
| `silence_broken` | mp | MP spoke for first time in N sittings where N > their average gap |
| `unusual_absence` | mp | MP absent but attended >90% of recent sittings |
| `topic_spike` | topic | Topic mentioned N times vs historical baseline, z > 2 |
| `duration_outlier` | sitting | Sitting duration in 90th+ percentile |
| `word_count_surge` | mp | MP's word count this sitting >2 sigma above their per-sitting average |
| `floor_dominance` | mp | Non-ministerial MP spoke >15% of total sitting words |
| `question_burst` | mp/party | Party asked 2x+ their average number of questions |

Each anomaly generates an `explanation` from templates:
- "Housing mentions spiked to 42 (baseline avg: 9, z=3.1)"
- "Pritam Singh spoke 0 words -- first silence since 2019-03-04"

### Prioritization

The homepage hero anomaly is the highest-severity, most-recent anomaly. The anomaly stack below it is ordered by severity descending, then recency.

---

## 4. Frontend

### Tech stack

| Layer | Choice |
|-------|--------|
| Backend | FastAPI |
| Templates | Jinja2 |
| Interactivity | HTMX (fragment polling, no JS framework) |
| Charts | Inline SVG sparklines, CSS bar charts. No D3 unless needed. |
| Styling | Dark muted theme. Monospace numbers. Red for anomalies only. |

### Visual identity: Newsroom Terminal (Approach 1.5)

- Dark grey background (not pure black)
- Monospace/tabular figures for numbers — columns align
- Restrained color: red = anomaly, green = up arrow (sparingly), muted blue for links, everything else greyscale
- Dense cards, minimal whitespace, high information per pixel
- One editorial flourish: the hero anomaly card has slightly more visual weight

### HTMX polling

- Default: 30-minute poll interval
- On sitting days (checked via `metadata.latest_sitting_date == today`): 5-minute poll
- Each zone is an independent HTMX fragment with its own endpoint

### Navigation grammar

```
Homepage → anomaly click → /mp/{slug}-{id} or /sitting/{date}
Homepage → search → results → /mp/ or /sitting/ or speech
Homepage → leaderboard click → /mp/{slug}-{id}
/mp/{slug}-{id} → speech → /sitting/{date}#speech-{id}
/sitting/{date} → speaker click → /mp/{slug}-{id}
```

### Routes

Routes use IDs, not names. Display names in UI only.

| Route | Page |
|-------|------|
| `/` | Homepage (Pulse dashboard) |
| `/mp/{slug}-{id}` | MP profile |
| `/sitting/{date}` | Sitting view |
| `/topics` | Topic explorer |
| `/search?q=` | Search results (FTS5) |

---

## 5. Page Designs

### 5.1 Homepage

```
┌───────────────────────────────────────────────────────────┐
│ SGParl Pulse   [Search speeches, MPs, topics...]          │
│                                     Last updated: 8:12 AM │
├───────────────────────────┬───────────────────────────────┤
│                           │                               │
│  * TODAY'S OUTLIER        │  LATEST SITTING: 7 May 2025   │
│  Housing mentions spiked  │                               │
│  240% above baseline      │  Duration: 8h 21m (93rd %ile) │
│  ▓▓▓▓▓▓▓▓▓▓▓▓▓░░ z=3.1   │  ┌────────────────────┐      │
│                           │  │ Speaking share bars │      │
│  ● Unusual absence (2)    │  └────────────────────┘      │
│  ● Duration outlier       │  Topics: [Housing] [AI] [CPF] │
│  ○ Silence broken         │  Speakers: 42 | Absent: 3    │
│  ○ Topic shift            │                               │
├───────────────────────────┴───────────────────────────────┤
│  LEADERBOARDS (15th Parliament)          [More indices >] │
│                                                           │
│  Top Talkers           Most Questions     Attendance      │
│  1. Name  12.4k ↑+2k  1. Name  42 ↑5    1. Name  100%   │
│  2. Name   9.8k --    2. Name  38 ↑3    2. Name   98%   │
│  3. Name   8.1k ↓-1k  3. Name  31 --    3. Name   97%   │
│                                                           │
│  [More indices >] reveals: Avg Speech Length,             │
│  Reply Inflation, Floor Dominance                         │
├───────────────────────────────────────────────────────────┤
│  Recently viewed: [Tan] [Singh] [Lim]  Topics > Sittings>│
└───────────────────────────────────────────────────────────┘
```

**Recess mode:** When no sitting in the last 14 days, the digest zone shows "Parliament in recess" and surfaces the top anomalies/trends from the most recent active period instead. Homepage never feels dead.

**Leaderboard defaults:** Top Talkers, Most Questions, Attendance. Quirky indices (Avg Speech Length, Reply Inflation, Floor Dominance) behind "More indices" toggle. Protects against spreadsheet syndrome.

**Leaderboard sparklines:** Each entry shows a micro trend arrow (↑/↓/--) comparing to 30 days ago. Keeps the dashboard feeling alive.

**Recently viewed:** Browser session memory. Tracks last 5 MPs and searches viewed this session. Small feature, disproportionate utility for repetitive newsroom workflows.

**Metric naming:** "Avg Speech Length" on the leaderboard (neutral framing). "Brevity Index" as secondary tooltip only. Same care for "Floor Dominance" -- factual label, editorial implication left to the journalist.

### 5.2 MP Profile (`/mp/{slug}-{id}`)

```
┌───────────────────────────────────────────────────────────┐
│ < Pulse    Pritam Singh                       WP | 15th P │
│            Leader of the Opposition                       │
├───────────────────────────┬───────────────────────────────┤
│  ACTIVITY SUMMARY         │  TOPIC FINGERPRINT            │
│  Speeches: 847            │                               │
│  Words: 312,409           │  ▓▓▓▓▓▓▓ Housing (142)        │
│  Questions: 186 (OA)      │  ▓▓▓▓▓░ Labour (98)           │
│  Attendance: 96.2%        │  ▓▓▓▓░░ Healthcare (76)       │
│  Avg length: 368 words    │  ▓▓▓░░░ Education (51)        │
│  ──────────────────       │  ▓▓░░░░ Foreign affairs (34)  │
│  Floor Dominance: 4.2%    │                               │
│                           │  [View all topics >]          │
├───────────────────────────┴───────────────────────────────┤
│  ACTIVITY TIMELINE (per month)                            │
│  2012 ░░▓░░░▓▓▓░░░ 2015 ░▓▓▓░░▓▓░░░░ 2019 ▓▓▓▓▓▓▓▓▓░  │
│                                               ↑ GE2020    │
├───────────────────────────────────────────────────────────┤
│  RECENT SPEECHES                           [Filter >]     │
│  7 May 2025 | Housing (OA) | 842 words | Full text >      │
│  5 May 2025 | CPF Bill     | 1,204 words | Full text >    │
│  ...                                                      │
└───────────────────────────────────────────────────────────┘
```

- **Activity timeline:** Per-month buckets. Each cell = one month, intensity = speech count that month. Clean, not decorative fog.
- **Topic fingerprint:** Horizontal bars from `mp_topics`, sorted by speech count. Clickable -- links to topic explorer filtered by this MP.
- **Recent speeches:** Expandable full text inline. Filterable by section_type and date range.

### 5.3 Sitting View (`/sitting/{date}`)

- Duration + percentile gauge
- Speaking share breakdown (CSS bar chart: who dominated the floor)
- All topics debated, with speech counts and word counts per topic
- Attendance list with anomaly flags for unexpected absences
- **Within-sitting search:** Text box at top for filtering speeches within this sitting. Local filtering, no server round-trip.
- **Jump to speaker:** Clicking a name in the speaking share chart scrolls to their first speech
- **Copy quote:** Small copy button on each speech block for easy quoting
- Full transcript browser, collapsible by topic, with speaker labels

### 5.4 Topic Explorer (`/topics`)

- Search/browse topics across all sittings
- Topic frequency over time (per-month chart)
- Top speakers on this topic
- Related topics (co-occurring in same sittings)
- All speeches on this topic, filterable by date range and MP

**Topic taxonomy (future risk, v1 mitigation):**
Topics come from Hansard titles -- they fragment ("housing", "public housing", "HDB", "resale flats"). v1 ships with raw titles. v1.1 adds a manually curated `topic_dictionary` seed file mapping aliases to canonical topics. Not glamorous, very useful. Without this, the topic explorer becomes lexical chaos.

### 5.5 Search Results (`/search?q=`)

- FTS5-powered full-text search
- Results show: speech snippet with highlighted match, MP name, date, topic
- Faceted filtering: by MP, by date range, by section_type
- Links to MP profile, sitting view, or inline speech expansion

---

## 6. Indices Reference

### Default leaderboards (always visible)

| Index | Measures | Source |
|-------|----------|--------|
| **Top Talkers** | Total words (backbench: `~is_appointment`) | `mp_stats.total_words` |
| **Most Questions** | Oral questions asked this Parliament | `speeches` in OA sections, questioner role |
| **Attendance** | % of sittings attended | `mp_stats.attendance_rate` |

### Secondary indices (behind "More")

| Index | Measures | Source |
|-------|----------|--------|
| **Avg Speech Length** | Words per speech (tooltip: "Brevity Index") | `mp_stats.avg_speech_length` |
| **Reply Inflation** | Ministerial answer length / question length | OA speech pairs (first speech = question, second = answer) |
| **Floor Dominance** | % of sitting words spoken by one MP | Per-sitting computation |

---

## 7. Build Order

### Phase 1: Backend / Data
1. `ingest.py` -- CSV reader, SQLite schema creation, data loading
2. Aggregate tables (`mp_stats`, `mp_topics`, `sitting_stats`, `topic_counts`)
3. Anomaly engine (detection functions, `anomalies` table)
4. FTS5 index
5. `metadata` table
6. Atomic temp-DB swap
7. Tests for ingest, aggregates, anomaly detection

### Phase 2: Frontend
1. FastAPI app skeleton, Jinja2 templates, static assets, HTMX
2. Homepage (Pulse dashboard with all zones)
3. MP profile page
4. Search results page
5. Sitting view page
6. Topic explorer page

### Phase 3: Polish
1. Recess mode (graceful degradation)
2. Recently viewed session memory
3. Within-sitting search
4. Copy quote buttons
5. Sparkline trends on leaderboards

### Future (v1.5+)
- Compare mode (`/compare?members=123,456`)
- Topic taxonomy / dictionary
- Direct SQLite ingestion (skip CSV intermediate)
- Public read-only mode

---

## 8. Verification

### Testing the data layer
- `pytest` for `ingest.py`: test schema creation, aggregate computation, anomaly detection against fixture data
- Verify atomic DB swap: interrupt mid-build, confirm live DB is uncorrupted
- Verify FTS5: search for known phrases, check result accuracy

### Testing the frontend
- Start FastAPI dev server: `uvicorn pulse.app:app --reload`
- Open `localhost:8000` -- homepage loads with real data
- Click through: anomaly → MP page → speech → sitting page
- Search for known MP name, known speech phrase
- Verify HTMX polling: check network tab for fragment refreshes
- Verify recess mode: set `latest_sitting_date` to 30 days ago, confirm graceful fallback

### End-to-end
- Run full pipeline: scrape → reparse → ingest → start server
- Confirm new sitting data appears on homepage after ingest
- Confirm anomalies are detected and displayed with correct severity

---

## 9. Key Files to Create

| File | Purpose |
|------|---------|
| `pulse/ingest.py` | CSV → SQLite ingestion, aggregates, anomalies |
| `pulse/anomalies.py` | Anomaly detection functions |
| `pulse/app.py` | FastAPI application |
| `pulse/templates/base.html` | Base template (dark theme, HTMX, global search) |
| `pulse/templates/index.html` | Homepage |
| `pulse/templates/mp.html` | MP profile |
| `pulse/templates/sitting.html` | Sitting view |
| `pulse/templates/topics.html` | Topic explorer |
| `pulse/templates/search.html` | Search results |
| `pulse/templates/fragments/` | HTMX partial templates (anomalies, digest, leaderboards) |
| `pulse/static/style.css` | Dark terminal theme |
| `seeds/topic_dictionary.csv` | Topic aliases (v1.1) |
