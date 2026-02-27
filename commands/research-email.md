---
description: "Non-interactive legal research triggered by incoming email. Reads email body from /tmp/gmail-monitor/req-${REQUEST_ID}.txt. Never pauses for user input."
argument-hint: "(no arguments — reads REQUEST_ID from environment)"
allowed-tools: Task, Read, Write, Bash, mcp__plugin_legal_research_courtlistener__lookup_citation, mcp__plugin_legal_research_courtlistener__find_citing_cases
---

# Email-Triggered Legal Research

You are conducting systematic legal research triggered by an incoming email. This is a **non-interactive** run. Never call `AskUserQuestion`. Apply all defaults automatically.

## Context Management

The state file is the primary data store. Your context is ephemeral.

- **The state file (`research-${REQUEST_ID}-state.json`) is the single source of truth.** Full subagent results go there, not in context.
- **After each phase**, write results to state immediately via `manage_state.py`.
- **In context, retain only summaries**: counts, top candidate names/scores, cluster_id lists.
- **Run `/compact` between phases.**
- **Delegate mechanical work to scripts** in `${CLAUDE_PLUGIN_ROOT}/scripts/`. Never compose HTML manually or manipulate JSON in context when a script can do it.

---

## Preflight Check

Run the preflight script:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/preflight.py
```

**This is a hard stop.**

- If the script **exits 0** and prints `PASS:` → API is available. Proceed.
- If the script **exits non-zero** (any other exit code) → **STOP IMMEDIATELY.** Write the error HTML below to `/tmp/gmail-monitor/result-${REQUEST_ID}.html` and stop. Do not proceed to any other phase.

```html
<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Research Error</title></head>
<body><h1>Legal Research: Service Unavailable</h1>
<p>The CourtListener case law database is currently unavailable. Please try again later.</p>
</body></html>
```

Do not attempt to work around this check or continue the workflow.

---

## Read Email Body

Run:
```bash
cat /tmp/gmail-monitor/req-${REQUEST_ID}.txt
```

If `REQUEST_ID` is not set, or the file is missing, or the file is empty: write the error HTML below to `/tmp/gmail-monitor/result-${REQUEST_ID}.html` and stop.

```html
<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Research Error</title></head>
<body><h1>Legal Research: Input Error</h1>
<p>No email body was found for this request. Please try again.</p>
</body></html>
```

Store the email body text for use in Phase E.

---

## Phase E: Email Query Extraction

Launch one **email-query-extractor** agent. Pass it the full email body text as its input.

Parse the JSON response the agent returns.

**If `status == "no_query"`**: Write the informational HTML below to `/tmp/gmail-monitor/result-${REQUEST_ID}.html` and stop normally.

```html
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><title>Legal Research: No Question Found</title></head>
<body>
<h1>Legal Research</h1>
<p>Your email did not appear to contain a legal research question.</p>
<p>To request research, send an email with a clear legal question, such as:</p>
<ul>
  <li>"What is the standard for qualified immunity in excessive force cases in the Ninth Circuit?"</li>
  <li>"Find cases where courts have held employers liable for independent contractor injuries."</li>
  <li>"How have courts analyzed First Amendment retaliation claims by public employees?"</li>
</ul>
<p>Include any relevant jurisdictional context or factual details in your email.</p>
</body>
</html>
```

**If `status == "query"`**: Store the extracted query as `RESEARCH_QUERY`. Log to stdout: `Query extracted: [RESEARCH_QUERY]`. Proceed.

---

## Phase 0: Structured Query Decomposition

Parse `RESEARCH_QUERY` into structured elements. Apply these **non-interactive defaults** automatically — never ask the user:

| Element | Default when unspecified |
|---------|-------------------------|
| `jurisdiction` | `"federal (national)"` |
| `query_type` | `"mixed"` |
| `depth_preference` | `"deep"` |
| `date_range` | `{"after": "", "before": ""}` (no filter) |

Parse into all standard fields:

| Element | Description |
|---------|-------------|
| `legal_questions` | Doctrinal/legal questions being asked |
| `fact_pattern` | Specific factual scenario, if any |
| `jurisdiction` | Jurisdiction mentioned or implied (default: `"federal (national)"`) |
| `date_range` | Temporal constraints |
| `constraints` | Other constraints |
| `query_type` | `"fact"`, `"law"`, or `"mixed"` (default: `"mixed"`) |
| `depth_preference` | Always `"deep"` for email mode |
| `required_legal_context` | Inferred prerequisites: `party_relationship`, `legal_predicate`, `applicable_test` |

Log the parsed query table to stdout. Write the initial state file with the two extra email fields:

```json
{
  "email_mode": true,
  "request_id": "${REQUEST_ID}",
  "parsed_query": { "legal_questions": [], "fact_pattern": "", "jurisdiction": "", "date_range": {}, "constraints": [], "query_type": "", "depth_preference": "deep", "original_input": "[RESEARCH_QUERY]", "required_legal_context": { "party_relationship": null, "legal_predicate": null, "applicable_test": null } },
  "workflow_mode": null,
  "search_strategies": [],
  "search_terms_table": [],
  "cases_table": [],
  "search_results_raw": [],
  "analyzed_cases": [],
  "iteration_log": [],
  "pending_leads": [],
  "explored_cluster_ids": [],
  "explored_terms": [],
  "pivotal_cases": [],
  "session_log": {"errors": [], "notes": [], "events": []}
}
```

After writing the state file, log the first two checkpoints:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/log_session.py note --state-file research-${REQUEST_ID}-state.json --message "Phase E complete: query extracted"
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/log_session.py note --state-file research-${REQUEST_ID}-state.json --message "Phase 0 complete: [query_type] query, jurisdiction=[jurisdiction]"
```

(Substitute the actual `query_type` and `jurisdiction` values from `parsed_query`.)

---

## Phase 1: Query Analysis

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/log_session.py note --state-file research-${REQUEST_ID}-state.json --message "Phase 1 start: launching query-analyst"
```

Launch 1 **query-analyst** agent with the `parsed_query` object. It returns 4-6 search strategies with keyword queries, semantic queries, court filters, and rationale.

Review the strategies. If they miss an obvious angle, add strategies yourself.

Save the strategies to the state file under `search_strategies`.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/log_session.py note --state-file research-${REQUEST_ID}-state.json --message "Phase 1 complete: [N] strategies generated"
```

(Substitute the actual count of strategies saved.)

Log the strategies to stdout.

---

## Phase 2: Parallel Initial Search

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/log_session.py note --state-file research-${REQUEST_ID}-state.json --message "Phase 2 start: launching searches for [N] strategies"
```

(Substitute the actual number of strategies.)

For each search strategy, write its JSON to `/tmp/strategy_{strategy_id}.json`. Launch all searches in parallel using `search_api.py`:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/search_api.py /tmp/strategy_S1.json > /tmp/search_raw_S1.json &
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/search_api.py /tmp/strategy_S2.json > /tmp/search_raw_S2.json &
# (one line per strategy)
wait
```

**Check each output file for errors.** Read `/tmp/search_raw_{strategy_id}.json`:
- If it contains `{"error": ...}`: log to stdout and run:
  ```bash
  python3 ${CLAUDE_PLUGIN_ROOT}/scripts/log_session.py error --state-file research-${REQUEST_ID}-state.json --level warn --message "strategy-{strategy_id}: search failed — {error}" --phase "Phase 2"
  ```
- If **ALL** strategies failed:
  ```bash
  python3 ${CLAUDE_PLUGIN_ROOT}/scripts/log_session.py error --state-file research-${REQUEST_ID}-state.json --level fatal --message "All searches failed — research halted" --phase "Phase 2"
  python3 ${CLAUDE_PLUGIN_ROOT}/scripts/log_session.py summary --state-file research-${REQUEST_ID}-state.json --log-file ./legal-research-sessions.jsonl --mode email
  ```
  Write the error HTML to `/tmp/gmail-monitor/result-${REQUEST_ID}.html` and stop.

For each successful result, run:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_state.py --state research-${REQUEST_ID}-state.json \
  add-searches /tmp/search_raw_{strategy_id}.json --round 1
```

**Log search batch performance.** After all `add-searches` calls complete:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/log_session.py ingest-search \
  --state-file research-${REQUEST_ID}-state.json --phase "Phase 2" \
  /tmp/search_raw_S1.json [/tmp/search_raw_S2.json ...]
```

List all `/tmp/search_raw_{strategy_id}.json` files from this batch (include both succeeded and failed).

**Score new cases.** Read the current `cases_table` from the state file. Launch ONE **case-scorer** agent (model: haiku, tools: none) with:

> Research question: [paste `parsed_query.original_input`]
>
> Score the following cases for relevance. Return ONLY a JSON array — no preamble, no code fences.
>
> Cases: [paste cases_table entries as JSON array with: cluster_id, case_name, court, date_filed, cite_count, snippet]

Write the agent's output to `/tmp/case_scores.json`. Merge:
```bash
python3 -c "
import json, sys
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
from state_io import load_state, save_state
scores = {s['cluster_id']: s for s in json.load(open('/tmp/case_scores.json'))}
state = load_state('research-${REQUEST_ID}-state.json')
for case in state.get('cases_table', []):
    cid = case.get('cluster_id')
    if cid in scores:
        raw = scores[cid].get('initial_relevance', 3)
        case['initial_relevance'] = min(5, max(1, int(raw)))
        case['relevance_note'] = scores[cid]['relevance_note']
save_state('research-${REQUEST_ID}-state.json', state)
print('Scores merged:', len(scores))
"
```

Add an `iteration_log` entry for round 1. Log a round 1 report to stdout: queries executed, result counts, top 5-8 candidates.

**After writing to state, clear context.** Keep only: total case count, top candidate names/cluster_ids, and the list of cluster_ids for Phase 3. Run `/compact`.

---

## Phase 3: Deep Case Analysis

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/log_session.py note --state-file research-${REQUEST_ID}-state.json --message "Phase 3 start: selecting top candidates"
```

Select the top 8-12 cases for analysis. Run:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_state.py --state research-${REQUEST_ID}-state.json top-candidates 12
```

Prioritize: highest initial_relevance, highest cite_count, court variety, recency. Log the selection with rationale to stdout.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/log_session.py note --state-file research-${REQUEST_ID}-state.json --message "Phase 3 selected: [N] cases — [cluster_id1, cluster_id2, ...]"
```

**Pre-fetch opinion text and run subsequent history queries in parallel.** For each selected cluster_id, look up its `court` and `date_filed` from `cases_table`. Launch both `fetch_case_text.py` and `check_subsequent_history.py` for each case in the same `&`/`wait` block:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_case_text.py {cluster_id_1} > /tmp/fetch_{cluster_id_1}.json &
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/check_subsequent_history.py {cluster_id_1} --court "{court_1}" --date-filed "{date_filed_1}" > /tmp/hist_{cluster_id_1}.json &
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_case_text.py {cluster_id_2} > /tmp/fetch_{cluster_id_2}.json &
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/check_subsequent_history.py {cluster_id_2} --court "{court_2}" --date-filed "{date_filed_2}" > /tmp/hist_{cluster_id_2}.json &
# (two lines per case: one fetch, one history)
wait
```

Read each `/tmp/fetch_{cluster_id}.json`:
- **Success** (`"error": null`): add to `fetch_ok` list with `{cluster_id, case_name, date_filed, absolute_url, truncated}`
- **Failure**: log via `log_session.py` (level `warn`) and skip

**Log fetch and history batch performance:**

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/log_session.py ingest-fetch \
  --state-file research-${REQUEST_ID}-state.json --phase "Phase 3" \
  /tmp/fetch_{cluster_id_1}.json [/tmp/fetch_{cluster_id_2}.json ...]
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/log_session.py ingest-hist \
  --state-file research-${REQUEST_ID}-state.json --phase "Phase 3" \
  /tmp/hist_{cluster_id_1}.json [/tmp/hist_{cluster_id_2}.json ...]
```

For each case in `fetch_ok`, launch **one case-analyzer agent** in parallel. Pass:
- `cluster_id`
- `url: "https://www.courtlistener.com{absolute_url}"` (from fetch metadata)
- `case_name`, `date_filed` (from fetch metadata)
- `parsed_query` (including `query_type`)
- Instruction: "Read `/tmp/vq_opinion_{cluster_id}.txt`. If missing or fewer than 500 characters, return the error JSON immediately."

After all agents return:

1. For each result:
   - If `{"error": "opinion_file_missing", ...}`: log via `log_session.py` (level `warn`) and skip.
   - If valid analysis: write to `/tmp/analysis_{cluster_id}.json` and run:
     ```bash
     python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_state.py --state research-${REQUEST_ID}-state.json \
       add-analysis /tmp/analysis_{cluster_id}.json
     ```

2. Log per-case results to stdout: name, citation, relevance, position, one-line finding.
3. Note new search leads discovered.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/log_session.py note --state-file research-${REQUEST_ID}-state.json --message "Phase 3 complete: [N] analyses written"
```

**Clear context after writing to state.** Keep only per-case one-line summaries and the new search terms. Run `/compact`.

---

## Phase 3.6: Pivotal Case Detection

After all analyses are merged into state, scan for `pivotal_case` fields in the collected analyses.

Run:
```bash
python3 -c "
import json, sys
state = json.load(open('research-${REQUEST_ID}-state.json'))
pivotal = []
seen = set()
for case in state.get('analyzed_cases', []):
    pc = case.get('pivotal_case')
    if pc and pc.get('name') and pc['name'] not in seen:
        pivotal.append(pc)
        seen.add(pc['name'])
if pivotal:
    state['pivotal_cases'] = pivotal
    json.dump(state, open('research-${REQUEST_ID}-state.json', 'w'), indent=2)
    print(json.dumps(pivotal))
else:
    print('[]')
"
```

If the output is non-empty, log to stdout:
```
### Pivotal Authority Identified
[Name of pivotal case] — [rule_adopted]
Note: [note from pivotal_case field]
Cases pre-dating [year] will be deprioritized in subsequent rounds.
```

---

## Phase 3.5: Automatic Depth Decision

Run:
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_state.py --state research-${REQUEST_ID}-state.json should-refine
```

The script returns a JSON decision:
```json
{"decision": "refine|skip", "reason": "...", "stats": {...}}
```

Because `depth_preference` is always `"deep"` in email mode, the script will always return `"refine"`. Log the decision to stdout:
```
### Depth Decision: Refining
Reason: [reason from script]
High-relevance cases: [N] | Unexplored leads: [N]
```

Log the depth decision as a session note:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/log_session.py note --state-file research-${REQUEST_ID}-state.json --message "Depth decision: refine — [reason from script]"
```

Set `workflow_mode` to `"deep"` in the state file. Proceed to Phase 4.

---

## Phase 4: Iterative Refinement

Perform at least one refinement round. This phase uses prior results to drive new searches.

**CRITICAL: Do NOT add cases to the state file via inline Python. All case additions MUST go through `manage_state.py add-searches` (for search_api.py results) or `manage_state.py add-mcp-cases` (for MCP tool results).**

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/log_session.py note --state-file research-${REQUEST_ID}-state.json --message "Phase 4 start: refinement round [N]"
```

(Substitute the actual round number, starting at 1.)

### API Failure Tracking

Maintain a running count of consecutive MCP tool failures during this phase (initialize `mcp_fail_count = 0`). After each MCP tool call (`lookup_citation` or `find_citing_cases`):
- If the call **succeeded** (returned valid data with a cluster_id): reset `mcp_fail_count = 0`
- If the call **failed** (returned an error, empty results, or timed out): increment `mcp_fail_count += 1`

**If `mcp_fail_count` reaches 3:**
1. Log: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/log_session.py error --state-file research-${REQUEST_ID}-state.json --level warn --message "API degradation detected: 3+ consecutive MCP failures in Phase 4" --phase "Phase 4"`
2. Skip all remaining Phase 4 MCP work (citation tracing, find_citing_cases)
3. Continue with any search_api.py strategies already planned, then proceed to the partial-result exit check below

### Step 1: Analyze leads

Run:
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_state.py --state research-${REQUEST_ID}-state.json get-leads
```

This returns unexplored citation leads and search terms that emerged from analyzed cases but haven't been searched yet.

### Step 2: Citation tracing via MCP tools

**For citation leads** from `get-leads`: Use `mcp__plugin_legal_research_courtlistener__lookup_citation` to resolve each cited case. Collect all successful results (those returning a `cluster_id`) into a JSON array. Write to `/tmp/mcp_cases_round_{N}.json` and run:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_state.py --state research-${REQUEST_ID}-state.json \
  add-mcp-cases /tmp/mcp_cases_round_{N}.json --source lookup_citation --round {N}
```

**For the 2-3 most important analyzed cases** (highest relevance): Use `mcp__plugin_legal_research_courtlistener__find_citing_cases` to find recent applications. Collect results into a JSON array. Write to `/tmp/mcp_citing_round_{N}.json` and run:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_state.py --state research-${REQUEST_ID}-state.json \
  add-mcp-cases /tmp/mcp_citing_round_{N}.json --source find_citing_cases --round {N}
```

### Step 3: Generate and execute search strategies

**For new terminology**: Generate 2-3 new keyword/semantic queries using terms discovered in the analyzed cases.

**If analogous expansion was triggered** (the `should-refine` reason includes "analogous expansion needed"): Launch a second **query-analyst** agent with an explicit instruction: *"The original factual pattern returned few results. Generate 2-3 additional strategies using broader factual framings, analogous party configurations, or the governing legal doctrine for this type of scenario. Tag all strategies as `strategy_type: 'analogous'`."* Incorporate these strategies into the refinement search round.

Write each refined strategy to `/tmp/strategy_{strategy_id}.json`. Launch all `search_api.py` processes in parallel (same pattern as Phase 2), outputting to `/tmp/search_raw_{strategy_id}.json`. After `wait`, check for errors (log failures). For successful results:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_state.py --state research-${REQUEST_ID}-state.json \
  add-searches /tmp/search_raw_{strategy_id}.json --round {N}
```

Log search batch performance (list all output files from this round):
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/log_session.py ingest-search \
  --state-file research-${REQUEST_ID}-state.json --phase "Phase 4" \
  /tmp/search_raw_{strategy_id}.json [...]
```

After merging new cases: identify new cluster_ids (not yet scored). Launch ONE **case-scorer** agent for new cases only, passing the research question, new cases list, and existing analyzed-case names/scores for calibration. Write output to `/tmp/case_scores.json` and merge into state using the same python snippet as Phase 2 (with score clamping).

### Step 4: Analyze new finds

Run `manage_state.py top-candidates 8` to get the best unanalyzed cases. For each candidate, look up its `court` and `date_filed` from `cases_table`. Pre-fetch opinion text and run subsequent history queries in parallel (same pattern as Phase 3):

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_case_text.py {cluster_id_1} > /tmp/fetch_{cluster_id_1}.json &
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/check_subsequent_history.py {cluster_id_1} --court "{court_1}" --date-filed "{date_filed_1}" > /tmp/hist_{cluster_id_1}.json &
# (two lines per case: one fetch, one history)
wait
```

Log fetch and history batch performance after the `wait`:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/log_session.py ingest-fetch \
  --state-file research-${REQUEST_ID}-state.json --phase "Phase 4" \
  /tmp/fetch_{cluster_id}.json [...]
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/log_session.py ingest-hist \
  --state-file research-${REQUEST_ID}-state.json --phase "Phase 4" \
  /tmp/hist_{cluster_id}.json [...]
```
Launch **case-analyzer** agents for the top 5-8 cases that fetched successfully. Merge via `manage_state.py add-analysis`.

### Step 5: Validate scores

After all scoring and analysis in this round, run:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_state.py --state research-${REQUEST_ID}-state.json validate-scores
```

### Step 6: Check for another round

Run both checks (N = current round number, e.g., 2 for the first refinement round):
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_state.py --state research-${REQUEST_ID}-state.json check-diminishing-returns --round N
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_state.py --state research-${REQUEST_ID}-state.json get-leads
```

Log the decision:
```
### Round N+1 Decision: [Continuing / Stopping — diminishing returns]
Overlap: [X]% of new cases already analyzed | Unexplored leads: [N]
```

**Decision logic**:
- If diminishing-returns `decision == "stop"` **OR** unexplored leads <= 3: proceed to Phase 5.
- If diminishing-returns `decision == "continue"` **AND** unexplored leads > 3: run one more round.

Mark explored terms and cluster_ids:
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_state.py --state research-${REQUEST_ID}-state.json mark-explored /tmp/explored.json
```

Log refinement results to stdout: new terms discovered, strategy productivity, new cases found and analyzed.

### Partial-Result Exit (API Degradation)

If API degradation was detected during this phase (3+ consecutive MCP failures), check whether enough data exists to produce a useful report:

**If at least 3 analyzed cases with `relevance_ranking >= 3` exist:**
1. Set `state["partial_results"] = true` in the state file
2. Log: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/log_session.py note --state-file research-${REQUEST_ID}-state.json --message "Research truncated: API degradation, outputting partial results"`
3. Skip Phase 4.5, proceed directly to Phase 5

**If fewer than 3 analyzed cases with `relevance_ranking >= 3`:**
1. Log: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/log_session.py error --state-file research-${REQUEST_ID}-state.json --level fatal --message "API degradation with insufficient data — cannot produce reliable report" --phase "Phase 4"`
2. Set `state["partial_results"] = true` in the state file
3. Still proceed to Phase 5 — the partial results banner will warn the user

Run `/compact` before Phase 4.5.

---

## Phase 4.5: Subsequent History Check

This phase evaluates all analyzed cases for negative subsequent treatment (reversal, overruling, vacatur, etc.). History API queries were already launched in parallel with fetch operations during Phases 3 and 4. This phase runs any remaining queries for cases not yet checked, then evaluates all results with history-checker agents.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/log_session.py note --state-file research-${REQUEST_ID}-state.json --message "Phase 4.5 start: subsequent history check"
```

### Step 1: Identify unchecked cases

Read the state file to get the list of analyzed cases. Check which ones already have `/tmp/hist_{cluster_id}.json` files from Phases 3/4. Run any missing history queries:

```bash
python3 -c "
import json, os
state = json.load(open('research-${REQUEST_ID}-state.json'))
ct_map = {c['cluster_id']: c for c in state.get('cases_table', [])}
missing = []
for c in state.get('analyzed_cases', []):
    cid = c['cluster_id']
    if not os.path.exists(f'/tmp/hist_{cid}.json'):
        ct = ct_map.get(cid, {})
        missing.append({'cluster_id': cid, 'case_name': c.get('case_name',''), 'court': ct.get('court',''), 'date_filed': c.get('date_filed','')})
print(json.dumps(missing))
"
```

### Step 2: Run history queries for unchecked cases only

If there are unchecked cases, launch `check_subsequent_history.py` for each in parallel:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/check_subsequent_history.py {cid1} --court "{court1}" --date-filed "{date1}" > /tmp/hist_{cid1}.json &
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/check_subsequent_history.py {cid2} --court "{court2}" --date-filed "{date2}" > /tmp/hist_{cid2}.json &
# (one line per unchecked case — most sessions will have 0 here)
wait
```

If additional queries were run, log batch performance:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/log_session.py ingest-hist \
  --state-file research-${REQUEST_ID}-state.json --phase "Phase 4.5" \
  /tmp/hist_{cid1}.json [/tmp/hist_{cid2}.json ...]
```

If all cases were already checked in Phases 3/4, log:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/log_session.py note --state-file research-${REQUEST_ID}-state.json --message "Phase 4.5: 0 additional history queries needed — all checked during fetch phases"
```

### Step 3: Gather all history results and filter

Read all `/tmp/hist_{cluster_id}.json` files for every analyzed case:
- If `"error"` is non-null: log via `log_session.py` (level `warn`, phase "Phase 4.5") and skip.
- Otherwise: note the count of `citing_cases`.

### Step 4: Filter and batch

- Cases with 0 `citing_cases` → no flag possible, skip (no state entry needed)
- Cases with 1+ `citing_cases` → batch into groups of 4-5 for agent evaluation

### Step 5: Launch history-checker agents in parallel

For each batch of 4-5 cases that have citing cases, launch one **history-checker** agent (model: haiku, tools: none). Pass the batch as a JSON array where each entry includes `cluster_id`, `case_name`, `court`, `date_filed`, and `citing_cases`.

Write each agent's output to `/tmp/hist_checked_batch_{N}.json`.

### Step 6: Merge flagged results

Combine all agent results (only flagged cases — agents omit cases with no negative treatment) into `/tmp/subsequent_history_all.json` as a single JSON array.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_state.py --state research-${REQUEST_ID}-state.json \
  add-subsequent-history /tmp/subsequent_history_all.json --cases-checked {total_analyzed_count}
```

### Step 7: Log results

Log to stdout: total cases checked, cases flagged, and for each flagged case: name, status, detail, confidence.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/log_session.py note --state-file research-${REQUEST_ID}-state.json --message "Phase 4.5 complete: [N] cases checked, [M] flagged for negative subsequent treatment"
```

Run `/compact` before Phase 5.

---

## Phase 5: Output

**Do NOT compose HTML manually.** Use the script. Follow this exact sequence.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/log_session.py note --state-file research-${REQUEST_ID}-state.json --message "Phase 5 start: generating output"
```

### Step 1: Generate summary stats

Run:
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_state.py --state research-${REQUEST_ID}-state.json summary
```

Log the summary to stdout: total queries, total cases, analyzed count, relevance distribution.

### Step 2: Generate Summary Answer

**Step A — Build input payload:**

```bash
python3 -c "
import json
state = json.load(open('research-${REQUEST_ID}-state.json'))
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
state = json.load(open('research-${REQUEST_ID}-state.json'))
state['summary_answer_raw'] = raw
state['summary_answer_map'] = mapping
json.dump(state, open('research-${REQUEST_ID}-state.json', 'w'), indent=2)
"
```

**Step D — Resolve citations:**

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_state.py --state research-${REQUEST_ID}-state.json resolve-citations
```

### Step 3: Generate local HTML

Run (no output path argument — script derives it from state path):
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/generate_html.py research-${REQUEST_ID}-state.json
```

This produces `research-${REQUEST_ID}-results.html` in the current working directory (`email-queries/`).

### Step 4: Run quote validation

Run (annotates the local HTML in-place):
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/run_quote_validation.py research-${REQUEST_ID}-state.json --annotate
```

This script checks opinion text files, runs the three-tier matcher, and annotates the HTML with verification labels.

If the script reports missing opinion files, run `fetch_case_text.py` for each missing cluster_id:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_case_text.py {cluster_id} > /tmp/fetch_{cluster_id}.json
```
Then rerun the validation script.

Log the quote validation summary to stdout.

### Step 5: Write session log

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/log_session.py summary --state-file research-${REQUEST_ID}-state.json --log-file ./legal-research-sessions.jsonl --mode email --output-file "/tmp/gmail-monitor/result-${REQUEST_ID}.html"
```

### Step 6: Copy annotated result to delivery path

Verify the local HTML exists, then copy:
```bash
mkdir -p /tmp/gmail-monitor
cp research-${REQUEST_ID}-results.html /tmp/gmail-monitor/result-${REQUEST_ID}.html
```

If `research-${REQUEST_ID}-results.html` does not exist (generate_html.py failed), write a minimal error HTML to the result path instead:
```html
<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Research Error</title></head>
<body><h1>Legal Research: Report Generation Failed</h1>
<p>Research completed but the report could not be generated. Please try again.</p>
</body></html>
```

### Step 7: Log completion to stdout

```
Email research complete.
Request ID: ${REQUEST_ID}
State: email-queries/research-${REQUEST_ID}-state.json
Result: /tmp/gmail-monitor/result-${REQUEST_ID}.html
```

---

## Important Notes

- **Never call `AskUserQuestion`** — this is a non-interactive command.
- **No synthesizer agent**: HTML is generated by `generate_html.py` from state file data. No LLM re-interpretation.
- **Verbatim rendering**: Case data fields are rendered exactly as returned by analyzers.
- **Citation format**: Bluebook format throughout.
- **One case per analyzer**: Never pass multiple cases to a single case-analyzer agent.
- **State file always current**: Update after every phase via `manage_state.py`.
- **Scripts use `${CLAUDE_PLUGIN_ROOT}`**: All scripts are in the plugin's `scripts/` directory.
- **`depth_preference` is always `"deep"`** in email mode — refinement always runs.
- **Output sequencing is critical**: generate HTML → validate quotes (in-place annotation) → copy to delivery path. Never copy before annotation.
