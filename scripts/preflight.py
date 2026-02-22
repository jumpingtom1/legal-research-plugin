#!/usr/bin/env python3
"""Preflight check: verify CourtListener API token and connectivity.

Exits 0 if the API is reachable and the token is valid.
Exits 1 with a descriptive error message on any failure.

Usage:
    python3 preflight.py
"""

import os
import sys
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

API_URL = "https://www.courtlistener.com/api/rest/v4/search/?type=o&q=test&limit=1"

MAX_RETRIES = 3
RETRY_DELAY = 10  # seconds between attempts

token = os.environ.get("COURTLISTENER_API_TOKEN", "").strip()

if not token:
    print("ERROR: COURTLISTENER_API_TOKEN is not set in the environment.")
    print("       Set it with: export COURTLISTENER_API_TOKEN=your-token")
    print("       See CLAUDE.md for setup instructions.")
    sys.exit(1)

req = Request(API_URL, headers={"Authorization": f"Token {token}"})

for attempt in range(1, MAX_RETRIES + 1):
    try:
        with urlopen(req, timeout=30) as resp:
            if resp.status == 200:
                print("PASS: CourtListener API is available.")
                sys.exit(0)
            else:
                print(f"ERROR: CourtListener API returned HTTP {resp.status} (expected 200).")
                sys.exit(1)
    except HTTPError as exc:
        # HTTPErrors are authoritative — don't retry
        if exc.code == 401:
            print("ERROR: CourtListener API token is invalid (HTTP 401 Unauthorized).")
            print("       Check that COURTLISTENER_API_TOKEN is set to a valid token.")
            print("       Generate a token at https://www.courtlistener.com/sign-in/")
        elif exc.code == 429:
            print("ERROR: CourtListener rate limit exceeded (HTTP 429).")
            print("       The daily limit is 5,000 requests. Try again tomorrow.")
        elif exc.code == 403:
            print("ERROR: CourtListener API access forbidden (HTTP 403).")
            print("       Check that your token has API access enabled.")
        else:
            print(f"ERROR: CourtListener API returned HTTP {exc.code}.")
            try:
                body = exc.read().decode("utf-8", errors="replace")[:200]
                if body:
                    print(f"       Response: {body}")
            except Exception:
                pass
        sys.exit(1)
    except URLError as exc:
        reason = str(exc.reason)
        if attempt < MAX_RETRIES:
            print(f"[retry {attempt}/{MAX_RETRIES - 1}] CourtListener API unreachable ({reason}), retrying in {RETRY_DELAY}s...", file=sys.stderr)
            time.sleep(RETRY_DELAY)
            continue
        # All retries exhausted
        if "timed out" in reason.lower():
            print("ERROR: CourtListener API timed out after 30 seconds.")
            print("       Check your network connectivity.")
        else:
            print(f"ERROR: Cannot connect to CourtListener API: {reason}")
            print("       Check your network connectivity.")
        sys.exit(1)
    except Exception as exc:
        print(f"ERROR: Unexpected error during preflight check: {exc}")
        sys.exit(1)

sys.exit(1)
