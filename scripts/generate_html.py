#!/usr/bin/env python3
"""
Generate HTML research report from a legal research state file.
Reads research-{id}-state.json, outputs research-{id}-results.html.
All case data is rendered verbatim from the state file — no interpretation.
"""

import sys
import json
import re
import html as html_mod
from datetime import date

from state_io import load_state, normalize_excerpts

CSS = """
    body {
      font-family: Georgia, 'Times New Roman', serif;
      max-width: 900px;
      margin: 40px auto;
      padding: 0 20px;
      line-height: 1.7;
      color: #1a1a1a;
      background: #fff;
    }
    h1 { font-size: 1.8em; border-bottom: 2px solid #333; padding-bottom: 10px; }
    h2 { font-size: 1.4em; color: #333; margin-top: 2em; border-bottom: 1px solid #ccc; padding-bottom: 5px; }
    h3 { font-size: 1.15em; color: #444; margin-top: 1.5em; }
    blockquote {
      border-left: 3px solid #c0392b;
      margin: 1em 0;
      padding: 0.5em 1em;
      background: #fef9f9;
      font-style: italic;
    }
    .warnings {
      background: #fff8e1;
      border: 1px solid #ffcc02;
      border-left: 5px solid #f0a500;
      padding: 1.2em;
      margin-bottom: 2em;
      font-size: 0.92em;
      line-height: 1.6;
    }
    .warnings strong { color: #b8860b; }
    .warnings ul { margin: 0.5em 0; padding-left: 1.5em; }
    .contents { background: #f8f9fa; border: 1px solid #dee2e6; padding: 1em 1.5em; margin-bottom: 2em; }
    .contents ol { margin: 0.3em 0; padding-left: 1.5em; }
    .contents a { color: #2980b9; text-decoration: none; }
    .contents a:hover { text-decoration: underline; }
    .query-display { background: #f5f5f5; padding: 1em; margin: 1em 0; }
    .query-display code { background: #e8e8e8; padding: 2px 5px; font-size: 0.9em; }
    .case-name { font-style: italic; font-weight: bold; }
    .relevance { display: inline-block; padding: 2px 8px; font-size: 0.85em; font-weight: bold; }
    .relevance-5 { background: #27ae60; color: white; }
    .relevance-4 { background: #2ecc71; color: white; }
    .relevance-3 { background: #f39c12; color: white; }
    .relevance-2 { background: #e67e22; color: white; }
    .relevance-1 { background: #e74c3c; color: white; }
    .history-badge { display: inline-block; padding: 2px 8px; font-size: 0.82em; font-weight: bold; color: white; }
    .history-reversed, .history-overruled { background: #c0392b; }
    .history-vacated { background: #e74c3c; }
    .history-modified, .history-rehearing_granted, .history-superseded { background: #e67e22; }
    .history-review_pending { background: #f39c12; }
    a { color: #2980b9; text-decoration: none; }
    a:hover { text-decoration: underline; }
    table { border-collapse: collapse; width: 100%; margin: 1em 0; font-size: 0.92em; }
    th, td { border: 1px solid #ddd; padding: 8px 12px; text-align: left; }
    th { background: #f5f5f5; font-weight: bold; }
    tr:nth-child(even) { background: #fafafa; }
    .about-report { color: #666; font-size: 0.9em; margin-top: 3em; border-top: 1px solid #eee; padding-top: 1em; }
"""


def e(text):
    """HTML-escape a string."""
    if text is None:
        return ""
    return html_mod.escape(str(text))


def normalize_case(case):
    """Normalize field names from case-analyzer output to canonical schema.
    Agents sometimes drift from the spec — this maps common variants back."""

    # citation / bluebook_citation
    if "bluebook_citation" not in case and "citation" in case:
        case["bluebook_citation"] = case["citation"]

    # excerpts → key_excerpts, and normalize plain strings to {text, context} objects
    case["key_excerpts"] = normalize_excerpts(case)

    # holding / key_reasoning → issues_presented
    if "issues_presented" not in case or not case["issues_presented"]:
        issues = []
        holding = case.get("holding", "")
        reasoning = case.get("key_reasoning", "")
        if holding or reasoning:
            issues.append({
                "issue": holding if holding else "See key reasoning",
                "resolution": reasoning if reasoning else holding,
                "holding_or_dicta": "holding"
            })
        case["issues_presented"] = issues

    # Ensure defaults for fields the templates read
    case.setdefault("bluebook_citation", "")
    case.setdefault("relevance_summary", "")
    case.setdefault("key_excerpts", [])
    case.setdefault("issues_presented", [])
    case.setdefault("factual_background", "")
    case.setdefault("factual_outcome", "")
    case.setdefault("relevance_ranking", 0)
    case.setdefault("position", "neutral")
    case.setdefault("url", "")
    case.setdefault("case_name", "")

    return case


def normalize_state(state):
    """Normalize all analyzed cases in a state dict."""
    state["analyzed_cases"] = [normalize_case(c) for c in state.get("analyzed_cases", [])]
    return state


def section_warnings(state):
    request_id = state.get("request_id", "")
    workflow_mode = state.get("workflow_mode", "unspecified")
    quick_note = ""
    if workflow_mode == "quick":
        quick_note = (
            f'<p><strong>Quick mode:</strong> This report is based on initial search results only. '
            f'Iterative refinement (additional search rounds, citation tracing) was not performed. '
            f'Use <code>/legal-research:research-continue {e(request_id)} '
            f'"&lt;direction&gt;"</code> to expand this research.</p>'
        )
    has_hist_check = "subsequent_history" in state
    if has_hist_check:
        shep_bullet = (
            '<li><strong>Shepardize all cases.</strong> An automated subsequent-history check was performed '
            'using CourtListener citation data, but this is not a comprehensive citator review. '
            'Before relying on any authority, independently confirm it has not been reversed, overruled, '
            'or otherwise undermined by subsequent decisions.</li>'
        )
    else:
        shep_bullet = (
            '<li><strong>Shepardize all cases.</strong> Cases cited in this report have not been shepardized. '
            'Before relying on any authority, confirm it has not been reversed, overruled, '
            'or otherwise undermined by subsequent decisions.</li>'
        )

    return f"""<div class="warnings" id="warnings">
  <strong>Important Limitations</strong>
  <ul>
    <li><strong>Verify all propositions.</strong> Every statement and characterization of a case's holding, facts, or reasoning should be checked against the full text of the cited authority. AI-generated case summaries may contain errors, omissions, or mischaracterizations.</li>
    {shep_bullet}
    <li><strong>Search coverage is limited.</strong> This report searched the CourtListener database, which may not include all relevant authorities. Unreported decisions, recent filings, and cases from some courts may be absent.</li>
    <li><strong>AI limitations.</strong> The AI may misidentify holdings vs. dicta, or draw incorrect connections between cases. All analysis should be independently verified.</li>
  </ul>
  {quick_note}
</div>"""


def section_contents(state=None):
    hist_link = ""
    if state and state.get("subsequent_history"):
        hist_link = '\n    <li><a href="#subsequent-history">Subsequent History Flags</a></li>'
    return f"""<nav class="contents" id="contents">
  <strong>Contents</strong>
  <ol>
    <li><a href="#query">User Query</a></li>
    <li><a href="#short-answer">Short Answer</a></li>
    <li><a href="#leading-authorities">Leading Authorities</a></li>{hist_link}
    <li><a href="#all-results">All Results</a></li>
    <li><a href="#about-search">About this Search</a></li>
  </ol>
</nav>"""


def section_query(state):
    pq = state.get("parsed_query", {})
    original = e(pq.get("original_input", ""))
    query_type = e(pq.get("query_type", ""))
    legal_qs = pq.get("legal_questions", [])
    legal_qs_str = e("; ".join(legal_qs)) if legal_qs else "N/A"
    fact_pattern = e(pq.get("fact_pattern", "")) or "N/A"
    jurisdiction = e(pq.get("jurisdiction", "")) or "All jurisdictions"
    dr = pq.get("date_range", {})
    date_after = dr.get("after", "")
    date_before = dr.get("before", "")
    if date_after or date_before:
        date_str = f"{date_after or 'any'} to {date_before or 'present'}"
    else:
        date_str = "None specified"
    constraints = pq.get("constraints", [])
    constraints_str = e("; ".join(constraints)) if constraints else "None"
    workflow = e(state.get("workflow_mode", "unspecified"))

    return f"""<h2 id="query">1. User Query</h2>
<h3>Original Query</h3>
<div class="query-display"><p>{original}</p></div>
<h3>Decomposed Query</h3>
<table>
  <tbody>
    <tr><th>Query Type</th><td>{query_type}</td></tr>
    <tr><th>Legal Questions</th><td>{legal_qs_str}</td></tr>
    <tr><th>Fact Pattern</th><td>{fact_pattern}</td></tr>
    <tr><th>Jurisdiction</th><td>{jurisdiction}</td></tr>
    <tr><th>Date Range</th><td>{date_str}</td></tr>
    <tr><th>Constraints</th><td>{constraints_str}</td></tr>
    <tr><th>Depth</th><td>{workflow}</td></tr>
  </tbody>
</table>"""


def section_short_answer(state):
    analyzed = state.get("analyzed_cases", [])
    iteration_log = state.get("iteration_log", [])
    rounds = len(iteration_log) if iteration_log else 1
    total = len(analyzed)

    # Prose summary answer (composed by orchestrator LLM, stored in state)
    summary_answer = state.get("summary_answer", "")
    prose_html = ""
    if summary_answer:
        paragraphs = [p.strip() for p in summary_answer.split("\n\n") if p.strip()]
        # summary_answer is produced by resolve-citations and is already HTML-safe
        # (prose text is escaped; citations use <em> tags, not markdown underscores).
        prose_html = "\n".join(f"<p>{p}</p>" for p in paragraphs)

    return f"""<h2 id="short-answer">2. Short Answer</h2>
{prose_html}
<p><strong>{total}</strong> cases analyzed in depth across <strong>{rounds}</strong> search round(s).</p>"""


def _render_excerpts(excerpts):
    """Render a list of excerpt dicts as HTML blockquotes."""
    parts = []
    for ex in excerpts:
        text = e(ex.get("text", ""))
        parts.append(f"<blockquote>{text}</blockquote>")
    return "\n".join(parts)


def _render_issues(issues):
    """Render a list of issues_presented items as HTML. Items may be dicts or plain strings."""
    parts = []
    for iss in issues:
        if isinstance(iss, str):
            parts.append(f"<p>{e(iss)}</p>")
        else:
            issue_text = e(iss.get("issue", ""))
            resolution = e(iss.get("resolution", ""))
            hod = iss.get("holding_or_dicta", "")
            hod_label = "Holding" if hod == "holding" else "Dicta"
            parts.append(
                f"<p><strong>Issue:</strong> {issue_text}</p>\n"
                f"<p><strong>{hod_label}:</strong> {resolution}</p>"
            )
    return "\n".join(parts)


def _citation_without_name(bluebook_citation):
    """Strip leading _CaseName_, prefix from Bluebook citation, returning reporter portion.

    E.g. '_Smith v. Jones_, 500 F.3d 123 (9th Cir. 2020)' → '500 F.3d 123 (9th Cir. 2020)'
    """
    m = re.match(r'^_[^_]+_,?\s*', bluebook_citation)
    if m:
        return bluebook_citation[m.end():]
    return bluebook_citation


def render_authority_entry(case, history=None):
    """Render a single leading authority entry."""
    name = e(case.get("case_name", ""))
    reporter = e(_citation_without_name(case.get("bluebook_citation", "")))
    rel = case.get("relevance_ranking", 0)
    url = e(case.get("url", ""))
    relevance_summary = e(case.get("relevance_summary", ""))

    issues_html = _render_issues(case.get("issues_presented", [])[:1])
    key_excerpts = case.get("key_excerpts", [])
    excerpts_html = _render_excerpts(key_excerpts[:1])

    if url:
        name_html = f'<a href="{url}"><em><strong>{name}</strong></em></a>'
    else:
        name_html = f'<em><strong>{name}</strong></em>'

    # History badge (only if flagged)
    history_badge = ""
    history_warning = ""
    if history:
        status = history.get("precedential_status", "")
        label = status.upper().replace("_", " ")
        confidence = history.get("confidence", "")
        detail = e(history.get("detail", ""))
        conf_note = f" ({confidence} confidence)" if confidence else ""
        history_badge = f' <span class="history-badge history-{e(status)}">{label}</span>'
        history_warning = (
            f'\n  <p style="color: #c0392b; font-weight: bold;">'
            f'Subsequent History: {detail}{conf_note}</p>'
        )

    reporter_sep = f", {reporter}" if reporter else ""
    return f"""<div style="margin-bottom: 2em; padding: 1em; border: 1px solid #e0e0e0; background: #fafafa;">
  <p>{name_html}{reporter_sep} &mdash; <span class="relevance relevance-{rel}">Relevance: {rel}/5</span>{history_badge}</p>{history_warning}
  <p><strong>Why Relevant:</strong> {relevance_summary}</p>
  {issues_html}
  {excerpts_html}
</div>"""


def section_leading_authorities(state):
    analyzed = state.get("analyzed_cases", [])

    if not analyzed:
        return '<h2 id="leading-authorities">3. Leading Authorities</h2>\n<p>No cases were deeply analyzed.</p>'

    sorted_cases = sorted(analyzed, key=lambda x: x.get("relevance_ranking", 0), reverse=True)
    top_cases = sorted_cases[:10]

    hist = state.get("subsequent_history", {})
    entries_html = "\n".join(
        render_authority_entry(c, hist.get(str(c.get("cluster_id"))))
        for c in top_cases
    )
    return f'<h2 id="leading-authorities">3. Leading Authorities</h2>\n{entries_html}'


def section_subsequent_history_flags(state):
    """Render a table of cases flagged with negative subsequent treatment.
    Returns empty string if no flags found."""
    hist = state.get("subsequent_history", {})
    if not hist:
        return ""

    # Build a name lookup from analyzed_cases and cases_table
    name_map = {}
    for c in state.get("analyzed_cases", []):
        name_map[str(c.get("cluster_id"))] = c.get("case_name", "Unknown")
    for c in state.get("cases_table", []):
        cid_str = str(c.get("cluster_id"))
        if cid_str not in name_map:
            name_map[cid_str] = c.get("case_name", "Unknown")

    rows = []
    for cid_str, entry in hist.items():
        name = e(name_map.get(cid_str, f"Cluster {cid_str}"))
        status = entry.get("precedential_status", "")
        label = status.upper().replace("_", " ")
        detail = e(entry.get("detail", ""))
        confidence = e(entry.get("confidence", ""))
        rows.append(
            f"<tr>"
            f"<td><em>{name}</em></td>"
            f'<td><span class="history-badge history-{e(status)}">{label}</span></td>'
            f"<td>{detail}</td>"
            f"<td>{confidence}</td>"
            f"</tr>"
        )

    rows_html = "\n".join(rows)
    return f"""<h2 id="subsequent-history">Subsequent History Flags</h2>
<p style="font-size: 0.9em; color: #666;"><em>The following cases were flagged by an automated subsequent-history check. This is not a substitute for comprehensive Shepard&#39;s/KeyCite verification.</em></p>
<table>
  <thead>
    <tr><th>Case</th><th>Status</th><th>Detail</th><th>Confidence</th></tr>
  </thead>
  <tbody>
{rows_html}
  </tbody>
</table>"""


def section_all_results(state):
    cases_table = state.get("cases_table", [])
    analyzed_map = {c.get("cluster_id"): c for c in state.get("analyzed_cases", [])}
    hist = state.get("subsequent_history", {})
    has_flags = bool(hist)

    # Sort by final score descending
    def sort_key(c):
        cid = c.get("cluster_id")
        if cid in analyzed_map:
            return analyzed_map[cid].get("relevance_ranking", 0)
        return c.get("initial_relevance", 0)

    cases_sorted = sorted(cases_table, key=sort_key, reverse=True)

    rows = []
    for i, c in enumerate(cases_sorted, 1):
        cid = c.get("cluster_id")
        ac = analyzed_map.get(cid, {})

        name = e(c.get("case_name", ""))
        raw_cite = ac.get("bluebook_citation", c.get("bluebook_citation", ""))
        reporter = e(_citation_without_name(raw_cite))
        url = e(c.get("url", ""))
        relevance_note = e(c.get("relevance_note", "") or ac.get("relevance_summary", ""))
        score = c.get("initial_relevance", 0)

        reporter_sep = f", {reporter}" if reporter else ""
        if url:
            case_cell = f'<td><a href="{url}"><em>{name}</em>{reporter_sep}</a></td>'
        else:
            case_cell = f'<td><em>{name}</em>{reporter_sep}</td>'

        flag_cell = ""
        if has_flags:
            h = hist.get(str(cid))
            if h:
                status = h.get("precedential_status", "")
                label = status.upper().replace("_", " ")
                flag_cell = f'<td><span class="history-badge history-{e(status)}">{label}</span></td>'
            else:
                flag_cell = "<td></td>"

        rows.append(
            f"<tr>"
            f"<td>{i}</td>"
            f"{case_cell}"
            f"<td>{relevance_note}</td>"
            f'<td><span class="relevance relevance-{score}">{score}/5</span></td>'
            f"{flag_cell}"
            f"</tr>"
        )

    rows_html = "\n".join(rows)
    total = len(cases_table)
    flags_header = "<th>Flags</th>" if has_flags else ""
    return f"""<h2 id="all-results">4. All Results</h2>
<table>
  <thead>
    <tr>
      <th>#</th><th>Case</th><th>Relevance Note</th><th>Score</th>{flags_header}
    </tr>
  </thead>
  <tbody>
{rows_html}
  </tbody>
</table>
<p style="font-size: 0.9em; color: #666;">{total} unique cases found across all search rounds.</p>"""


def section_about_search(state):
    search_terms = state.get("search_terms_table", [])
    strategies = state.get("search_strategies", [])
    iteration_log = state.get("iteration_log", [])
    rounds = len(iteration_log) if iteration_log else 1

    keyword_count = sum(1 for s in search_terms if s.get("type") == "keyword")
    semantic_count = sum(1 for s in search_terms if s.get("type") == "semantic")
    citing_count = sum(1 for s in search_terms if s.get("type") == "citing")
    total_searches = len(search_terms)

    # Strategies table
    strat_rows = []
    for i, st in enumerate(strategies, 1):
        desc = e(st.get("description", ""))
        kw = e("; ".join(st.get("keyword_queries", [])))
        sem = e("; ".join(st.get("semantic_queries", [])))
        court = e(st.get("court_filter", ""))
        rationale = e(st.get("rationale", ""))
        strat_rows.append(
            f"<tr><td>{i}</td><td>{desc}</td><td>{kw}</td>"
            f"<td>{sem}</td><td>{court}</td><td>{rationale}</td></tr>"
        )
    strat_html = "\n".join(strat_rows)

    # Searches executed table
    search_rows = []
    for st in search_terms:
        rnd = e(str(st.get("round", 1)))
        strategy = e(st.get("strategy", ""))
        stype = e(st.get("type", ""))
        query = e(st.get("query", ""))
        court = e(st.get("court_filter", "")) or "&mdash;"
        date_filter = e(st.get("date_filter", "")) or "&mdash;"
        results = st.get("result_count", 0)
        search_rows.append(
            f"<tr><td>{rnd}</td><td>{strategy}</td><td>{stype}</td>"
            f"<td>{query}</td><td>{court}</td><td>{date_filter}</td><td>{results}</td></tr>"
        )
    searches_html = "\n".join(search_rows)

    num_strategies = len(strategies)

    return f"""<h2 id="about-search">5. About this Search</h2>
<h3>Search Strategies</h3>
<p>The query analyst generated {num_strategies} search strategies:</p>
<table>
  <thead><tr><th>#</th><th>Description</th><th>Keyword Queries</th><th>Semantic Queries</th><th>Court Filter</th><th>Rationale</th></tr></thead>
  <tbody>
{strat_html}
  </tbody>
</table>
<h3>Searches Executed</h3>
<p><strong>{total_searches}</strong> total searches executed across <strong>{rounds}</strong> round(s): <strong>{keyword_count}</strong> keyword, <strong>{semantic_count}</strong> semantic, <strong>{citing_count}</strong> citing.</p>
<table>
  <thead>
    <tr><th>Round</th><th>Strategy</th><th>Type</th><th>Query Text</th><th>Court Filter</th><th>Date Filter</th><th>Results</th></tr>
  </thead>
  <tbody>
{searches_html}
  </tbody>
</table>"""


def section_about_report(state):
    request_id = state.get("request_id", "unknown")
    today = date.today().isoformat()
    state_file = f"research-{request_id}-state.json"
    return f"""<div class="about-report">
  <p>Request ID: {e(request_id)}</p>
  <p>Generated by Legal Research Plugin using Claude + CourtListener &middot; {today}</p>
  <p>State file: {e(state_file)}</p>
</div>"""


def generate_report(state):
    state = normalize_state(state)
    original_input = state.get("parsed_query", {}).get("original_input", "Legal Research")
    title = original_input[:80]

    sections = [
        section_warnings(state),
        section_contents(state),
        section_query(state),
        section_short_answer(state),
        section_leading_authorities(state),
        section_subsequent_history_flags(state),
        section_all_results(state),
        section_about_search(state),
        section_about_report(state),
    ]

    body = "\n\n".join(sections)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{e(title)}</title>
  <style>{CSS}</style>
</head>
<body>
  <main>
{body}
  </main>
</body>
</html>"""


def main():
    if len(sys.argv) < 2:
        print("Usage: generate_html.py <state_file> [output_file]", file=sys.stderr)
        sys.exit(1)

    state_path = sys.argv[1]
    state = load_state(state_path)

    if len(sys.argv) >= 3:
        output_path = sys.argv[2]
    else:
        output_path = state_path.replace("-state.json", "-results.html")

    html_content = generate_report(state)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    analyzed_count = len(state.get("analyzed_cases", []))
    cases_count = len(state.get("cases_table", []))
    searches_count = len(state.get("search_terms_table", []))
    print(f"Report: {output_path}")
    print(f"  {cases_count} cases in results table, {analyzed_count} deeply analyzed, {searches_count} searches documented")


if __name__ == "__main__":
    main()
