---
description: "Continue or refine an existing legal research session"
argument-hint: "<request_id> <refinement direction or additional question>"
allowed-tools: Task, Read, Write, Bash, AskUserQuestion, mcp__plugin_legal_research_courtlistener__lookup_citation, mcp__plugin_legal_research_courtlistener__find_citing_cases
---

# Continue Legal Research

You are continuing a legal research session previously started with `/legal-research:research`.

**Raw input**: $ARGUMENTS

---

## Preflight Check

Run the preflight script:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/preflight.py
```

**This is a hard stop.**

- If the script **exits 0** and prints `PASS:` → API is available. Proceed.
- If the script **exits non-zero** (any other exit code) → **STOP IMMEDIATELY.** Do not proceed to any other step. Output:

```
ERROR: CourtListener MCP is not available. Legal research cannot proceed.
[paste the full output of preflight.py here]
```

Do not attempt to work around this check or continue the workflow.

---

## Step 1: Parse Arguments and Load State

The first token in `$ARGUMENTS` is the **Request ID**. Everything after it is the **refinement direction**.

Example: `REQ-20260220-143022-a3f1 focus on Washington appellate courts`

**If no Request ID is provided**, inform the user:
```
ERROR: A Request ID is required.
Usage: /legal-research:research-continue <request_id> <refinement direction>
To find your Request ID, check the top of your results HTML file or look for research-REQ-*-state.json files.
```

**Load** `research-{request_id}-state.json`. If it doesn't exist, tell the user and stop.

Run:
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_state.py --state research-{request_id}-state.json summary
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

Present your plan briefly before proceeding. Log the continuation strategy as a session note:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/log_session.py note --state-file research-{request_id}-state.json --message "research-continue: [brief description of continuation strategy]"
```

---

## Step 3: Execute Additional Research

**Searching**: For new search strategies, write each to `/tmp/strategy_{strategy_id}.json` and run `search_api.py` in parallel:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/search_api.py /tmp/strategy_{strategy_id}.json > /tmp/search_raw_{strategy_id}.json &
# (one line per strategy)
wait
```

Check each output for `{"error": ...}`. Log failures via `log_session.py` (level `warn`). For successful results:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_state.py --state research-{request_id}-state.json \
  add-searches /tmp/search_raw_{strategy_id}.json --round N
```

Log search batch performance (list all output files from this round):
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/log_session.py ingest-search \
  --state-file research-{request_id}-state.json --phase "research-continue" \
  /tmp/search_raw_{strategy_id}.json [...]
```

After merging new cases: launch ONE **case-scorer** agent (model: haiku, tools: none) for new cases only. Pass the research question, the new cases list (cluster_id, case_name, court, date_filed, cite_count, snippet), and the names/scores of existing analyzed cases for calibration. Write the agent's JSON array output to `/tmp/case_scores.json`. Merge scores:
```bash
python3 -c "
import json, sys
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
from state_io import load_state, save_state
scores = {s['cluster_id']: s for s in json.load(open('/tmp/case_scores.json'))}
state = load_state('research-{request_id}-state.json')
for case in state.get('cases_table', []):
    cid = case.get('cluster_id')
    if cid in scores:
        case['initial_relevance'] = scores[cid]['initial_relevance']
        case['relevance_note'] = scores[cid]['relevance_note']
save_state('research-{request_id}-state.json', state)
print('Scores merged:', len(scores))
"
```

**Analysis**: For new cases to analyze, pre-fetch opinion text in parallel using `fetch_case_text.py`:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_case_text.py {cluster_id} > /tmp/fetch_{cluster_id}.json &
# (one line per case)
wait
```

Log fetch batch performance:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/log_session.py ingest-fetch \
  --state-file research-{request_id}-state.json --phase "research-continue" \
  /tmp/fetch_{cluster_id}.json [...]
```

Check each fetch result. For successful fetches (`"error": null`), launch one **case-analyzer** agent per case in parallel. Pass `cluster_id`, `url: "https://www.courtlistener.com{absolute_url}"` (from fetch metadata), `case_name`, `date_filed`, `parsed_query` (including `query_type`). For each valid analysis result: write to `/tmp/analysis_{cluster_id}.json` and run:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_state.py --state research-{request_id}-state.json \
  add-analysis /tmp/analysis_{cluster_id}.json
```

**Citation tracing**: Use `mcp__plugin_legal_research_courtlistener__find_citing_cases` and `mcp__plugin_legal_research_courtlistener__lookup_citation` for citation-based leads (these MCP tools are available in the orchestrator context).

Update `workflow_mode` to `"deep"` if transitioning from quick mode.

Log progress: queries executed, results, case selections, analysis findings.

---

## Step 4: Generate Output

**Do NOT compose HTML manually.** Use the scripts.

### Step 4a: Generate Summary Answer

**Step A — Build input payload:**

```bash
python3 -c "
import json
state = json.load(open('research-{request_id}-state.json'))
analyzed = sorted(state.get('analyzed_cases', []),
                  key=lambda x: x.get('relevance_ranking', 0), reverse=True)[:10]
mapping = {f'C{i+1}': {'cluster_id': c['cluster_id'],
                        'bluebook_citation': c.get('bluebook_citation', ''),
                        'case_name': c.get('case_name', '')}
           for i, c in enumerate(analyzed)}
payload = {
    'user_query': state['parsed_query']['original_input'],
    'case_map': mapping,
    'cases': [{**c, '_id': f'C{i+1}'} for i, c in enumerate(analyzed)]
}
json.dump(payload, open('/tmp/answer_writer_input.json', 'w'), indent=2)
json.dump(mapping, open('/tmp/answer_writer_map.json', 'w'), indent=2)
"
```

**Step B — Launch answer-writer agent:**

Launch one **answer-writer** agent. Pass it this prompt:
> Read `/tmp/answer_writer_input.json` and follow the agent instructions.

**Step C — Store raw result and mapping in state:**

```bash
python3 -c "
import json
raw = open('/tmp/answer_writer_output.txt').read().strip()
mapping = json.load(open('/tmp/answer_writer_map.json'))
state = json.load(open('research-{request_id}-state.json'))
state['summary_answer_raw'] = raw
state['summary_answer_map'] = mapping
json.dump(state, open('research-{request_id}-state.json', 'w'), indent=2)
"
```

**Step D — Resolve citations:**

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_state.py --state research-{request_id}-state.json resolve-citations
```

### Step 4b: Generate HTML and validate quotes

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/generate_html.py research-{request_id}-state.json
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/run_quote_validation.py research-{request_id}-state.json --annotate
```

If quote validation reports missing opinion files, run `fetch_case_text.py` for each missing cluster_id, then rerun validation:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_case_text.py {cluster_id} > /tmp/fetch_{cluster_id}.json
```

---

## Step 5: Write session log and present results

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/log_session.py summary --state-file research-{request_id}-state.json --log-file ./legal-research-sessions.jsonl --mode continue --output-file "$(pwd)/research-{request_id}-results.html"
```

Highlight what changed from the previous session:
- New cases discovered (with citations)
- New follow-up leads
- If transitioning from quick to deep: "Expanded initial results with [N] additional rounds and [N] new cases analyzed."

```
Updated results: research-{request_id}-results.html (open in browser)
Updated state: research-{request_id}-state.json
To continue: /legal-research:research-continue {request_id} "<direction>"
```

---

## Important Notes

- **Preserve existing work**: Never discard previously analyzed cases. Build on existing research.
- **Increment round numbers**: Check `iteration_log` for the last round number.
- **Respect query type**: Maintain `query_type` unless the refinement direction clearly changes it.
- **One case per analyzer**: Never pass multiple cases to a single case-analyzer agent.
- **Scripts handle HTML**: Use `generate_html.py` and `run_quote_validation.py` — never compose HTML manually.
