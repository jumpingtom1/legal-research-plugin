#!/usr/bin/env python3
"""Check subsequent history for one analyzed case via CourtListener v4 API.

Usage:
    python3 check_subsequent_history.py <cluster_id> [--court "Court Name"] [--date-filed "2021-07-14"]

Runs two queries per case:
  1. Negative treatment keywords (reversed, overruled, vacated, etc.)
  2. All recent citers (catches same-court rehearing, higher-court review)

Deduplicates across both queries by cluster_id.
Prints JSON to stdout: citing cases with query_source tags and stats.
Exit 0 on success, 1 on fatal error.
"""

import argparse
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
_MAX_RESULTS = 10


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


def _strip_mark_tags(text):
    """Remove <mark> and </mark> tags from snippet text."""
    return re.sub(r"</?mark>", "", text)


def _parse_results(data, query_tag):
    """Parse v4 search results into citing case dicts."""
    cases = []
    for r in data.get("results", []):
        cluster_id = r.get("cluster_id")
        if not cluster_id:
            continue
        absolute_url = r.get("absolute_url", "") or ""
        url = f"https://www.courtlistener.com{absolute_url}" if absolute_url else ""
        snippet_raw = r.get("snippet", "") or ""
        snippet = _strip_mark_tags(snippet_raw)[:300]
        cases.append({
            "cluster_id": cluster_id,
            "case_name": r.get("caseName", "Unknown"),
            "court": r.get("court", ""),
            "date_filed": r.get("dateFiled", ""),
            "snippet": snippet,
            "url": url,
            "cite_count": r.get("citeCount") or r.get("citation_count") or 0,
            "query_source": [query_tag],
        })
    return cases


def main():
    parser = argparse.ArgumentParser(
        description="Check subsequent history for one cluster_id."
    )
    parser.add_argument("cluster_id", type=int, help="CourtListener cluster ID")
    parser.add_argument("--court", default="", help="Court name of the case being checked")
    parser.add_argument("--date-filed", default="", help="Date filed of the case being checked")
    args = parser.parse_args()

    cluster_id = args.cluster_id
    court = args.court
    date_filed = args.date_filed

    token = os.environ.get("COURTLISTENER_API_TOKEN", "")
    if not token:
        print(json.dumps({
            "cluster_id": cluster_id,
            "case_name": "",
            "court": court,
            "date_filed": date_filed,
            "citing_cases": [],
            "query_stats": {"negative_kw_count": 0, "recent_citers_count": 0, "total_unique": 0},
            "elapsed_ms": 0,
            "total_retries": 0,
            "error": "COURTLISTENER_API_TOKEN is not set",
        }))
        sys.exit(1)

    start_time = time.monotonic()
    total_retries = 0

    # Query 1: Negative treatment keywords
    negative_q = (
        f'cites:({cluster_id}) AND '
        f'(reversed OR overruled OR vacated OR remanded OR modified '
        f'OR superseded OR "rehearing granted" OR "on further review" '
        f'OR "review allowed" OR "on appeal")'
    )
    neg_params = {
        "type": "o",
        "q": negative_q,
        "order_by": "dateFiled desc",
        "limit": _MAX_RESULTS,
    }

    neg_data, neg_err, retries = _api_get(f"{BASE_V4}/search/", neg_params, token)
    total_retries += retries
    neg_cases = []
    neg_count = 0
    if neg_err:
        # Log but don't fatal — try the second query
        pass
    else:
        neg_cases = _parse_results(neg_data, "negative_keywords")
        neg_count = len(neg_cases)

    # Query 2: All recent citers (no keyword filter)
    recent_q = f"cites:({cluster_id})"
    recent_params = {
        "type": "o",
        "q": recent_q,
        "order_by": "dateFiled desc",
        "limit": _MAX_RESULTS,
    }

    recent_data, recent_err, retries = _api_get(f"{BASE_V4}/search/", recent_params, token)
    total_retries += retries
    recent_cases = []
    recent_count = 0
    if recent_err:
        pass
    else:
        recent_cases = _parse_results(recent_data, "recent_citers")
        recent_count = len(recent_cases)

    # Both queries failed
    if neg_err and recent_err:
        print(json.dumps({
            "cluster_id": cluster_id,
            "case_name": "",
            "court": court,
            "date_filed": date_filed,
            "citing_cases": [],
            "query_stats": {"negative_kw_count": 0, "recent_citers_count": 0, "total_unique": 0},
            "elapsed_ms": int((time.monotonic() - start_time) * 1000),
            "total_retries": total_retries,
            "error": f"Both queries failed: neg={neg_err}; recent={recent_err}",
        }))
        sys.exit(1)

    # Deduplicate across both queries by cluster_id, merging query_source tags
    merged = {}
    for c in neg_cases:
        cid = c["cluster_id"]
        if cid == cluster_id:
            continue  # Skip self-citation
        merged[cid] = c

    for c in recent_cases:
        cid = c["cluster_id"]
        if cid == cluster_id:
            continue
        if cid in merged:
            # Merge query_source tags
            existing_sources = set(merged[cid]["query_source"])
            for tag in c["query_source"]:
                if tag not in existing_sources:
                    merged[cid]["query_source"].append(tag)
        else:
            merged[cid] = c

    citing_cases = list(merged.values())
    # Sort by date descending
    citing_cases.sort(key=lambda x: x.get("date_filed", ""), reverse=True)

    elapsed_ms = int((time.monotonic() - start_time) * 1000)

    output = {
        "cluster_id": cluster_id,
        "case_name": "",
        "court": court,
        "date_filed": date_filed,
        "citing_cases": citing_cases,
        "query_stats": {
            "negative_kw_count": neg_count,
            "recent_citers_count": recent_count,
            "total_unique": len(citing_cases),
        },
        "elapsed_ms": elapsed_ms,
        "total_retries": total_retries,
        "error": None,
    }
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
