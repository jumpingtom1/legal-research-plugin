#!/usr/bin/env python3
"""
Generate HTML research report from a legal research state file.
Reads research-{id}-state.json, outputs research-{id}-results.html.
All case data is rendered verbatim from the state file — no interpretation.
"""

import sys
import json
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
      border-left: 4px solid #c0392b;
      margin: 1em 0;
      padding: 0.5em 1em;
      background: #fdf2f2;
      font-style: italic;
    }
    .about-report {
      background: #fff8e1;
      border: 1px solid #ffcc02;
      border-left: 5px solid #f0a500;
      padding: 1.2em;
      border-radius: 6px;
      margin-bottom: 2em;
      font-size: 0.92em;
      line-height: 1.6;
    }
    .about-report strong { color: #b8860b; }
    .about-report ul { margin: 0.5em 0; padding-left: 1.5em; }
    .index { background: #f8f9fa; border: 1px solid #dee2e6; padding: 1em 1.5em; border-radius: 6px; margin-bottom: 2em; }
    .index ol { margin: 0.3em 0; padding-left: 1.5em; }
    .index a { color: #2980b9; text-decoration: none; }
    .index a:hover { text-decoration: underline; }
    .query-display { background: #f5f5f5; padding: 1em; border-radius: 6px; margin: 1em 0; }
    .query-display code { background: #e8e8e8; padding: 2px 5px; border-radius: 3px; font-size: 0.9em; }
    .case-entry { margin-bottom: 2em; padding: 1em; border: 1px solid #e0e0e0; border-radius: 6px; background: #fafafa; }
    .case-entry a { color: #2c3e50; }
    .case-name { font-style: italic; font-weight: bold; }
    .relevance { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.85em; font-weight: bold; }
    .relevance-5 { background: #27ae60; color: white; }
    .relevance-4 { background: #2ecc71; color: white; }
    .relevance-3 { background: #f39c12; color: white; }
    .relevance-2 { background: #e67e22; color: white; }
    .relevance-1 { background: #e74c3c; color: white; }
    .holding { color: #27ae60; font-weight: bold; }
    .dicta { color: #e67e22; font-style: italic; }
    a { color: #2980b9; text-decoration: none; }
    a:hover { text-decoration: underline; }
    table { border-collapse: collapse; width: 100%; margin: 1em 0; font-size: 0.92em; }
    th, td { border: 1px solid #ddd; padding: 8px 12px; text-align: left; }
    th { background: #f5f5f5; font-weight: bold; }
    tr:nth-child(even) { background: #fafafa; }
    .meta { color: #666; font-size: 0.9em; margin-top: 3em; border-top: 1px solid #eee; padding-top: 1em; }
    .quote-verified::after { content: " [Verified]"; color: #27ae60; font-size: 0.8em; font-style: normal; }
    .quote-likely::after { content: " [Likely Match]"; color: #6b8e23; font-size: 0.8em; font-style: normal; }
    .quote-possible::after { content: " [Unverified]"; color: #e67e22; font-size: 0.8em; font-style: normal; font-weight: bold; }
    .quote-truncated::after { content: " [Unverified — opinion truncated]"; color: #888; font-size: 0.8em; font-style: normal; }
    .quote-not-found::after { content: " [UNVERIFIED]"; color: #e74c3c; font-size: 0.8em; font-style: normal; font-weight: bold; }
    .context-match { display: inline-block; padding: 2px 6px; border-radius: 4px; font-size: 0.8em; font-weight: bold; margin-left: 4px; }
    .context-match-full { background: #27ae60; color: white; }
    .context-match-partial { background: #f39c12; color: white; }
    .context-match-absent { background: #e74c3c; color: white; }
    .facts-detail {
      margin-top: 1em;
      border: 1px solid #e0e0e0;
      border-radius: 4px;
      padding: 0.5em 1em;
      background: #f9f9f9;
      color: #555;
      font-size: 0.92em;
    }
    .facts-detail summary {
      cursor: pointer;
      font-weight: bold;
      color: #666;
      padding: 0.3em 0;
    }
    .facts-detail summary:hover { color: #333; }
    .facts-detail p { margin: 0.5em 0 0 0; }
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

    # analysis_notes → ranking_explanation
    if "ranking_explanation" not in case and "analysis_notes" in case:
        case["ranking_explanation"] = case["analysis_notes"]

    # relevance_summary fallback to ranking_explanation
    if "relevance_summary" not in case or not case.get("relevance_summary"):
        case["relevance_summary"] = case.get("ranking_explanation", "")

    # Ensure defaults for fields the templates read
    case.setdefault("bluebook_citation", "")
    case.setdefault("relevance_summary", "")
    case.setdefault("ranking_explanation", "")
    case.setdefault("key_excerpts", [])
    case.setdefault("issues_presented", [])
    case.setdefault("factual_background", "")
    case.setdefault("factual_outcome", "")
    case.setdefault("relevance_ranking", 0)
    case.setdefault("position", "neutral")
    case.setdefault("url", "")
    case.setdefault("case_name", "")
    case.setdefault("context_match", "n/a")

    return case


def normalize_state(state):
    """Normalize all analyzed cases in a state dict."""
    state["analyzed_cases"] = [normalize_case(c) for c in state.get("analyzed_cases", [])]
    return state


def section_about(state):
    search_id = state.get("search_id", "")
    workflow_mode = state.get("workflow_mode", "unspecified")
    quick_note = ""
    if workflow_mode == "quick":
        quick_note = (
            f'<p><strong>Quick mode:</strong> This report is based on initial search results only. '
            f'Iterative refinement (additional search rounds, citation tracing) was not performed. '
            f'Use <code>/legal-research:research-continue {e(search_id)} '
            f'"&lt;direction&gt;"</code> to expand this research.</p>'
        )
    return f"""<div class="about-report" id="about">
  <strong>About This Report</strong>
  <p>This legal research report was generated by an AI assistant (Claude) using the CourtListener case law database. It is a research aid, not legal advice.</p>
  <ul>
    <li><strong>Verify all propositions.</strong> Every statement and characterization of a case's holding, facts, or reasoning should be checked against the full text of the cited authority. AI-generated case summaries may contain errors, omissions, or mischaracterizations.</li>
    <li><strong>Shepardize all cases.</strong> Cases cited in this report have not been shepardized. Before relying on any authority, confirm it has not been reversed, overruled, or otherwise undermined by subsequent decisions.</li>
    <li><strong>Search coverage is limited.</strong> This report searched the CourtListener database, which may not include all relevant authorities. Unreported decisions, recent filings, and cases from some courts may be absent.</li>
    <li><strong>AI limitations.</strong> The AI may misidentify holdings vs. dicta, or draw incorrect connections between cases. All analysis should be independently verified.</li>
    <li><strong>Quotes validated.</strong> All material appearing in quotation marks in this report has been programmatically verified against the original court opinion text. Excerpts are labeled as Verified, Likely Match, or Unverified based on automated text matching. See individual quote annotations for details.</li>
  </ul>
  {quick_note}
</div>"""


def section_index():
    return """<nav class="index" id="index">
  <strong>Contents</strong>
  <ol start="0">
    <li><a href="#about">About This Report</a></li>
    <li><a href="#index">Index</a></li>
    <li><a href="#query">User Query &amp; Decomposed Queries</a></li>
    <li><a href="#summary">Summary Answer</a></li>
    <li><a href="#authorities">Authorities</a></li>
    <li><a href="#all-results">All Search Results</a></li>
    <li><a href="#search-process">Search Process</a></li>
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

    return f"""<h2 id="query">User Query &amp; Decomposed Queries</h2>
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


def section_summary(state):
    analyzed = state.get("analyzed_cases", [])
    query_type = state.get("parsed_query", {}).get("query_type", "mixed")
    iteration_log = state.get("iteration_log", [])
    rounds = len(iteration_log) if iteration_log else 1

    total = len(analyzed)
    supports = sum(1 for c in analyzed if c.get("position") == "supports")
    opposes = sum(1 for c in analyzed if c.get("position") == "opposes")
    neutral = sum(1 for c in analyzed if c.get("position") not in ("supports", "opposes"))
    high_rel = [c for c in analyzed if c.get("relevance_ranking", 0) >= 4]

    position_line = ""
    if query_type in ("law", "mixed"):
        position_line = f"<strong>{supports}</strong> supporting, <strong>{opposes}</strong> opposing, <strong>{neutral}</strong> neutral."
    else:
        position_line = f"<strong>{len(high_rel)}</strong> scored relevance 4 or higher for factual similarity."

    case_lines = []
    for c in sorted(high_rel, key=lambda x: x.get("relevance_ranking", 0), reverse=True):
        name = e(c.get("case_name", ""))
        cite = e(c.get("bluebook_citation", ""))
        rel = c.get("relevance_ranking", 0)
        explanation = e(c.get("relevance_summary", ""))
        # First sentence only
        first_sentence = explanation.split(". ")[0]
        if not first_sentence.endswith("."):
            first_sentence += "."
        case_lines.append(f"<li><em>{name}</em> ({cite}) — Relevance {rel}/5: {first_sentence}</li>")

    cases_html = "\n".join(case_lines) if case_lines else "<li>No cases scored relevance 4 or higher.</li>"

    # Prose summary answer (composed by orchestrator LLM, stored in state)
    summary_answer = state.get("summary_answer", "")
    prose_html = ""
    if summary_answer:
        prose_html = f"<p>{e(summary_answer)}</p>"

    return f"""<h2 id="summary">Summary Answer</h2>
{prose_html}
<p><strong>{total}</strong> cases analyzed in depth across <strong>{rounds}</strong> search round(s). {position_line}</p>
<ul>
{cases_html}
</ul>"""


def _render_excerpts(excerpts):
    """Render a list of excerpt dicts as HTML blockquotes."""
    parts = []
    for ex in excerpts:
        text = e(ex.get("text", ""))
        ctx = e(ex.get("context", ""))
        parts.append(f"<blockquote>{text}<br><small>{ctx}</small></blockquote>")
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
            hod_class = "holding" if hod == "holding" else "dicta"
            hod_label = e(hod)
            parts.append(
                f"<p><strong>Issue:</strong> {issue_text}</p>\n"
                f'<p><strong>Resolution:</strong> {resolution} '
                f'<span class="{hod_class}">({hod_label})</span></p>'
            )
    return "\n".join(parts)


def _render_context_match_badge(context_match):
    """Render a context_match badge or empty string."""
    if context_match == "full":
        return '<span class="context-match context-match-full">Context: Match</span>'
    elif context_match == "partial":
        return '<span class="context-match context-match-partial">Context: Partial</span>'
    elif context_match == "absent":
        return '<span class="context-match context-match-absent">Context: Mismatch</span>'
    return ""


def render_case_entry(case, query_type):
    """Render a single case entry.

    Order: case name → citation · relevance badge [· context badge] → URL →
           Why relevant → Issue/Resolution pairs (all query types) →
           Key quotes → [fact/mixed only: collapsible Factual Background details]
    """
    name = e(case.get("case_name", ""))
    cite = e(case.get("bluebook_citation", ""))
    rel = case.get("relevance_ranking", 0)
    url = e(case.get("url", ""))
    relevance_summary = e(case.get("relevance_summary", ""))
    context_match = case.get("context_match", "n/a")

    context_badge = _render_context_match_badge(context_match)

    # Issues/resolution — rendered for all query types
    issues_html = _render_issues(case.get("issues_presented", []))

    # Key quotes — always rendered
    excerpts_html = _render_excerpts(case.get("key_excerpts", []))

    # Collapsible factual background — fact and mixed only
    facts_detail_html = ""
    if query_type in ("fact", "mixed"):
        facts = e(case.get("factual_background", ""))
        outcome = e(case.get("factual_outcome", ""))
        outcome_line = f"<p><strong>Outcome:</strong> {outcome}</p>" if outcome else ""
        if facts or outcome:
            facts_detail_html = (
                f'<details class="facts-detail">'
                f"<summary>Factual Background</summary>"
                f"<p>{facts}</p>"
                f"{outcome_line}"
                f"</details>"
            )

    return f"""<div class="case-entry">
  <h3><span class="case-name">{name}</span></h3>
  <p>{cite} &middot; <span class="relevance relevance-{rel}">Relevance: {rel}/5</span>{context_badge}</p>
  <p><a href="{url}">{url}</a></p>
  <p><strong>Why relevant:</strong> {relevance_summary}</p>
  {issues_html}
  {excerpts_html}
  {facts_detail_html}
</div>"""


def section_authorities(state):
    analyzed = state.get("analyzed_cases", [])
    query_type = state.get("parsed_query", {}).get("query_type", "mixed")

    if not analyzed:
        return '<h2 id="authorities">Authorities</h2>\n<p>No cases were deeply analyzed.</p>'

    def by_relevance(cases):
        return sorted(cases, key=lambda x: x.get("relevance_ranking", 0), reverse=True)

    def render(case):
        return render_case_entry(case, query_type)

    if query_type == "fact":
        cases_html = "\n".join(render(c) for c in by_relevance(analyzed))
        return f'<h2 id="authorities">Authorities</h2>\n{cases_html}'

    # law or mixed: group by position
    supports = by_relevance([c for c in analyzed if c.get("position") == "supports"])
    opposes = by_relevance([c for c in analyzed if c.get("position") == "opposes"])
    neutral = by_relevance([c for c in analyzed if c.get("position") not in ("supports", "opposes")])

    parts = ['<h2 id="authorities">Authorities</h2>']

    parts.append("<h3>Supporting Authorities</h3>")
    if supports:
        parts.extend(render(c) for c in supports)
    else:
        parts.append("<p>No supporting authorities identified in this research.</p>")

    parts.append("<h3>Opposing or Distinguishable Authorities</h3>")
    if opposes:
        parts.extend(render(c) for c in opposes)
    else:
        parts.append("<p>No opposing authorities identified in this research.</p>")

    if neutral:
        parts.append("<h3>Other Relevant Authorities</h3>")
        parts.extend(render(c) for c in neutral)

    return "\n".join(parts)


def section_all_results(state):
    cases_table = state.get("cases_table", [])
    analyzed_ids = {c.get("cluster_id") for c in state.get("analyzed_cases", [])}
    analyzed_map = {c.get("cluster_id"): c for c in state.get("analyzed_cases", [])}

    total = len(cases_table)
    analyzed_count = sum(1 for c in cases_table if c.get("cluster_id") in analyzed_ids)

    # Sort by relevance desc then cite_count desc
    def sort_key(c):
        cid = c.get("cluster_id")
        if cid in analyzed_map:
            rel = analyzed_map[cid].get("relevance_ranking", 0)
        else:
            rel = c.get("initial_relevance", 0)
        return (rel, c.get("cite_count", 0))

    cases_sorted = sorted(cases_table, key=sort_key, reverse=True)

    rows = []
    for c in cases_sorted:
        cid = c.get("cluster_id")
        name = e(c.get("case_name", ""))
        is_analyzed = cid in analyzed_ids
        ac = analyzed_map.get(cid, {})

        cite = e(ac.get("bluebook_citation", c.get("bluebook_citation", "")))
        court = e(c.get("court", ""))
        date_filed = e(c.get("date_filed", ""))
        cite_count = c.get("cite_count", 0)

        if is_analyzed:
            rel = ac.get("relevance_ranking", 0)
            position = e(ac.get("position", ""))
        else:
            rel = c.get("initial_relevance", 0)
            position = "&mdash;"

        url = e(ac.get("url", c.get("url", "")))
        analyzed_str = "Yes" if is_analyzed else "No"

        rows.append(
            f"<tr>"
            f"<td><em>{name}</em></td>"
            f"<td>{cite}</td>"
            f"<td>{court}</td>"
            f"<td>{date_filed}</td>"
            f"<td>{cite_count}</td>"
            f'<td><span class="relevance relevance-{rel}">{rel}</span></td>'
            f"<td>{position}</td>"
            f"<td>{analyzed_str}</td>"
            f'<td><a href="{url}">View</a></td>'
            f"</tr>"
        )

    rows_html = "\n".join(rows)
    return f"""<h2 id="all-results">All Search Results</h2>
<p>{total} unique cases found across all search rounds. {analyzed_count} selected for deep analysis (marked below).</p>
<table>
  <thead>
    <tr>
      <th>Case Name</th><th>Citation</th><th>Court</th><th>Date</th>
      <th>Cited By</th><th>Relevance</th><th>Position</th><th>Analyzed</th><th>Link</th>
    </tr>
  </thead>
  <tbody>
{rows_html}
  </tbody>
</table>"""


def section_search_process(state):
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

    return f"""<h2 id="search-process">Search Process</h2>
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


def generate_report(state):
    state = normalize_state(state)
    search_id = state.get("search_id", "unknown")
    original_input = state.get("parsed_query", {}).get("original_input", "Legal Research")
    title = original_input[:80]
    today = date.today().isoformat()

    sections = [
        section_about(state),
        section_index(),
        section_query(state),
        section_summary(state),
        section_authorities(state),
        section_all_results(state),
        section_search_process(state),
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
  <p style="font-family: monospace; color: #666; font-size: 0.85em; margin-bottom: 0;">Search ID: <strong>{e(search_id)}</strong></p>
  <main>
{body}
  </main>
  <div class="meta">
    <p>Generated by Legal Research Plugin &middot; Search ID: {e(search_id)} &middot; {today}</p>
    <p>State file: research-{e(search_id)}-state.json</p>
  </div>
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
