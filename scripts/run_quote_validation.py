#!/usr/bin/env python3
"""
Orchestrate quote validation for a legal research session.
Checks opinion files, runs the matcher, annotates the HTML, updates state.

Usage:
  run_quote_validation.py <state_file> [--annotate]

Without --annotate: runs matcher only, outputs summary and missing files list.
With --annotate: also runs the annotator on the HTML results file.
"""

import sys
import json
import subprocess
import os
from datetime import datetime
from pathlib import Path

from state_io import load_state, save_state, normalize_excerpts


SCRIPT_DIR = Path(__file__).parent
MATCHER_SCRIPT = SCRIPT_DIR / "vq_matcher.py"
ANNOTATOR_SCRIPT = SCRIPT_DIR / "vq_annotator.py"


def check_opinion_files(cluster_ids):
    """Check which opinion files exist and are valid."""
    present = {}
    missing = []
    for cid in cluster_ids:
        path = f"/tmp/vq_opinion_{cid}.txt"
        if os.path.exists(path):
            size = os.path.getsize(path)
            if size < 1000:
                missing.append({"cluster_id": cid, "reason": f"too small ({size} bytes)"})
            else:
                present[cid] = {"path": path, "size": size}
        else:
            missing.append({"cluster_id": cid, "reason": "file not found"})
    return present, missing


def run_matcher(opinion_path, excerpts, cluster_id):
    """Run vq_matcher.py for a single case. Returns list of results."""
    if not excerpts:
        return []

    excerpts_path = f"/tmp/vq_excerpts_{cluster_id}.json"
    with open(excerpts_path, "w", encoding="utf-8") as f:
        json.dump(excerpts, f, ensure_ascii=False)

    try:
        result = subprocess.run(
            ["python3", str(MATCHER_SCRIPT), opinion_path, excerpts_path],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            print(f"  Matcher error for {cluster_id}: {result.stderr}", file=sys.stderr)
            return [{"excerpt_index": i, "status": "not_found", "match_tier": "error",
                     "similarity": 0.0, "best_match_preview": ""}
                    for i in range(len(excerpts))]
        return json.loads(result.stdout)
    except Exception as ex:
        print(f"  Matcher exception for {cluster_id}: {ex}", file=sys.stderr)
        return [{"excerpt_index": i, "status": "not_found", "match_tier": "error",
                 "similarity": 0.0, "best_match_preview": ""}
                for i in range(len(excerpts))]


def run_annotator(html_path, results_with_text):
    """Run vq_annotator.py on the HTML file."""
    results_path = f"/tmp/vq_annotator_input.json"
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(results_with_text, f, ensure_ascii=False)

    result = subprocess.run(
        ["python3", str(ANNOTATOR_SCRIPT), html_path, results_path],
        capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        print(f"Annotator error: {result.stderr}", file=sys.stderr)
        return False
    return True


def main():
    if len(sys.argv) < 2:
        print("Usage: run_quote_validation.py <state_file> [--annotate]", file=sys.stderr)
        sys.exit(1)

    state_path = sys.argv[1]
    do_annotate = "--annotate" in sys.argv

    state = load_state(state_path)
    analyzed_cases = state.get("analyzed_cases", [])

    if not analyzed_cases:
        print(json.dumps({"status": "no_cases", "message": "No analyzed cases to validate"}))
        return

    # Collect all cluster_ids that have excerpts
    cases_with_excerpts = []
    for c in analyzed_cases:
        normalized = normalize_excerpts(c)
        if normalized:
            c["_normalized_excerpts"] = normalized
            cases_with_excerpts.append(c)

    if not cases_with_excerpts:
        print(json.dumps({"status": "no_excerpts", "message": "No excerpts to validate"}))
        return

    cluster_ids = [c["cluster_id"] for c in cases_with_excerpts]

    # Step 1: Check opinion files
    present, missing = check_opinion_files(cluster_ids)

    # Step 2: Run matcher for each case with a valid opinion file
    all_results = []
    summary = {"total": 0, "verified": 0, "likely_match": 0, "possible_match": 0,
               "not_found": 0, "not_found_truncated": 0, "skipped": 0}
    per_case_summary = []

    for case in cases_with_excerpts:
        cid = case["cluster_id"]
        case_name = case.get("case_name", f"cluster_{cid}")
        excerpts = case.get("_normalized_excerpts", [])

        if cid not in present:
            # Mark all excerpts as not_found_truncated
            for i, ex in enumerate(excerpts):
                r = {
                    "cluster_id": cid,
                    "case_name": case_name,
                    "excerpt_index": i,
                    "excerpt_preview": ex.get("text", "")[:80],
                    "excerpt_text": ex.get("text", ""),
                    "status": "not_found_truncated",
                    "match_tier": "none",
                    "similarity": 0.0,
                    "best_match_preview": ""
                }
                all_results.append(r)
                summary["not_found_truncated"] += 1
                summary["total"] += 1
            per_case_summary.append(f"{case_name}: {len(excerpts)} excerpts — all skipped (opinion file missing)")
            continue

        opinion_info = present[cid]
        matcher_results = run_matcher(opinion_info["path"], excerpts, cid)

        case_counts = {"verified": 0, "likely_match": 0, "possible_match": 0,
                       "not_found": 0, "not_found_truncated": 0}

        for mr in matcher_results:
            idx = mr.get("excerpt_index", 0)
            status = mr.get("status", "not_found")

            # Reclassify not_found as truncated if opinion was near limit
            if status == "not_found" and opinion_info["size"] >= 49500:
                status = "not_found_truncated"

            excerpt_text = excerpts[idx].get("text", "") if idx < len(excerpts) else ""
            r = {
                "cluster_id": cid,
                "case_name": case_name,
                "excerpt_index": idx,
                "excerpt_preview": excerpt_text[:80],
                "excerpt_text": excerpt_text,
                "status": status,
                "match_tier": mr.get("match_tier", "none"),
                "similarity": mr.get("similarity", 0.0),
                "best_match_preview": mr.get("best_match_preview", "")
            }
            all_results.append(r)
            summary["total"] += 1
            if status in summary:
                summary[status] += 1
            case_counts[status] = case_counts.get(status, 0) + 1

        counts_str = ", ".join(f"{v} {k}" for k, v in case_counts.items() if v > 0)
        per_case_summary.append(f"{case_name}: {len(excerpts)} excerpts — {counts_str}")

    # Step 3: Annotate HTML if requested
    annotated = False
    if do_annotate:
        search_id = state.get("search_id", "")
        html_path = state_path.replace("-state.json", "-results.html")
        if os.path.exists(html_path):
            annotated = run_annotator(html_path, all_results)

    # Step 4: Update state
    state["quote_validation"] = {
        "validated_at": datetime.now().isoformat(),
        "summary": summary,
        "results": [
            {k: v for k, v in r.items() if k != "excerpt_text"}
            for r in all_results
        ]
    }
    save_state(state_path, state)

    # Output
    output = {
        "status": "complete",
        "summary": summary,
        "per_case": per_case_summary,
        "missing_opinion_files": missing,
        "html_annotated": annotated,
        "not_found_excerpts": [
            {"case_name": r["case_name"], "excerpt_preview": r["excerpt_preview"],
             "similarity": r["similarity"]}
            for r in all_results if r["status"] == "not_found"
        ]
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
