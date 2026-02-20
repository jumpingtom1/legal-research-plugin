#!/usr/bin/env python3
"""
Session logging for legal research.

Writes error entries and notes into the state file during a session,
then assembles and appends a complete session record to the JSONL log
at session end.

Usage:
  log_session.py error --state-file FILE --level warn|fatal --message MSG [--phase PHASE]
  log_session.py note --state-file FILE --message MSG
  log_session.py summary --state-file FILE [--log-file PATH] [--mode interactive|email|continue] [--output-file PATH]
"""

import sys
import json
import argparse
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from state_io import load_state, save_state


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _ensure_session_log(state):
    """Initialize session_log in state if not present."""
    sl = state.setdefault("session_log", {})
    sl.setdefault("errors", [])
    sl.setdefault("notes", [])


def cmd_error(state, args):
    """Append an error entry to session_log.errors."""
    _ensure_session_log(state)
    sl = state["session_log"]

    # Record started_at on first call if not already set
    if "started_at" not in sl:
        sl["started_at"] = _now()

    entry = {"level": args.level, "message": args.message, "timestamp": _now()}
    if args.phase:
        entry["phase"] = args.phase
    sl["errors"].append(entry)

    print(json.dumps({"logged": "error", "level": args.level, "message": args.message}))


def cmd_note(state, args):
    """Append a note string to session_log.notes."""
    _ensure_session_log(state)
    sl = state["session_log"]

    if "started_at" not in sl:
        sl["started_at"] = _now()

    sl["notes"].append(args.message)
    print(json.dumps({"logged": "note", "message": args.message}))


def cmd_summary(state, args):
    """Assemble session record from state and append one line to the JSONL log."""
    _ensure_session_log(state)
    sl = state["session_log"]

    completed_at = _now()
    started_at = sl.get("started_at")  # May be None if no errors/notes were logged

    parsed_query = state.get("parsed_query", {})
    search_terms = state.get("search_terms_table", [])
    cases_table = state.get("cases_table", [])
    analyzed = state.get("analyzed_cases", [])
    iteration_log = state.get("iteration_log", [])

    # legal_questions_count
    lq = parsed_query.get("legal_questions", [])
    lq_count = len(lq) if isinstance(lq, list) else (1 if lq else 0)

    # Search type breakdown
    keyword_count = sum(1 for s in search_terms if s.get("type") == "keyword")
    semantic_count = sum(1 for s in search_terms if s.get("type") == "semantic")
    citing_count = sum(1 for s in search_terms if s.get("type") == "citing")

    # Relevance and position distributions
    rel_dist = {}
    pos_dist = {}
    for c in analyzed:
        r = str(c.get("relevance_ranking", 0))
        rel_dist[r] = rel_dist.get(r, 0) + 1
        p = c.get("position", "unknown")
        pos_dist[p] = pos_dist.get(p, 0) + 1

    # Pivotal case citations
    pivotal_names = [
        pc.get("name", "")
        for pc in state.get("pivotal_cases", [])
        if isinstance(pc, dict) and pc.get("name")
    ]

    # Refinement rounds: iteration_log entry 0 is round 1 (initial), entries 1+ are refinements
    refinement_rounds = max(0, len(iteration_log) - 1)

    # Quote validation summary (written by run_quote_validation.py)
    qv_data = state.get("quote_validation", {})
    qv_summary = qv_data.get("summary") if qv_data else None
    quote_val = None
    if qv_summary:
        quote_val = {
            "verified": qv_summary.get("verified", 0),
            "likely_match": qv_summary.get("likely_match", 0),
            "not_found": qv_summary.get("not_found", 0),
        }

    record = {
        "session_id": state.get("request_id", "unknown"),
        "started_at": started_at,
        "completed_at": completed_at,
        "mode": args.mode,
        "query": {
            "original": parsed_query.get("original_input", ""),
            "type": parsed_query.get("query_type", ""),
            "jurisdiction": parsed_query.get("jurisdiction", ""),
            "depth_preference": parsed_query.get("depth_preference", "unspecified"),
            "legal_questions_count": lq_count,
        },
        "workflow": {
            "final_mode": state.get("workflow_mode"),
            "refinement_rounds": refinement_rounds,
        },
        "search_stats": {
            "strategies_generated": len(state.get("search_strategies", [])),
            "total_searches": len(search_terms),
            "keyword_searches": keyword_count,
            "semantic_searches": semantic_count,
            "citing_searches": citing_count,
            "total_cases_found": len(cases_table),
            "duplicates_skipped": state.get("total_duplicates_skipped", 0),
            "total_analyzed": len(analyzed),
        },
        "results": {
            "relevance_distribution": rel_dist,
            "position_distribution": pos_dist,
            "pivotal_cases": pivotal_names,
            "quote_validation": quote_val,
        },
        "errors": sl.get("errors", []),
        "notes": sl.get("notes", []),
        "output_file": args.output_file,
    }

    # Print pretty-printed record to stdout as confirmation
    print(json.dumps(record, indent=2, ensure_ascii=False))

    # Append compact line to the JSONL log file
    log_path = Path(args.log_file)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"\nSession record appended to: {log_path}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Session logging for legal research")
    subparsers = parser.add_subparsers(dest="command")

    p_err = subparsers.add_parser("error", help="Log an error to the session")
    p_err.add_argument("--state-file", required=True, help="Path to state JSON file")
    p_err.add_argument("--level", required=True, choices=["warn", "fatal"])
    p_err.add_argument("--message", required=True)
    p_err.add_argument("--phase", default=None, help="Phase label (e.g. 'Phase 2')")

    p_note = subparsers.add_parser("note", help="Log an observation note to the session")
    p_note.add_argument("--state-file", required=True, help="Path to state JSON file")
    p_note.add_argument("--message", required=True)

    p_sum = subparsers.add_parser("summary", help="Assemble and append session record to JSONL log")
    p_sum.add_argument("--state-file", required=True, help="Path to state JSON file")
    p_sum.add_argument("--log-file", default="./legal-research-sessions.jsonl",
                       help="Path to the JSONL log file (default: ./legal-research-sessions.jsonl)")
    p_sum.add_argument("--mode", default="interactive",
                       choices=["interactive", "email", "continue"],
                       help="Session mode")
    p_sum.add_argument("--output-file", default=None,
                       help="Absolute path to the delivered HTML results file")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    state = load_state(args.state_file)

    if args.command == "error":
        cmd_error(state, args)
        save_state(args.state_file, state)
    elif args.command == "note":
        cmd_note(state, args)
        save_state(args.state_file, state)
    elif args.command == "summary":
        cmd_summary(state, args)
        # summary does not need to save state (session_log already flushed by error/note calls)


if __name__ == "__main__":
    main()
