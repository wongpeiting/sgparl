# sgparl/api.py
"""Post-2012 Hansard scraping against the current sprs3 API.

The legacy ``GET /search/getHansardReport/?sittingDate=...`` endpoint that this
module used to call was retired when Parliament rebuilt the Hansard portal as an
Angular SPA (the sprs3 silo). It now returns HTTP 500 for every date. The live
site fetches data through three POST endpoints instead:

  * ``POST /search/searchResult``    -- enumerate the reports for a date range.
  * ``POST /search/getHansardTopic`` -- fetch one report's full HTML content.
  * ``POST /search/fetchData``       -- filter options incl. the MP roster.

``fetch(date)`` is an adapter: it drives the two content endpoints and rebuilds
the ``{metadata, attendanceList, takesSectionVOList}`` structure that the rest of
the pipeline (``parse.py``) already knows how to consume, so no other module had
to change.

Two things the old date-level report used to carry are not exposed by the new
endpoints and are returned best-effort:
  * the sitting's roll-call attendance list (returned empty -> attendance.csv is
    empty for post-2012; correct it later from speech activity if needed);
  * the sitting start time (unknown -> sittings.csv datetime/duration are blank).
Speech content -- the thing that actually matters -- is fully recovered.
"""
import datetime
import time
from concurrent.futures import ThreadPoolExecutor

import requests


BASE = "https://sprs.parl.gov.sg/search"

# The API 500s bare requests; it wants a browser-ish UA and a same-site referer.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Content-Type": "application/json",
    "Referer": "https://sprs.parl.gov.sg/search/",
}

PAGE_SIZE = 20          # searchResult returns at most 20 rows per call
MAX_SWEEPS = 15         # cap on repeated sweeps (see _search_reports)
NO_GAIN_LIMIT = 4       # give up after this many consecutive fruitless sweeps
REQUEST_PAUSE = 0.25    # polite delay between sequential requests (seconds)
MAX_RETRIES = 4         # the API 500s intermittently; retry with backoff
WORKERS = 1             # parallel report-content downloads (set via CLI --workers)

# New report-type prefixes (from the reportId) -> the section_type codes this
# project has always used in topics.csv. Anything unmapped falls back to "OS".
_SECTION_MAP = {
    "oral-answer": "OA",
    "written-answer-na": "WANA",
    "written-answer": "WA",
    "bill-intro": "BI",
    "bill": "BI",
    "budget": "BP",
    "president-address": "BP",
    "presidential-address": "BP",
}

_session = requests.Session()
_session.headers.update(HEADERS)


class NoSittingError(Exception):
    """Raised when the API returns no reports for a given date."""
    pass


def _to_ddmmyyyy(date_str):
    """Convert YYYY-MM-DD to zero-padded DD-MM-YYYY (metadata format)."""
    dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
    return dt.strftime("%d-%m-%Y")


def _solr_daterange(date_str):
    """Build the Solr day-range string searchResult filters on.

    The server ignores the fromday/frommonth/... fields but honours a Solr range
    on the date_dt field, e.g. "2020-01-06T00:00:00Z TO 2020-01-06T23:59:59Z".
    """
    return f"{date_str}T00:00:00Z TO {date_str}T23:59:59Z"


def _search_body(date_str, start_index):
    """Body for a searchResult call: browse (empty keyword) one day, one page."""
    return {
        "keyword": "",
        "reportContent": "with all the words",
        "parliamentNo": "",
        "selectedSort": "date_dt desc",
        "portfolio": [],
        "mpName": "",
        "rsSelected": "",
        "lang": "",
        "startIndex": str(start_index),
        "endIndex": str(start_index + PAGE_SIZE - 1),
        "titleChecked": "false",
        "footNoteChecked": "false",
        "ministrySelected": [],
        "fromday": "", "frommonth": "", "fromyear": "",
        "today": "", "tomonth": "", "toyear": "",
        "dateRange": _solr_daterange(date_str),
    }


def _post(endpoint, payload):
    """POST with retry/backoff. Returns parsed JSON, or raises after retries."""
    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = _session.post(f"{BASE}/{endpoint}", json=payload, timeout=45)
            # The API uses 500 both for real errors and transient hiccups.
            if resp.status_code == 500 and attempt < MAX_RETRIES - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            last_exc = exc
            time.sleep(1.5 * (attempt + 1))
    raise last_exc


def _post_safe(endpoint, payload):
    """Like _post but returns None instead of raising, for best-effort paging."""
    try:
        return _post(endpoint, payload)
    except Exception:
        return None


def _as_list(payload):
    """searchResult returns either a JSON array or an object keyed "0","1",..."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return [v for v in payload.values() if isinstance(v, dict)]
    return []


def _report_prefix(report_id):
    """oral-answer-2101# -> oral-answer (drops the trailing -<n> and #)."""
    rid = report_id.rstrip("#")
    parts = rid.rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0]
    return rid


def _section_type(report_id):
    return _SECTION_MAP.get(_report_prefix(report_id), "OS")


def _report_sort_key(report_id):
    """Deterministic ordering: group by type, then by numeric id ascending."""
    rid = report_id.rstrip("#")
    parts = rid.rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return (parts[0], int(parts[1]))
    return (rid, 0)


def _search_reports(date_str):
    """Enumerate every report for a sitting date.

    searchResult is load-balanced across two backend nodes that report slightly
    different totals and orderings, so a single linear pass through startIndex
    misses rows -- and if the *smaller* node answers first, its maxResult would
    fool us into stopping early. So we (1) probe maxResult a few times and keep
    the largest, then (2) sweep pages deep enough to cover it (plus a buffer),
    deduping by reportId, until the unique count reaches maxResult or several
    consecutive sweeps add nothing new.
    """
    seen = {}
    max_result = 0

    # (1) Probe the reported total a few times; nodes disagree, take the max.
    for _ in range(3):
        rows = [r for r in _as_list(_post_safe("searchResult", _search_body(date_str, 0)))
                if r.get("reportId")]
        if rows:
            max_result = max(max_result, int(rows[0].get("maxResult") or 0))
            for r in rows:
                seen.setdefault(r["reportId"].rstrip("#"), r)
        time.sleep(REQUEST_PAUSE)

    if not max_result:
        return seen, 0

    # (2) Sweep enough pages to cover the larger node, with one buffer page.
    # Page fetches are best-effort: a transient 500 just drops that page, and a
    # later sweep re-fetches it, rather than aborting the whole sitting.
    last_start = max_result + PAGE_SIZE
    no_gain = 0
    for _ in range(MAX_SWEEPS):
        if len(seen) >= max_result:
            break
        before = len(seen)
        for start in range(0, last_start + 1, PAGE_SIZE):
            rows = [r for r in _as_list(_post_safe("searchResult", _search_body(date_str, start)))
                    if r.get("reportId")]
            if not rows:
                continue
            max_result = max(max_result, int(rows[0].get("maxResult") or 0))
            for r in rows:
                seen.setdefault(r["reportId"].rstrip("#"), r)
            time.sleep(REQUEST_PAUSE)
        no_gain = no_gain + 1 if len(seen) == before else 0
        if no_gain >= NO_GAIN_LIMIT:
            break
    return seen, max_result


def _fetch_topic(report_id):
    """Fetch one report's content + metadata. Returns the resultHTML dict,
    or None if the API could not be reached after retries."""
    data = _post_safe("getHansardTopic", {"id": report_id.rstrip("#")})
    if data is None:
        return None
    return data.get("resultHTML") or {}


def _build_metadata(topic_meta, date_str):
    """Assemble the old-shape sitting metadata from a report's metadata.

    Field names differ between the APIs (parlNo vs parlimentNO, etc.). The
    sitting start time is not exposed by the new API, so startTimeStr is left
    blank and parse_sittings degrades gracefully.
    """
    sitting_date = topic_meta.get("sittingDate")
    if sitting_date:
        # New API gives "6-1-2020"; normalise to zero-padded DD-MM-YYYY.
        try:
            dt = datetime.datetime.strptime(sitting_date, "%d-%m-%Y")
            sitting_date = dt.strftime("%d-%m-%Y")
        except ValueError:
            sitting_date = _to_ddmmyyyy(date_str)
    else:
        sitting_date = _to_ddmmyyyy(date_str)

    return {
        "sittingDate": sitting_date,
        "startTimeStr": "",  # not available from the sprs3 API
        "parlimentNO": topic_meta.get("parlNo", ""),
        "sessionNO": topic_meta.get("sessionNo", ""),
        "volumeNO": topic_meta.get("volumeNo", ""),
        "sittingNO": topic_meta.get("sittingNo", ""),
    }


def check_sitting(date):
    """Return True if the date has a parliamentary sitting. Never raises."""
    try:
        rows = _as_list(_post("searchResult", _search_body(date, 0)))
        rows = [r for r in rows if r.get("reportId")]
        return bool(rows and int(rows[0].get("maxResult") or 0) > 0)
    except Exception:
        return False


def fetch(date):
    """Fetch a sitting (YYYY-MM-DD) and return old-shape data for parse.py.

    Returns {"metadata": ..., "attendanceList": [...], "takesSectionVOList": [...]}.
    Raises NoSittingError if the date has no reports.
    """
    print(f"Enumerating reports: {date}")
    reports, max_result = _search_reports(date)
    if not reports:
        raise NoSittingError(f"No sitting found for {date}")

    report_ids = sorted(reports, key=_report_sort_key)
    print(f"  {len(report_ids)} report(s) found"
          + (f" (maxResult={max_result})" if max_result else ""))
    if max_result and len(report_ids) < max_result:
        print(f"  WARNING: collected {len(report_ids)} of {max_result} reports "
              f"for {date} -- some may be missing (API pagination is flaky).")

    # Download each report's content. This is the bulk of the work, so it can be
    # parallelised (WORKERS, set via the CLI). Results are re-ordered to match
    # report_ids so output stays deterministic regardless of completion order.
    if WORKERS > 1:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            topic_metas = list(pool.map(_fetch_topic, report_ids))
    else:
        topic_metas = []
        for report_id in report_ids:
            topic_metas.append(_fetch_topic(report_id))
            time.sleep(REQUEST_PAUSE)

    takes = []
    metadata = None
    failures = 0
    for report_id, topic_meta in zip(report_ids, topic_metas):
        if topic_meta is None:
            failures += 1
            topic_meta = {}
        content = topic_meta.get("content") or reports[report_id].get("content") or ""
        takes.append({
            "title": (topic_meta.get("title")
                      or reports[report_id].get("title") or "").strip(),
            "sectionType": _section_type(report_id),
            "content": content,
        })
        if metadata is None and topic_meta.get("parlNo"):
            metadata = _build_metadata(topic_meta, date)

    if failures:
        print(f"  WARNING: {failures} report(s) failed to download for {date}.")

    if metadata is None:
        # No report yielded usable metadata; fall back to the date alone.
        metadata = _build_metadata({}, date)

    return {
        "metadata": metadata,
        "attendanceList": [],  # roll-call attendance not exposed by sprs3 API
        "takesSectionVOList": takes,
    }
