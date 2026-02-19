---
description: "Continue or refine an existing legal research session"
argument-hint: "<search_id> <refinement direction or additional question>"
allowed-tools: Task, Read, Write, Bash, AskUserQuestion, mcp__courtlistener__search_cases, mcp__courtlistener__semantic_search, mcp__courtlistener__lookup_citation, mcp__courtlistener__get_case_text, mcp__courtlistener__find_citing_cases
---

# Continue Legal Research

You are continuing a legal research session previously started with `/legal-research:research`.

**Raw input**: $ARGUMENTS

---

## Step 1: Parse Arguments and Load State

The first token in `$ARGUMENTS` is the **Search ID**. Everything after it is the **refinement direction**.

Example: `statute-limitations-0452 focus on Washington appellate courts`

**If no Search ID is provided**, inform the user:
```
ERROR: A Search ID is required.
Usage: /legal-research:research-continue <search_id> <refinement direction>
To find your Search ID, check the top of your results HTML file or look for research-*-state.json files.
```

**Load** `research-{search_id}-state.json`. If it doesn't exist, tell the user and stop.

Run:
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_state.py --state research-{search_id}-state.json summary
```

Display the session summary to the user: original query, query type, workflow mode, rounds completed, cases found/analyzed, relevance breakdown, pending leads.

---

## Step 2: Determine Continuation Strategy

Based on the refinement direction and existing state:

### Quick-to-Deep Continuation

If previous session was quick mode (`workflow_mode === "quick"`): the natural continuation is to run Phase 4 (iterative refinement) on existing results. Tell the user this is what you'll do unless their refinement direction specifies something different.

### Standard Continuations

- **Narrow focus**: Search within a specific jurisdiction, time period, or factual pattern
- **Explore follow-ups**: Examine cases in `pending_leads` via `manage_state.py get-leads`
- **Find recent developments**: `find_citing_cases` on top authorities
- **New angle**: Generate new search strategies from the refinement direction
- **Deepen analysis**: Analyze cases found but not yet deeply analyzed (via `manage_state.py top-candidates`)

Present your plan briefly before proceeding.

---

## Step 3: Execute Additional Research

Follow the same patterns as the main research command:
- Launch **case-searcher** agents for new strategies
- Launch **case-analyzer** agents for new cases (one per case, pass `query_type`)
- Use **find_citing_cases** for citation tracing

Merge all results via `manage_state.py`:
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_state.py --state research-{search_id}-state.json add-searches /tmp/searcher_results.json --round N
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_state.py --state research-{search_id}-state.json add-analysis /tmp/analysis_results.json
```

Update `workflow_mode` to `"deep"` if transitioning from quick mode.

Log progress: queries executed, results, case selections, analysis findings.

---

## Step 4: Generate Output

**Do NOT compose HTML manually.** Use the scripts:

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/generate_html.py research-{search_id}-state.json
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/run_quote_validation.py research-{search_id}-state.json --annotate
```

If quote validation reports missing opinion files, launch re-fetch agents (minimal prompt, fetch and save only), then rerun validation.

---

## Step 5: Present Results

Highlight what changed from the previous session:
- New cases discovered (with citations)
- New follow-up leads
- If transitioning from quick to deep: "Expanded initial results with [N] additional rounds and [N] new cases analyzed."

```
Updated results: research-{search_id}-results.html (open in browser)
Updated state: research-{search_id}-state.json
To continue: /legal-research:research-continue {search_id} "<direction>"
```

---

## Important Notes

- **Preserve existing work**: Never discard previously analyzed cases. Build on existing research.
- **Increment round numbers**: Check `iteration_log` for the last round number.
- **Respect query type**: Maintain `query_type` unless the refinement direction clearly changes it.
- **One case per analyzer**: Never pass multiple cases to a single case-analyzer agent.
- **Scripts handle HTML**: Use `generate_html.py` and `run_quote_validation.py` — never compose HTML manually.
