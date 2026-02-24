#!/usr/bin/env python3
"""
State file management for legal research sessions.
Handles deduplication, lead tracking, and refinement decisions.

Usage:
  manage_state.py --state <path> <command> [args...]

Commands:
  add-searches <json_file>    Merge case-searcher results into state, dedup by cluster_id
  add-analysis <json_file>    Merge case-analyzer result(s) into state
  get-leads                   Return unexplored follow-up leads as JSON
  top-candidates <n>          Return top N candidates for deep analysis (not yet analyzed)
  summary                     Return summary counts as JSON
  should-refine               Decide whether to perform additional search rounds
"""

import sys
import json
import argparse
from pathlib import Path

from state_io import load_state, save_state


def cmd_add_searches(state, args):
    """Merge case-searcher results into state. Dedup by cluster_id."""
    with open(args.json_file, "r", encoding="utf-8") as f:
        searcher_output = json.load(f)

    existing_ids = {c["cluster_id"] for c in state.get("cases_table", [])}
    cases = searcher_output.get("cases", [])
    searches = searcher_output.get("searches_executed", [])
    strategy_id = searcher_output.get("strategy_id", "")
    round_num = args.round if args.round else len(state.get("iteration_log", [])) + 1

    new_count = 0
    for case in cases:
        cid = case.get("cluster_id")
        if cid and cid not in existing_ids:
            state.setdefault("cases_table", []).append(case)
            existing_ids.add(cid)
            new_count += 1

    for search in searches:
        search["strategy"] = strategy_id
        search["round"] = round_num
        state.setdefault("search_terms_table", []).append(search)

    # Store raw results (tag with round number for diminishing-returns check)
    searcher_output["round"] = round_num
    state.setdefault("search_results_raw", []).append(searcher_output)

    dups_this_call = len(cases) - new_count
    state["total_duplicates_skipped"] = state.get("total_duplicates_skipped", 0) + dups_this_call

    total = len(state.get("cases_table", []))
    result = {
        "new_cases_added": new_count,
        "duplicates_skipped": dups_this_call,
        "searches_logged": len(searches),
        "total_cases_in_state": total
    }
    print(json.dumps(result))


def cmd_add_analysis(state, args):
    """Merge case-analyzer result(s) into state."""
    with open(args.json_file, "r", encoding="utf-8") as f:
        analysis = json.load(f)

    # Accept either a single object or an array
    if isinstance(analysis, dict):
        analyses = [analysis]
    else:
        analyses = analysis

    existing_ids = {c["cluster_id"] for c in state.get("analyzed_cases", [])}
    added = 0
    for a in analyses:
        cid = a.get("cluster_id")
        if cid and cid not in existing_ids:
            state.setdefault("analyzed_cases", []).append(a)
            existing_ids.add(cid)
            added += 1

            # Extract follow-up leads into pending_leads
            follow_up = a.get("follow_up", {})
            for case_ref in follow_up.get("cases_to_examine", []):
                lead = {
                    "type": "citation",
                    "text": case_ref,
                    "source_cluster_id": cid,
                    "source_case": a.get("case_name", "")
                }
                state.setdefault("pending_leads", []).append(lead)
            # Extract new search terms from the analysis
            # (The orchestrator can also add terms manually)

    # Track which cluster_ids have been analyzed
    state.setdefault("explored_cluster_ids", [])
    for a in analyses:
        cid = a.get("cluster_id")
        if cid and cid not in state["explored_cluster_ids"]:
            state["explored_cluster_ids"].append(cid)

    result = {
        "analyses_added": added,
        "total_analyzed": len(state.get("analyzed_cases", [])),
        "pending_leads": len(state.get("pending_leads", []))
    }
    print(json.dumps(result))


def _is_citation_known(lead, known_names):
    """Check if a citation lead matches any known case name (heuristic)."""
    lead_text = lead.get("text", "").lower()
    return any(name in lead_text or lead_text in name
               for name in known_names if name)


def _get_known_case_names(state):
    """Return lowercase case names from the cases table."""
    return {c.get("case_name", "").lower() for c in state.get("cases_table", [])}


def _count_unexplored_citations(state):
    """Count citation leads not yet matched to known cases."""
    known_names = _get_known_case_names(state)
    count = 0
    for lead in state.get("pending_leads", []):
        if lead.get("type") == "citation" and not _is_citation_known(lead, known_names):
            count += 1
    return count


def cmd_get_leads(state, args):
    """Return unexplored follow-up leads."""
    pending = state.get("pending_leads", [])
    known_names = _get_known_case_names(state)
    explored_terms = {t.lower() for t in state.get("explored_terms", [])}

    unexplored = []
    for lead in pending:
        if lead.get("type") == "citation":
            if not _is_citation_known(lead, known_names):
                unexplored.append(lead)
        elif lead.get("type") == "search_term":
            if lead.get("term", "").lower() not in explored_terms:
                unexplored.append(lead)

    # Deduplicate leads by text
    seen = set()
    unique = []
    for lead in unexplored:
        key = lead.get("text", lead.get("term", "")).lower().strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(lead)

    result = {
        "unexplored_leads": unique,
        "total_pending": len(pending),
        "total_unexplored": len(unique)
    }
    print(json.dumps(result, indent=2))


def _pivotal_year(state):
    """Return the earliest pivotal case year from state, or None."""
    pivotal_cases = state.get("pivotal_cases", [])
    if not pivotal_cases:
        return None
    years = []
    for pc in pivotal_cases:
        name = pc.get("name", "")
        # Extract 4-digit year from Bluebook citation, e.g. "(9th Cir. 2010)"
        import re
        m = re.search(r'\b(19|20)\d{2}\b', name)
        if m:
            years.append(int(m.group()))
    return min(years) if years else None


def cmd_top_candidates(state, args):
    """Return top N candidates from cases_table not yet analyzed."""
    n = int(args.n)
    analyzed_ids = {c.get("cluster_id") for c in state.get("analyzed_cases", [])}
    candidates = [c for c in state.get("cases_table", [])
                  if c.get("cluster_id") not in analyzed_ids]

    pivot_year = _pivotal_year(state)

    def sort_key(x):
        initial_rel = x.get("initial_relevance", 0)
        cite_count = x.get("cite_count", 0)
        # Secondary tiebreaker: pre-pivotal cases sorted lower (0 = pre-pivotal, 1 = post/unknown)
        if pivot_year:
            date_filed = x.get("date_filed", "") or ""
            case_year = int(date_filed[:4]) if len(date_filed) >= 4 and date_filed[:4].isdigit() else 9999
            post_pivotal = 0 if case_year < pivot_year else 1
        else:
            post_pivotal = 1
        # context_match penalty: absent = -2 effective relevance
        context_match = x.get("context_match", "n/a")
        context_penalty = -2 if context_match == "absent" else 0
        return (initial_rel + context_penalty, post_pivotal, cite_count)

    candidates.sort(key=sort_key, reverse=True)

    top = candidates[:n]
    result = {
        "candidates": top,
        "total_unanalyzed": len(candidates),
        "returned": len(top)
    }
    print(json.dumps(result, indent=2))


def cmd_summary(state, args):
    """Return summary counts."""
    cases_table = state.get("cases_table", [])
    analyzed = state.get("analyzed_cases", [])
    search_terms = state.get("search_terms_table", [])
    iteration_log = state.get("iteration_log", [])
    pending_leads = state.get("pending_leads", [])

    # Relevance distribution from analyzed cases
    rel_dist = {}
    for c in analyzed:
        r = c.get("relevance_ranking", 0)
        rel_dist[r] = rel_dist.get(r, 0) + 1

    # Position distribution
    pos_dist = {}
    for c in analyzed:
        p = c.get("position", "unknown")
        pos_dist[p] = pos_dist.get(p, 0) + 1

    pivotal_cases = state.get("pivotal_cases", [])

    result = {
        "request_id": state.get("request_id", ""),
        "query_type": state.get("parsed_query", {}).get("query_type", ""),
        "workflow_mode": state.get("workflow_mode"),
        "total_cases_found": len(cases_table),
        "total_analyzed": len(analyzed),
        "total_searches": len(search_terms),
        "rounds": len(iteration_log),
        "keyword_searches": sum(1 for s in search_terms if s.get("type") == "keyword"),
        "semantic_searches": sum(1 for s in search_terms if s.get("type") == "semantic"),
        "citing_searches": sum(1 for s in search_terms if s.get("type") == "citing"),
        "relevance_distribution": rel_dist,
        "position_distribution": pos_dist,
        "pending_leads": len(pending_leads),
        "pivotal_cases": pivotal_cases
    }
    print(json.dumps(result, indent=2))


def cmd_should_refine(state, args):
    """Decide whether to perform additional search rounds.

    Auto-refine if:
    - depth_preference is "deep"
    - Fewer than 3 cases with relevance >= 4
    - More than 3 unexplored citation leads from analyzed cases

    Auto-skip if:
    - depth_preference is "quick"
    - 3+ cases with relevance >= 4 AND fewer than 3 unexplored leads
    """
    depth_pref = state.get("parsed_query", {}).get("depth_preference", "unspecified")

    if depth_pref == "quick":
        print(json.dumps({
            "decision": "skip",
            "reason": "Quick mode requested by user",
            "stats": {}
        }))
        return

    if depth_pref == "deep":
        print(json.dumps({
            "decision": "refine",
            "reason": "Deep research mode requested by user",
            "stats": {}
        }))
        return

    # Automatic decision for "unspecified"
    analyzed = state.get("analyzed_cases", [])
    query_type = state.get("parsed_query", {}).get("query_type", "mixed")
    high_rel_count = sum(1 for c in analyzed if c.get("relevance_ranking", 0) >= 4)
    unexplored_count = _count_unexplored_citations(state)

    reasons = []
    should_refine = False

    if high_rel_count < 3:
        reasons.append(f"Only {high_rel_count} cases with relevance >= 4 (threshold: 3)")
        should_refine = True

    if unexplored_count > 3:
        reasons.append(f"{unexplored_count} unexplored citation leads from analyzed cases (threshold: 3)")
        should_refine = True

    # Third condition: low factual matches for fact/mixed queries
    if query_type in ("fact", "mixed"):
        factual_match_count = sum(1 for c in analyzed if c.get("relevance_ranking", 0) >= 3)
        if factual_match_count < 3:
            reasons.append(
                f"Fewer than 3 cases with factual relevance >= 3 — analogous expansion needed"
            )
            should_refine = True

    if not should_refine:
        reasons.append(f"{high_rel_count} cases with relevance >= 4")
        reasons.append(f"Only {unexplored_count} unexplored leads remaining")

    decision = "refine" if should_refine else "skip"

    stats = {
        "high_relevance_count": high_rel_count,
        "unexplored_leads": unexplored_count,
        "total_analyzed": len(analyzed),
        "total_cases_found": len(state.get("cases_table", []))
    }
    if query_type in ("fact", "mixed"):
        stats["factual_match_count"] = sum(1 for c in analyzed if c.get("relevance_ranking", 0) >= 3)

    print(json.dumps({
        "decision": decision,
        "reason": "; ".join(reasons),
        "stats": stats
    }))


def cmd_add_leads(state, args):
    """Add new search term leads to pending_leads."""
    with open(args.json_file, "r", encoding="utf-8") as f:
        leads = json.load(f)

    if isinstance(leads, dict):
        leads = [leads]

    for lead in leads:
        state.setdefault("pending_leads", []).append(lead)

    print(json.dumps({"leads_added": len(leads), "total_pending": len(state.get("pending_leads", []))}))


def cmd_mark_explored(state, args):
    """Mark terms or cluster_ids as explored."""
    with open(args.json_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    terms = data.get("terms", [])
    cluster_ids = data.get("cluster_ids", [])

    explored_terms = state.setdefault("explored_terms", [])
    existing_terms_lower = {t.lower() for t in explored_terms}
    for t in terms:
        if t.lower() not in existing_terms_lower:
            explored_terms.append(t)
            existing_terms_lower.add(t.lower())

    explored_ids = state.setdefault("explored_cluster_ids", [])
    existing_ids = set(explored_ids)
    for cid in cluster_ids:
        if cid not in existing_ids:
            explored_ids.append(cid)
            existing_ids.add(cid)

    print(json.dumps({"terms_marked": len(terms), "cluster_ids_marked": len(cluster_ids)}))


def cmd_resolve_citations(state, args):
    """Replace [C1][C2] identifier runs in summary_answer_raw with Bluebook citations.

    Produces HTML-safe output: the raw answer text is HTML-escaped, and citation
    strings use <em> tags (not markdown underscores) so they render correctly in email.
    """
    import re
    import html as html_mod

    raw = state.get("summary_answer_raw", "")
    mapping = state.get("summary_answer_map", {})
    if not raw or not mapping:
        print(json.dumps({"error": "missing summary_answer_raw or summary_answer_map"}))
        return

    # Build URL lookup from analyzed_cases and cases_table
    url_map = {}
    for c in state.get("analyzed_cases", []):
        cid = c.get("cluster_id")
        url = c.get("url", "")
        if cid and url:
            url_map[cid] = url
    for c in state.get("cases_table", []):
        cid = c.get("cluster_id")
        url = c.get("url", "")
        if cid and url and cid not in url_map:
            url_map[cid] = url

    def citation_to_html(bc):
        """Convert a Bluebook citation string to HTML, replacing _text_ with <em>text</em>."""
        escaped = html_mod.escape(bc)
        return re.sub(r'_([^_]+)_', r'<em>\1</em>', escaped)

    def replace_run(m):
        ids = re.findall(r'C(\d+)', m.group(0))
        parts = []
        for n in ids:
            key = f'C{n}'
            if key not in mapping:
                continue
            entry = mapping[key]
            cite_html = citation_to_html(entry["bluebook_citation"])
            cid = entry.get("cluster_id")
            url = url_map.get(cid, "")
            if url:
                cite_html = f'<a href="{html_mod.escape(url)}">{cite_html}</a>'
            parts.append(cite_html)
        return "; ".join(parts) if parts else html_mod.escape(m.group(0))

    # HTML-escape the prose first, then substitute citation placeholders with HTML citations.
    # [, ], C, and digits are not HTML-special, so the regex still matches after escaping.
    escaped_raw = html_mod.escape(raw)
    resolved = re.sub(r'(?:\[C\d+\])+', replace_run, escaped_raw)
    state["summary_answer"] = resolved
    print(json.dumps({"ok": True, "length": len(resolved)}))


def cmd_check_diminishing_returns(state, args):
    """Check if the latest search round returned mostly already-analyzed cases.

    Returns JSON with decision ('stop' or 'continue'), overlap percentage,
    and counts of new vs. already-analyzed cases in the round.
    """
    round_num = args.round
    analyzed_ids = {c.get("cluster_id") for c in state.get("analyzed_cases", [])}

    # Find cases added in round N from search_results_raw
    round_cases = []
    for raw in state.get("search_results_raw", []):
        if raw.get("round") == round_num:
            round_cases.extend(raw.get("cases", []))

    if not round_cases:
        print(json.dumps({
            "decision": "continue",
            "reason": f"No data for round {round_num}",
            "overlap_pct": 0,
            "new_cases_in_round": 0,
            "already_analyzed": 0
        }))
        return

    round_ids = {c.get("cluster_id") for c in round_cases if c.get("cluster_id")}
    overlap = round_ids & analyzed_ids
    overlap_pct = len(overlap) / len(round_ids) if round_ids else 0

    decision = "stop" if overlap_pct >= 0.60 else "continue"
    print(json.dumps({
        "decision": decision,
        "overlap_pct": round(overlap_pct, 2),
        "new_cases_in_round": len(round_ids),
        "already_analyzed": len(overlap),
        "reason": f"{int(overlap_pct * 100)}% of round {round_num} results already analyzed"
    }))


def cmd_add_subsequent_history(state, args):
    """Store flagged subsequent history results in state.

    Input JSON: array of history-checker results (only flagged cases).
    Stores in state["subsequent_history"] dict keyed by string cluster_id.
    """
    with open(args.json_file, "r", encoding="utf-8") as f:
        flagged = json.load(f)

    if isinstance(flagged, dict):
        flagged = [flagged]

    total_checked = args.cases_checked if args.cases_checked else 0
    history = state.setdefault("subsequent_history", {})

    added = 0
    for entry in flagged:
        cid = entry.get("cluster_id")
        if not cid:
            continue
        cid_str = str(cid)
        history[cid_str] = {
            "precedential_status": entry.get("precedential_status", ""),
            "detail": entry.get("detail", ""),
            "confidence": entry.get("confidence", "uncertain"),
            "reversing_case": entry.get("reversing_case"),
        }
        added += 1

    result = {
        "cases_checked": total_checked,
        "flagged": added,
    }
    print(json.dumps(result))


def main():
    parser = argparse.ArgumentParser(description="Legal research state management")
    parser.add_argument("--state", required=True, help="Path to state JSON file")
    subparsers = parser.add_subparsers(dest="command")

    p_add_s = subparsers.add_parser("add-searches")
    p_add_s.add_argument("json_file")
    p_add_s.add_argument("--round", type=int, default=0)

    p_add_a = subparsers.add_parser("add-analysis")
    p_add_a.add_argument("json_file")

    subparsers.add_parser("get-leads")

    p_top = subparsers.add_parser("top-candidates")
    p_top.add_argument("n", type=int)

    subparsers.add_parser("summary")
    subparsers.add_parser("should-refine")

    p_leads = subparsers.add_parser("add-leads")
    p_leads.add_argument("json_file")

    p_mark = subparsers.add_parser("mark-explored")
    p_mark.add_argument("json_file")

    p_dr = subparsers.add_parser("check-diminishing-returns")
    p_dr.add_argument("--round", type=int, required=True, help="Round number to check")

    subparsers.add_parser("resolve-citations")

    p_hist = subparsers.add_parser("add-subsequent-history")
    p_hist.add_argument("json_file")
    p_hist.add_argument("--cases-checked", type=int, default=0,
                        help="Total number of analyzed cases checked")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    state_path = args.state
    state = load_state(state_path)

    cmd_map = {
        "add-searches": cmd_add_searches,
        "add-analysis": cmd_add_analysis,
        "get-leads": cmd_get_leads,
        "top-candidates": cmd_top_candidates,
        "summary": cmd_summary,
        "should-refine": cmd_should_refine,
        "add-leads": cmd_add_leads,
        "mark-explored": cmd_mark_explored,
        "check-diminishing-returns": cmd_check_diminishing_returns,
        "resolve-citations": cmd_resolve_citations,
        "add-subsequent-history": cmd_add_subsequent_history,
    }

    handler = cmd_map[args.command]
    handler(state, args)

    # Save state back (all commands may modify state)
    save_state(state_path, state)


if __name__ == "__main__":
    main()
