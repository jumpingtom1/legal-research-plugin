"""Execute one search strategy against CourtListener v4 API.

Usage:
    python3 search_api.py /path/to/strategy.json

Input:  JSON file with one search strategy (from query-analyst output).
Output: JSON to stdout in add-searches format (no initial_relevance/relevance_note).
Exit 1 on fatal error (all searches failed or missing token).

Strategy JSON format:
    {
      "strategy_id": "S1",
      "keyword_queries": ["..."],
      "semantic_queries": ["..."],
      "court_filter": "ca9",
      "date_filter": {"filed_after": "", "filed_before": ""}
    }
"""

import json
import os
import re
import sys
import time
import urllib.request
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

BASE_V4 = "https://www.courtlistener.com/api/rest/v4"
_RETRY_DELAYS = [0, 4, 8]
_MAX_RESULTS = 15
_MAX_SNIPPET = 150

# Court code → Bluebook abbreviation for citation parentheticals.
# Empty string = SCOTUS (no court name in parens, just year).
_COURT_ABBREVS = {
    # Federal
    "scotus":  "",
    "ca1":     "1st Cir.",
    "ca2":     "2d Cir.",
    "ca3":     "3d Cir.",
    "ca4":     "4th Cir.",
    "ca5":     "5th Cir.",
    "ca6":     "6th Cir.",
    "ca7":     "7th Cir.",
    "ca8":     "8th Cir.",
    "ca9":     "9th Cir.",
    "ca10":    "10th Cir.",
    "ca11":    "11th Cir.",
    "cadc":    "D.C. Cir.",
    "cafc":    "Fed. Cir.",
    # State supreme courts
    "or":      "Or.",
    "wash":    "Wash.",
    "cal":     "Cal.",
    "ny":      "N.Y.",
    "tex":     "Tex.",
    "fla":     "Fla.",
    "ill":     "Ill.",
    "pa":      "Pa.",
    "ohio":    "Ohio",
    "mich":    "Mich.",
    "nj":      "N.J.",
    "va":      "Va.",
    "ga":      "Ga.",
    "nc":      "N.C.",
    "mass":    "Mass.",
    "minn":    "Minn.",
    "mo":      "Mo.",
    "ariz":    "Ariz.",
    "colo":    "Colo.",
    "conn":    "Conn.",
    "ind":     "Ind.",
    "ky":      "Ky.",
    "la":      "La.",
    "md":      "Md.",
    "wis":     "Wis.",
    "sc":      "S.C.",
    "tenn":    "Tenn.",
    "ok":      "Okla.",
    "ark":     "Ark.",
    "miss":    "Miss.",
    "neb":     "Neb.",
    "nev":     "Nev.",
    "nm":      "N.M.",
    "utah":    "Utah",
    "alaska":  "Alaska",
    "hawaii":  "Haw.",
    "idaho":   "Idaho",
    "mont":    "Mont.",
    "nd":      "N.D.",
    "sd":      "S.D.",
    "wyo":     "Wyo.",
    "me":      "Me.",
    "nh":      "N.H.",
    "vt":      "Vt.",
    "ri":      "R.I.",
    "del":     "Del.",
    "dc":      "D.C.",
    "wva":     "W. Va.",
    "al":      "Ala.",
    # State appellate courts
    "orctapp":         "Or. Ct. App.",
    "washctapp":       "Wash. Ct. App.",
    "calctapp":        "Cal. Ct. App.",
    "nyappdiv":        "N.Y. App. Div.",
    "texapp":          "Tex. App.",
    "fladistctapp":    "Fla. Dist. Ct. App.",
    "illappct":        "Ill. App. Ct.",
    "pasuperct":       "Pa. Super. Ct.",
    "ohioctapp":       "Ohio Ct. App.",
    "michctapp":       "Mich. Ct. App.",
    "njsuperctappdiv": "N.J. Super. Ct. App. Div.",
    "vactapp":         "Va. Ct. App.",
    "gactapp":         "Ga. Ct. App.",
    "ncctapp":         "N.C. Ct. App.",
    "massappct":       "Mass. App. Ct.",
    "minnctapp":       "Minn. Ct. App.",
    "moctapp":         "Mo. Ct. App.",
    "arizctapp":       "Ariz. Ct. App.",
    "coloctapp":       "Colo. App.",
    "connapp":         "Conn. App.",
    "indctapp":        "Ind. Ct. App.",
    "kyctapp":         "Ky. Ct. App.",
    "lactapp":         "La. Ct. App.",
    "mdctspecapp":     "Md. Ct. Spec. App.",
    "wisctapp":        "Wis. Ct. App.",
    "scctapp":         "S.C. Ct. App.",
    "tennctapp":       "Tenn. Ct. App.",
    "okctcivapp":      "Okla. Civ. App.",
    "arkctapp":        "Ark. Ct. App.",
    "missctapp":       "Miss. Ct. App.",
    "nebctapp":        "Neb. Ct. App.",
    "nmctapp":         "N.M. Ct. App.",
    "utahctapp":       "Utah Ct. App.",
    "alaskactapp":     "Alaska Ct. App.",
    "hawapp":          "Haw. App.",
    "alctcivapp":      "Ala. Civ. App.",
    "alctcrimapp":     "Ala. Crim. App.",
}


def _api_get(url, params, token):
    """GET request with retries. Returns (data_dict, None, retry_count) or (None, error_str, retry_count)."""
    query_string = urllib.parse.urlencode(params, doseq=True)
    full_url = f"{url}?{query_string}"
    headers = {
        "Authorization": f"Token {token}",
        "Accept": "application/json",
        "User-Agent": "legal-research-plugin/1.0",
    }
    last_err = None
    retries = 0
    for delay in _RETRY_DELAYS:
        if delay:
            time.sleep(delay)
            retries += 1
        try:
            req = urllib.request.Request(full_url, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8")), None, retries
        except urllib.error.HTTPError as exc:
            code = exc.code
            body = exc.read().decode("utf-8", errors="replace")[:300]
            if code == 401:
                return None, "Invalid API token. Check COURTLISTENER_API_TOKEN.", retries
            if code == 429:
                return None, "Rate limit exceeded (429).", retries
            if code == 404:
                return None, "HTTP 404 — Not found", retries
            return None, f"HTTP {code} — {body}", retries
        except Exception as exc:
            last_err = exc
    return None, f"Request failed after {len(_RETRY_DELAYS)} attempts — {last_err}", retries


def _build_bluebook(result, court_code):
    """Build best-effort Bluebook citation from API result fields."""
    case_name = result.get("caseName", "Unknown")
    date_filed = result.get("dateFiled", "") or ""
    year = date_filed[:4] if len(date_filed) >= 4 else "?"

    # Look up court abbreviation; fall back to raw court code
    court_abbrev = _COURT_ABBREVS.get(court_code, court_code)

    # Find the primary citation from the citations array
    cites = result.get("citations") or []
    cite_str = ""
    for c in cites:
        if isinstance(c, dict):
            vol = str(c.get("volume", "") or "").strip()
            rep = str(c.get("reporter", "") or "").strip()
            page = str(c.get("page", "") or "").strip()
            if vol and rep and page:
                cite_str = f"{vol} {rep} {page}"
                break

    if cite_str:
        if not court_abbrev:  # SCOTUS: no court name in parens
            return f"_{case_name}_, {cite_str} ({year})"
        return f"_{case_name}_, {cite_str} ({court_abbrev} {year})"
    else:
        if not court_abbrev:  # SCOTUS, no citation available
            return f"_{case_name}_ ({year})"
        return f"_{case_name}_ ({court_abbrev} {year})"


def _result_to_case(result, court_filter):
    """Convert one API search result to the output case dict."""
    cluster_id = result.get("cluster_id")
    if not cluster_id:
        return None

    court_code = result.get("court", court_filter or "")
    absolute_url = result.get("absolute_url", "") or ""
    url = f"https://www.courtlistener.com{absolute_url}" if absolute_url else ""

    snippet_raw = result.get("snippet", "") or ""
    snippet = re.sub(r"</?mark>", "", snippet_raw)[:_MAX_SNIPPET]

    cites = result.get("citations") or []
    citations_raw = []
    for c in cites:
        if isinstance(c, dict):
            vol = str(c.get("volume", "") or "").strip()
            rep = str(c.get("reporter", "") or "").strip()
            page = str(c.get("page", "") or "").strip()
            if rep:
                citations_raw.append(f"{vol} {rep} {page}".strip())
        else:
            citations_raw.append(str(c))

    cite_count = result.get("citeCount") or result.get("citation_count") or 0

    return {
        "cluster_id": cluster_id,
        "case_name": result.get("caseName", "Unknown"),
        "bluebook_citation": _build_bluebook(result, court_code),
        "citations_raw": citations_raw,
        "court": court_code,
        "date_filed": result.get("dateFiled", ""),
        "url": url,
        "snippet": snippet,
        "cite_count": cite_count,
    }


def _execute_search(query, search_type, court_filter, date_filter, token):
    """Execute one keyword or semantic search. Returns (results, count, error_or_None, retry_count)."""
    params = {
        "type": "o",
        "q": query,
        "order_by": "score desc",
        "limit": _MAX_RESULTS,
    }
    if search_type == "semantic":
        params["semantic"] = "true"
    if court_filter:
        params["court"] = court_filter
    if isinstance(date_filter, dict):
        if date_filter.get("filed_after"):
            params["filed_after"] = date_filter["filed_after"]
        if date_filter.get("filed_before"):
            params["filed_before"] = date_filter["filed_before"]

    data, err, retries = _api_get(f"{BASE_V4}/search/", params, token)
    if err:
        return [], 0, err, retries
    results = data.get("results", [])
    count = data.get("count", len(results))
    return results, count, None, retries


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: search_api.py <strategy_json_path>", "strategy_id": "unknown"}))
        sys.exit(1)

    strategy_path = sys.argv[1]
    try:
        with open(strategy_path, "r", encoding="utf-8") as f:
            strategy = json.load(f)
    except Exception as exc:
        print(json.dumps({"error": f"Failed to load strategy: {exc}", "strategy_id": "unknown"}))
        sys.exit(1)

    strategy_id = strategy.get("strategy_id", "S?")
    keyword_queries = strategy.get("keyword_queries") or []
    semantic_queries = strategy.get("semantic_queries") or []
    court_filter = strategy.get("court_filter", "") or ""
    date_filter = strategy.get("date_filter") or {}

    token = os.environ.get("COURTLISTENER_API_TOKEN", "")
    if not token:
        print(json.dumps({"error": "COURTLISTENER_API_TOKEN is not set", "strategy_id": strategy_id}))
        sys.exit(1)

    start_time = time.monotonic()
    searches_executed = []
    seen_ids = set()
    all_cases = []
    errors = []
    total_retries = 0
    total_queries = len(keyword_queries) + len(semantic_queries)

    for query in keyword_queries:
        q_start = time.monotonic()
        results, count, err, retries = _execute_search(query, "keyword", court_filter, date_filter, token)
        elapsed = int((time.monotonic() - q_start) * 1000)
        total_retries += retries
        entry = {"type": "keyword", "query": query, "result_count": 0,
                 "court_filter": court_filter, "date_filter": date_filter,
                 "elapsed_ms": elapsed, "retries": retries}
        if err:
            errors.append(f"keyword '{query[:60]}': {err}")
            entry["error"] = err
            searches_executed.append(entry)
            continue
        entry["result_count"] = count
        searches_executed.append(entry)
        for r in results:
            case = _result_to_case(r, court_filter)
            if case and case["cluster_id"] not in seen_ids:
                seen_ids.add(case["cluster_id"])
                all_cases.append(case)

    for query in semantic_queries:
        q_start = time.monotonic()
        results, count, err, retries = _execute_search(query, "semantic", court_filter, date_filter, token)
        elapsed = int((time.monotonic() - q_start) * 1000)
        total_retries += retries
        entry = {"type": "semantic", "query": query, "result_count": 0,
                 "court_filter": court_filter, "date_filter": date_filter,
                 "elapsed_ms": elapsed, "retries": retries}
        if err:
            errors.append(f"semantic '{query[:60]}': {err}")
            entry["error"] = err
            searches_executed.append(entry)
            continue
        entry["result_count"] = count
        searches_executed.append(entry)
        for r in results:
            case = _result_to_case(r, court_filter)
            if case and case["cluster_id"] not in seen_ids:
                seen_ids.add(case["cluster_id"])
                all_cases.append(case)

    if total_queries > 0 and len(errors) == total_queries:
        print(json.dumps({
            "error": f"All {total_queries} search(es) failed. First error: {errors[0]}",
            "strategy_id": strategy_id,
            "total_elapsed_ms": int((time.monotonic() - start_time) * 1000),
            "total_retries": total_retries,
        }))
        sys.exit(1)

    # Cap at 20 unique cases per strategy (cross-strategy dedup handled by manage_state.py)
    all_cases = all_cases[:20]

    notes_parts = []
    if errors:
        notes_parts.append(f"{len(errors)}/{total_queries} search(es) failed: {errors[0]}")

    output = {
        "strategy_id": strategy_id,
        "searches_executed": searches_executed,
        "cases": all_cases,
        "total_unique_cases": len(all_cases),
        "total_elapsed_ms": int((time.monotonic() - start_time) * 1000),
        "total_retries": total_retries,
        "notes": "; ".join(notes_parts),
    }
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
