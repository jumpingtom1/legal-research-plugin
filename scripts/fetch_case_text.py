"""Pre-fetch opinion text for one cluster_id before case-analyzer runs.

Usage:
    python3 fetch_case_text.py <cluster_id> [--output-dir /tmp]

Writes /tmp/vq_opinion_{cluster_id}.txt (up to 50,000 chars).
Prints JSON to stdout: success metadata or error info.
Exit 0 on success, 1 on failure.

This script mirrors the logic of mcp-server/server.py get_case_text,
using urllib (stdlib only) instead of httpx.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

BASE_V3 = "https://www.courtlistener.com/api/rest/v3"
_RETRY_DELAYS = [0, 4, 8]
_MAX_CHARS = 50000

# HTML fields to try in order when plain_text is absent (mirrors server.py)
_HTML_FIELDS = [
    "html_with_citations",
    "html",
    "html_columbia",
    "html_lawbox",
    "html_anon_2020",
    "xml_harvard",
]


def _api_get(url, token):
    """GET request with retries. Returns (data_dict, None, retry_count) or (None, error_str, retry_count)."""
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
            req = urllib.request.Request(url, headers=headers)
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


def _strip_html(text):
    """Remove HTML/XML tags from a string."""
    return re.sub(r"<[^>]+>", "", text)


def main():
    parser = argparse.ArgumentParser(
        description="Pre-fetch opinion text for one cluster_id."
    )
    parser.add_argument("cluster_id", type=int, help="CourtListener cluster ID")
    parser.add_argument(
        "--output-dir", default="/tmp",
        help="Directory to write opinion file (default: /tmp)"
    )
    args = parser.parse_args()

    cluster_id = args.cluster_id
    output_dir = Path(args.output_dir)

    token = os.environ.get("COURTLISTENER_API_TOKEN", "")
    if not token:
        print(json.dumps({
            "cluster_id": cluster_id,
            "error": "COURTLISTENER_API_TOKEN is not set",
            "chars_saved": 0,
            "elapsed_ms": 0,
            "total_retries": 0,
        }))
        sys.exit(1)

    start_time = time.monotonic()
    total_retries = 0

    # Step 1: Fetch cluster metadata
    cluster_data, err, retries = _api_get(f"{BASE_V3}/clusters/{cluster_id}/", token)
    total_retries += retries
    if err:
        print(json.dumps({
            "cluster_id": cluster_id,
            "error": f"Cluster fetch failed: {err}",
            "chars_saved": 0,
            "elapsed_ms": int((time.monotonic() - start_time) * 1000),
            "total_retries": total_retries,
        }))
        sys.exit(1)

    case_name = cluster_data.get("case_name", "Unknown")
    date_filed = cluster_data.get("date_filed", "")
    absolute_url = cluster_data.get("absolute_url", "")
    sub_opinions = cluster_data.get("sub_opinions", [])

    if not sub_opinions:
        print(json.dumps({
            "cluster_id": cluster_id,
            "error": f"No sub_opinions in cluster {cluster_id}",
            "chars_saved": 0,
            "elapsed_ms": int((time.monotonic() - start_time) * 1000),
            "total_retries": total_retries,
        }))
        sys.exit(1)

    # Step 2: Parse opinion_id from first sub_opinion URL
    first_url = sub_opinions[0] if isinstance(sub_opinions[0], str) else ""
    match = re.search(r"/opinions/(\d+)/", first_url)
    if not match:
        print(json.dumps({
            "cluster_id": cluster_id,
            "error": f"Could not parse opinion_id from sub_opinions URL: {first_url!r}",
            "chars_saved": 0,
            "elapsed_ms": int((time.monotonic() - start_time) * 1000),
            "total_retries": total_retries,
        }))
        sys.exit(1)

    opinion_id = int(match.group(1))

    # Step 3: Fetch opinion text
    opinion_data, err, retries = _api_get(f"{BASE_V3}/opinions/{opinion_id}/", token)
    total_retries += retries
    if err:
        print(json.dumps({
            "cluster_id": cluster_id,
            "opinion_id": opinion_id,
            "error": f"Opinion fetch failed: {err}",
            "chars_saved": 0,
            "elapsed_ms": int((time.monotonic() - start_time) * 1000),
            "total_retries": total_retries,
        }))
        sys.exit(1)

    # Extract text: plain_text first, then HTML fields with tag-stripping
    text = opinion_data.get("plain_text", "") or ""
    if not text:
        for field in _HTML_FIELDS:
            raw = opinion_data.get(field, "") or ""
            if raw:
                text = _strip_html(raw)
                break

    if not text:
        print(json.dumps({
            "cluster_id": cluster_id,
            "opinion_id": opinion_id,
            "error": f"No text content available for opinion {opinion_id}",
            "chars_saved": 0,
            "elapsed_ms": int((time.monotonic() - start_time) * 1000),
            "total_retries": total_retries,
        }))
        sys.exit(1)

    # Truncate to max chars (same limit as MCP get_case_text)
    truncated = len(text) > _MAX_CHARS
    text = text[:_MAX_CHARS]

    # Write to output file
    output_path = output_dir / f"vq_opinion_{cluster_id}.txt"
    try:
        output_path.write_text(text, encoding="utf-8")
    except Exception as exc:
        print(json.dumps({
            "cluster_id": cluster_id,
            "opinion_id": opinion_id,
            "error": f"Write failed: {exc}",
            "chars_saved": 0,
            "elapsed_ms": int((time.monotonic() - start_time) * 1000),
            "total_retries": total_retries,
        }))
        sys.exit(1)

    print(json.dumps({
        "cluster_id": cluster_id,
        "opinion_id": opinion_id,
        "case_name": case_name,
        "date_filed": date_filed,
        "absolute_url": absolute_url,
        "chars_saved": len(text),
        "truncated": truncated,
        "elapsed_ms": int((time.monotonic() - start_time) * 1000),
        "total_retries": total_retries,
        "error": None,
    }))


if __name__ == "__main__":
    main()
