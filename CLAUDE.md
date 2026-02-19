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
- `/legal-research:research-continue "<refinement direction>"` — Continue/refine an existing session

## Architecture

### Orchestrator + Specialized Agents + Scripts

The `commands/research.md` orchestrator drives a workflow, delegating to specialized agents for LLM tasks and Python scripts for mechanical tasks:

1. **Phase 0 — Query Decomposition**: Parse user input into structured elements (legal_questions, fact_pattern, jurisdiction, query_type)
2. **Phase 1 — Query Analysis**: `agents/query-analyst.md` (runs on haiku) generates 4-6 search strategies
3. **Phase 2 — Parallel Search**: 3-4 `agents/case-searcher.md` instances run in parallel (15 results/query, top 20 returned per agent)
4. **Phase 3 — Deep Case Analysis**: 8-12 `agents/case-analyzer.md` launched in parallel (one case per agent, 3 excerpts each)
5. **Phase 3.5 — Automatic Depth Decision**: `scripts/manage_state.py should-refine` decides whether to iterate based on result quality and unexplored leads — no user checkpoint
6. **Phase 4 — Iterative Refinement** (if triggered): Additional search rounds using new terminology and citation tracing, driven by `manage_state.py get-leads`
7. **Phase 5 — Output**: `scripts/generate_html.py` assembles HTML from state file; `scripts/run_quote_validation.py` verifies all quotations

### Key Design Principles

- **State file (`research-{slug}-state.json`) is the single source of truth** — context is ephemeral, all results persist in this file
- **Scripts handle mechanical work** — HTML generation, state management, deduplication, quote validation are all deterministic Python, never LLM-composed
- **No synthesizer agent** — the HTML report is assembled by `generate_html.py` directly from case-analyzer outputs
- **Verbatim rendering** — case data fields are rendered exactly as returned by analyzers, never re-interpreted
- **Automatic depth decisions** — the system decides whether to refine based on result quality (< 3 high-relevance cases) and lead potential (> 3 unexplored citations), not user input
- **Query type (`fact`/`law`/`mixed`) drives everything** — search strategy, analysis depth, and output format all adapt

### Agent Contracts

| Agent | Model | Input | Output |
|-------|-------|-------|--------|
| `query-analyst` | haiku | Structured parsed_query | Array of search strategies |
| `case-searcher` | inherit | Search strategies + tool access | Deduplicated case results with metadata |
| `case-analyzer` | inherit | Single cluster_id + query context | Deep analysis adapted to query type |

### Scripts

| Script | Purpose |
|--------|---------|
| `scripts/generate_html.py` | Reads state file, produces complete HTML report |
| `scripts/manage_state.py` | State file operations: add-searches, add-analysis, get-leads, top-candidates, summary, should-refine, mark-explored |
| `scripts/run_quote_validation.py` | Orchestrates quote validation: checks opinion files, runs matcher, annotates HTML |
| `scripts/vq_matcher.py` | Three-tier quote matching (normalized substring → token sequence → fuzzy) |
| `scripts/vq_annotator.py` | Annotates HTML blockquotes with validation labels |
| `scripts/state_io.py` | Shared I/O utilities: `load_state`, `save_state`, `normalize_excerpts` — imported by all three main scripts |

### CourtListener MCP Tools

The MCP server lives in `mcp-server/` and is launched automatically by `.mcp.json` using `uv run`. Tools are prefixed `mcp__plugin_legal_research_courtlistener__`. Five tools available (documented in `skills/courtlistener-guide/SKILL.md`):

- `mcp__plugin_legal_research_courtlistener__search_cases` — Keyword search (Solr syntax; keep to 2-3 concepts per query)
- `mcp__plugin_legal_research_courtlistener__semantic_search` — Natural language conceptual search
- `mcp__plugin_legal_research_courtlistener__lookup_citation` — Resolve citation strings to cases
- `mcp__plugin_legal_research_courtlistener__get_case_text` — Full opinion text (up to 50k chars)
- `mcp__plugin_legal_research_courtlistener__find_citing_cases` — Cases citing a given decision

Query pattern: `[Core Concept] AND ([Variant1] OR [Variant2])` — avoid overloading queries with 5+ terms.

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
agents/
  query-analyst.md              # Search strategy generation (haiku model)
  case-searcher.md              # CourtListener search execution
  case-analyzer.md              # Individual case deep analysis
skills/
  courtlistener-guide/SKILL.md  # CourtListener API reference and best practices
scripts/
  generate_html.py              # HTML report generator (reads state file)
  manage_state.py               # State file management (dedup, leads, decisions)
  run_quote_validation.py       # Quote validation orchestrator
  vq_matcher.py                 # Three-tier quote matcher
  vq_annotator.py               # HTML quote annotation
  state_io.py                   # Shared load_state/save_state/normalize_excerpts
```
