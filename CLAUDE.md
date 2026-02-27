# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a **Claude Code plugin** (`legal-research`) that orchestrates iterative legal research workflows using the CourtListener case law database via MCP. It is not a traditional software project — there is no build system, package manager, or compiled code. The plugin consists of markdown specification files that define commands, agents, and skills, plus Python scripts that handle mechanical tasks (HTML generation, state management, quote validation).

**Bundled MCP**: The CourtListener MCP server is included in `mcp-server/` and registered automatically via `.mcp.json`. No external MCP setup required — only `COURTLISTENER_API_TOKEN` must be set in the environment.

## Setup (New Users)

Before using this plugin, you need a free CourtListener API token:

1. Register at [courtlistener.com](https://www.courtlistener.com/sign-in/) and generate an API token under your account settings.
2. Export the token in your shell config (`~/.bashrc`, `~/.zshrc`, etc.):
   ```bash
   export COURTLISTENER_API_TOKEN="your-token-here"
   ```
3. Reload your shell (`source ~/.bashrc`) or open a new terminal, then launch Claude Code.

If the token is missing or invalid, MCP tool calls will return an error message rather than results. Verify it is set with `echo $COURTLISTENER_API_TOKEN` before starting a session.

## Usage

- `/legal-research:research "<legal question>"` — Start a new multi-phase research session
- `/legal-research:research-continue "<request_id> <refinement direction>"` — Continue/refine an existing session
- `/legal-research:research-email` — Non-interactive entry point triggered by gmail-monitor (reads REQUEST_ID from env)

## Architecture

### Orchestrator + Specialized Agents + Scripts

The `commands/research.md` orchestrator drives a workflow, delegating to specialized agents for LLM tasks and Python scripts for mechanical tasks:

1. **Phase 0 — Query Decomposition**: Parse user input into structured elements (legal_questions, fact_pattern, jurisdiction, query_type)
2. **Phase 1 — Query Analysis**: `agents/query-analyst.md` (runs on haiku) generates 4-6 search strategies
3. **Phase 2 — Parallel Search**: `scripts/search_api.py` runs per strategy (direct HTTP, parallel Bash `&`+`wait`); ONE `agents/case-scorer.md` (haiku, no tools) scores all results; scores merged via inline Python
4. **Phase 3 — Deep Case Analysis**: `scripts/fetch_case_text.py` pre-fetches opinion text per cluster_id AND `scripts/check_subsequent_history.py` runs history queries — both in the same parallel Bash `&`+`wait` block; 8-12 `agents/case-analyzer.md` launched in parallel reading pre-fetched files (one case per agent)
5. **Phase 3.5 — Automatic Depth Decision**: `scripts/manage_state.py should-refine` decides whether to iterate based on result quality and unexplored leads — no user checkpoint
6. **Phase 4 — Iterative Refinement** (if triggered): Additional search rounds using new terminology and citation tracing, driven by `manage_state.py get-leads`; fetch and history queries again run in parallel for new cases
7. **Phase 4.5 — Subsequent History Evaluation**: Checks for any analyzed cases not yet history-checked (stragglers from Phase 4 additions), runs remaining queries, then `agents/history-checker.md` (haiku, no tools) evaluates all citing-case data for negative treatment (reversal, overruling, vacatur); flagged cases stored in `state["subsequent_history"]`
8. **Phase 5 — Output**: `agents/answer-writer.md` composes summary answer with per-sentence `[C1]` citations; `manage_state.py resolve-citations` resolves identifiers to Bluebook text; `scripts/generate_html.py` assembles HTML; `scripts/run_quote_validation.py` verifies quotations

### Key Design Principles

- **State file (`research-{request_id}-state.json`) is the single source of truth** — context is ephemeral, all results persist in this file. `request_id` format: `REQ-YYYYMMDD-HHMMSS-XXXX`.
- **Scripts handle mechanical work** — HTML generation, state management, deduplication, quote validation are all deterministic Python, never LLM-composed
- **Scripts for API calls, agents for analysis**: `search_api.py` and `fetch_case_text.py` make all CourtListener HTTP calls — MCP tools in `allowed-tools` are orchestrator-only (`lookup_citation`, `find_citing_cases`). `case-analyzer` has `tools: Read` only.
- **All case additions go through `manage_state.py`** — `add-searches` for search_api.py results, `add-mcp-cases` for MCP tool results. Never add cases to `cases_table` via inline Python.
- **No synthesizer agent** — the HTML report is assembled by `generate_html.py` directly from case-analyzer outputs
- **Verbatim rendering** — case data fields are rendered exactly as returned by analyzers, never re-interpreted
- **Automatic depth decisions** — the system decides whether to refine based on result quality (< 3 high-relevance cases) and lead potential (> 3 unexplored citations), not user input
- **Query type (`fact`/`law`/`mixed`) drives everything** — search strategy, analysis depth, and output format all adapt
- **Session ID asymmetry**: In interactive commands, `{request_id}` is a template placeholder filled with the generated REQ-... string. In `research-email.md`, `${REQUEST_ID}` is a bash env var used directly — there is no `{request_id}` placeholder in email mode.
- **Non-interactive commands**: Omit `AskUserQuestion` from `allowed-tools` frontmatter to enforce non-interactive mode
- **HTML output sequencing**: `run_quote_validation.py:190` derives HTML path by string-replacing `-state.json` → `-results.html` on the state path. Sequence must be: `generate_html.py` → `run_quote_validation.py --annotate` (in-place) → `cp` to delivery path. Never copy before annotation.
- **HTML is email-targeted**: No `<details>` elements, no CSS `::after` pseudo-elements, no `border-radius` on containers. Maintain email compatibility when modifying `generate_html.py`.
- **Orchestrator sync**: `research.md` and `research-email.md` share identical Phase 2-5 logic (only Phase 0/E differs). Changes to search, analysis, scoring, refinement, or output phases must be applied to both files. The email variant uses `${REQUEST_ID}` (bash env var) where the interactive variant uses `{request_id}` (template placeholder).

### Agent Contracts

| Agent | Model | Input | Output |
|-------|-------|-------|--------|
| `query-analyst` | haiku | Structured parsed_query | Array of search strategies |
| `case-scorer` | haiku | Research question + cases list (cluster_id, name, court, date, cite_count, snippet) | JSON array → orchestrator writes to `/tmp/case_scores.json` |
| `case-analyzer` | inherit | cluster_id + url + case_name + date_filed (from fetch metadata) + parsed_query | Deep analysis adapted to query type; reads `/tmp/vq_opinion_{cluster_id}.txt` |
| `answer-writer` | inherit | `/tmp/answer_writer_input.json` (top-10 analyzed cases with `_id` + `case_map`) | Per-sentence prose with `[C1]` identifiers → `/tmp/answer_writer_output.txt` |
| `history-checker` | haiku | Batch of 4-5 cases with their `citing_cases` from `check_subsequent_history.py` | JSON array of flagged cases only (omits cases with no negative treatment) |

**`case-searcher.md`** — retired; no longer invoked by any command. Replaced by `search_api.py` + `case-scorer`.

**`case-analyzer.md` schema changes**: Two locations must stay in sync — the JSON example block (~line 57) and the CRITICAL field names list (~line 105). Also check `scripts/generate_html.py`'s `normalize_case()`, `render_authority_entry()`, and `_citation_without_name()` functions for field references, and scan inline text (e.g., absent-case notes) for field name cross-references.

**`generate_html.py` data sources**: `cases_table` entries (populated by `search_api.py`, scored by `case-scorer`) carry `initial_relevance` and `relevance_note`. `analyzed_cases` entries (from case-analyzer) carry `relevance_ranking` and `relevance_summary`. `section_all_results()` merges both via `analyzed_map = {c.get("cluster_id"): c ...}`. Score column uses `initial_relevance`; relevance note falls back from `relevance_note` → `relevance_summary`.

**`_citation_without_name()` helper**: Strips leading `_CaseName_,` prefix from `bluebook_citation` via regex `^_[^_]+_,?\s*`, returning just the reporter portion. Used by both `render_authority_entry()` and `section_all_results()` to avoid duplicating the case name (which is rendered separately with `<em>` styling and linked to CourtListener). The all-results table has a single "Case" column (not separate "Case Name" + "Citation" columns).

### Scripts

| Script | Purpose |
|--------|---------|
| `scripts/search_api.py` | Direct HTTP search against v4 API for one strategy; input: `/tmp/strategy_{id}.json`; output: JSON to stdout (no `initial_relevance`/`relevance_note`) |
| `scripts/fetch_case_text.py` | Pre-fetches opinion text for one cluster_id via v3 API; writes `/tmp/vq_opinion_{id}.txt` (≤300k chars); output: JSON to stdout |
| `scripts/check_subsequent_history.py` | Queries v4 API for citing cases that may represent negative subsequent treatment; two queries per case (negative keywords + all recent citers); output: JSON to stdout with deduplicated citing cases |
| `scripts/generate_html.py` | Reads state file, produces complete HTML report |
| `scripts/manage_state.py` | State file operations: add-searches, add-analysis, add-mcp-cases, add-subsequent-history, validate-scores, get-leads, top-candidates, summary, should-refine, mark-explored, resolve-citations |
| `scripts/log_session.py` | Session logging: `error`/`note` write to state; `ingest-search`/`ingest-fetch`/`ingest-hist` log structured batch events (timing, retries, counts) from script output files; `summary` assembles and appends one JSONL record including `api_performance` |
| `scripts/run_quote_validation.py` | Orchestrates quote validation: checks opinion files, runs matcher, annotates HTML |
| `scripts/vq_matcher.py` | Three-tier quote matching (normalized substring → token sequence → fuzzy sliding window with `quick_ratio()` pre-filter) |

**`quote_validation` state structure**: `run_quote_validation.py` writes `state["quote_validation"]["summary"]` (nested under a `"summary"` key), with fields: `total`, `verified`, `likely_match`, `possible_match`, `not_found`, `not_found_truncated`, `skipped`.

**`quote_validation` results and re-annotation**: Saved state results strip `excerpt_text` (too large). To re-run the annotator without re-running validation, reconstruct `excerpt_text` from `analyzed_cases` via `normalize_excerpts()`. Full re-validation: `run_quote_validation.py <state> --annotate` (requires opinion files in `/tmp/vq_opinion_{cid}.txt`).

**Script import convention**: All scripts that import `state_io` use `sys.path.insert(0, str(Path(__file__).parent))` at the top (before the import), so they work regardless of the calling directory.

**Script dependency convention**: Scripts in `scripts/` use Python stdlib only — no external packages. (`httpx` is available only inside `mcp-server/` via `uv`.) Use `urllib.request` for HTTP, not `httpx`.

**`search_api.py` v4 API quirk**: The `court` field in v4 search results is the full court name (e.g., "Court of Appeals for the Ninth Circuit"), not the court code ("ca9"). Bluebook citation is best-effort; case-analyzer produces the authoritative citation.

**`_api_get()` return signature**: In `search_api.py`, `fetch_case_text.py`, and `check_subsequent_history.py`, `_api_get()` returns a 3-tuple `(data, error, retry_count)`. All callers must unpack all three values. Maintain this signature if modifying these functions.

**`case-scorer` orchestrator pattern**: Agent has `tools: none` — it returns a JSON array as its response text. Orchestrator writes the output to `/tmp/case_scores.json`, then merges via inline Python. Merge step ignores any `cluster_id` not already in `cases_table` (guardrail against hallucination). **Score clamping**: The merge snippet clamps `initial_relevance` to 1-5 via `min(5, max(1, int(raw)))`.

**`add-mcp-cases` command**: Structured pathway for cases discovered via MCP tools (`lookup_citation`, `find_citing_cases`) during Phase 4 citation tracing. Usage: `manage_state.py --state <path> add-mcp-cases <json_file> --source <lookup_citation|find_citing_cases> --round <N>`. Sets `initial_relevance` to null (must be scored before analysis), `source_strategy` to the `--source` value. Rejects entries missing `cluster_id`. **All Phase 4 case additions MUST go through `add-mcp-cases` or `add-searches` — never via inline Python.**

**`add-mcp-cases` scoring interaction**: Cases added via `add-mcp-cases` have `initial_relevance: null`. The `top-candidates` sort treats null as 0, so unscored cases won't be selected for analysis until a case-scorer pass assigns scores. Always run case-scorer after `add-mcp-cases` before `top-candidates`.

**`validate-scores` command**: Defense-in-depth score validation. Usage: `manage_state.py --state <path> validate-scores`. Clamps all `initial_relevance` in `cases_table` and `relevance_ranking` in `analyzed_cases` to 1-5 range. Run after Phase 4 scoring. Reports all corrections made.

**`partial_results` state flag**: Boolean. Set to `true` when research is truncated due to API degradation. `generate_html.py` renders a warning banner at the top of the report. Orchestrators set this flag when 3+ consecutive MCP failures are detected during Phase 4.

**`session_log` state structure**: `{"started_at": "ISO-timestamp-or-null", "errors": [], "notes": [], "events": []}`. `started_at` is set on the first `error`, `note`, or `ingest-*` call. `events[]` stores structured batch performance records written by `ingest-search` and `ingest-fetch`. **Performance fields**: `search_api.py` outputs include `elapsed_ms`/`retries` per query and `total_elapsed_ms`/`total_retries` at top level; `fetch_case_text.py` outputs include `elapsed_ms`/`total_retries`. The `summary` JSONL record includes an `api_performance` section aggregated from events (total_api_ms, fetch_success_rate, total_retries, per-batch breakdown).

**Timestamp conventions**: `request_id` timestamps (e.g., `REQ-20260223-185552-...`) use UTC. `session_log` note/error timestamps use local time. Account for timezone offset when correlating run timing with file modification times.

**`summary_answer` state fields**: `summary_answer_raw` — agent output with `[C1]` identifiers (audit trail); `summary_answer_map` — `"C1"` → `{cluster_id, bluebook_citation, case_name}`; `summary_answer` — final resolved text (read by `generate_html.py`).

**`resolve-citations` linking**: `cmd_resolve_citations()` builds a URL map from `analyzed_cases` + `cases_table` and wraps each Bluebook citation in `<a href="url">` tags. The `summary_answer` field contains fully-linked HTML — `generate_html.py` renders it without further transformation.

**`subsequent_history` state structure**: Dict keyed by string cluster_id. Only flagged cases (those with negative treatment) have entries — absence means "not flagged," not "confirmed good law." Each entry: `{"precedential_status": "reversed|overruled|...", "detail": "One sentence.", "confidence": "high|medium|uncertain", "reversing_case": {"cluster_id": N, "case_name": "...", "court": "...", "date_filed": "..."}}`.

**`history-checker` orchestrator pattern**: Agent has `tools: none` — it returns a JSON array of flagged cases only. Cases with no negative treatment are omitted from output. The orchestrator batches 4-5 cases per agent invocation, combining citing case data from `check_subsequent_history.py`. Results merged via `manage_state.py add-subsequent-history`.

| `scripts/vq_annotator.py` | Annotates HTML blockquotes with validation labels |
| `scripts/state_io.py` | Shared I/O utilities: `load_state`, `save_state`, `normalize_excerpts` — imported by all three main scripts |
| `scripts/preflight.py` | Hard-stop preflight: checks `COURTLISTENER_API_TOKEN` and pings API; exits 0 (PASS) or 1 (FAIL). Run before any MCP calls. |

### CourtListener MCP Tools

The MCP server lives in `mcp-server/` and is launched automatically by `.mcp.json` using `uv run`. Tools are prefixed `mcp__plugin_legal_research_courtlistener__`. Five tools available (documented in `skills/courtlistener-guide/SKILL.md`):

- `mcp__plugin_legal_research_courtlistener__search_cases` — Keyword search (Solr syntax; keep to 2-3 concepts per query)
- `mcp__plugin_legal_research_courtlistener__semantic_search` — Natural language conceptual search
- `mcp__plugin_legal_research_courtlistener__lookup_citation` — Resolve citation strings to cases
- `mcp__plugin_legal_research_courtlistener__get_case_text` — Full opinion text (up to 300k chars)
- `mcp__plugin_legal_research_courtlistener__find_citing_cases` — Cases citing a given decision

Query pattern: `[Core Concept] AND ([Variant1] OR [Variant2])` — avoid overloading queries with 5+ terms.

**Orchestrator usage**: `search_cases`, `semantic_search`, `get_case_text` are NOT in command `allowed-tools` — all search/fetch is handled by scripts. Only `lookup_citation` and `find_citing_cases` remain (used in Phase 4 citation tracing).

### Citation Format

All citations must follow **Bluebook format**: _Smith v. Jones_, 500 F.3d 123 (9th Cir. 2020).

## File Structure

```
.claude-plugin/plugin.json     # Plugin metadata (name, version, author)
.mcp.json                      # Registers bundled CourtListener MCP server (auto-launched)
mcp-server/
  server.py                    # CourtListener MCP server (FastMCP, stdio transport)
  pyproject.toml               # Dependencies: mcp[cli], httpx (managed by uv)
commands/
  research.md                   # Main orchestrator (~300 lines, script-driven workflow)
  research-continue.md          # Session continuation from state file
  research-email.md             # Non-interactive email-triggered orchestrator (mirrors research.md)
agents/
  query-analyst.md              # Search strategy generation (haiku model)
  case-searcher.md              # CourtListener search execution (retired)
  case-analyzer.md              # Individual case deep analysis
  email-query-extractor.md      # Haiku sanitizer: extracts query from email, rejects prompt injection
  answer-writer.md              # Summary answer: per-sentence [C1] citations resolved to Bluebook by resolve-citations
  history-checker.md            # Evaluates citing cases for negative subsequent treatment (haiku, no tools)
plugin-wrapper.sh               # Shell entry point called by gmail-monitor (must be chmod +x, always exits 0)
email-queries/                  # Runtime dir (created by plugin-wrapper.sh); email state/HTML files land here
skills/
  courtlistener-guide/SKILL.md  # CourtListener API reference and best practices
scripts/
  search_api.py               # Direct HTTP search for one strategy (replaces case-searcher MCP calls)
  fetch_case_text.py          # Pre-fetch opinion text for one cluster_id (replaces case-analyzer get_case_text)
  check_subsequent_history.py # Queries v4 API for citing cases with negative treatment signals
  generate_html.py              # HTML report generator (reads state file)
  manage_state.py               # State file management (dedup, leads, decisions, subsequent history)
  log_session.py                # Session logging: error/note/ingest-search/ingest-fetch/ingest-hist write to state; summary appends to JSONL log
  run_quote_validation.py       # Quote validation orchestrator
  vq_matcher.py                 # Three-tier quote matcher
  vq_annotator.py               # HTML quote annotation
  state_io.py                   # Shared load_state/save_state/normalize_excerpts
legal-research-sessions.jsonl   # Append-only JSONL log; one record per completed session (created on first run)
```
