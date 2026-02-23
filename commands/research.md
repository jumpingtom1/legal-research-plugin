---
description: "Conduct iterative legal research using CourtListener case law database"
argument-hint: "<legal question or research topic>"
allowed-tools: Task, Read, Write, Bash, AskUserQuestion, mcp__plugin_legal_research_courtlistener__lookup_citation, mcp__plugin_legal_research_courtlistener__find_citing_cases
---

# Iterative Legal Research

You are conducting systematic legal research using the CourtListener case law database. Follow these phases in order.

## Context Management

The state file is the primary data store. Your context is ephemeral.

- **The state file (`research-{request_id}-state.json`) is the single source of truth.** Full subagent results go there, not in context.
- **After each phase**, write results to state immediately via `manage_state.py`.
- **In context, retain only summaries**: counts, top candidate names/scores, cluster_id lists.
- **Run `/compact` between phases.**
- **Delegate mechanical work to scripts** in `${CLAUDE_PLUGIN_ROOT}/scripts/`. Never compose HTML manually or manipulate JSON in context when a script can do it.

**Raw input**: $ARGUMENTS

---

## Preflight Check

Run the preflight script:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/preflight.py
```

**This is a hard stop.**

- If the script **exits 0** and prints `PASS:` → API is available. Proceed.
- If the script **exits non-zero** (any other exit code) → **STOP IMMEDIATELY.** Do not proceed to any other phase. Output:

```
ERROR: CourtListener MCP is not available. Legal research cannot proceed.
[paste the full output of preflight.py here]
```

Do not attempt to work around this check or continue the workflow.

---

## Request ID & File Naming

At the start of Phase 0, generate a Request ID:

```bash
python3 -c "import secrets, datetime; print('REQ-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + secrets.token_hex(2))"
```

Store the output as `{request_id}`.

Files: `research-{request_id}-state.json` and `research-{request_id}-results.html`

Tell the user the Request ID and file names.

---

## Phase 0: Structured Query Decomposition

Parse `$ARGUMENTS` into structured elements:

| Element | Description |
|---------|-------------|
| `legal_questions` | Doctrinal/legal questions being asked |
| `fact_pattern` | Specific factual scenario, if any |
| `jurisdiction` | Jurisdiction mentioned or implied |
| `date_range` | Temporal constraints: `{"after": "", "before": ""}` |
| `constraints` | Other constraints (e.g., "state courts only") |
| `query_type` | `"fact"`, `"law"`, or `"mixed"` (see below) |
| `depth_preference` | `"quick"`, `"deep"`, or `"unspecified"` |
| `required_legal_context` | Inferred prerequisites: `party_relationship`, `legal_predicate`, `applicable_test` (each string or null) |

**Extracting `required_legal_context`**: Infer from the fact pattern and legal questions. If the user's scenario presupposes a party relationship (e.g., contracting parties, employer-employee, landlord-tenant) or legal predicate (e.g., duty of care, privity, standing), or a specific legal test (e.g., Pickering balancing, McDonnell Douglas), capture it. If nothing is apparent, set all fields to null. These are hard constraints — a case addressing the same doctrine in a different relational context may be inapposite.

### Query Type Classification

- **Fact**: User wants cases with matching factual scenarios ("find cases where...", "cases involving...")
- **Law**: User wants legal conclusions, rules, standards ("what is the standard for...", "how do courts analyze...")
- **Mixed**: Both facts and legal analysis matter. Default if ambiguous.

### Depth Preference

- **Deep signals**: "deep research", "comprehensive", "thorough", "exhaustive" → `"deep"`
- **Quick signals**: "quick search", "brief overview", "just a few cases" → `"quick"`
- **No signals** → `"unspecified"` (auto-decided after initial results)

### Missing Elements

Use `AskUserQuestion` ONLY for missing critical elements:
- Law queries without jurisdiction → ask
- Fact queries without specific enough facts → ask
- Never ask about optional elements (date range, constraints, depth)

### Display and Save

Show the parsed query table to the user. Write the initial state file:

```json
{
  "request_id": "REQ-YYYYMMDD-HHMMSS-XXXX",
  "parsed_query": { "legal_questions": [], "fact_pattern": "", "jurisdiction": "", "date_range": {}, "constraints": [], "query_type": "", "depth_preference": "", "original_input": "$ARGUMENTS", "required_legal_context": { "party_relationship": null, "legal_predicate": null, "applicable_test": null } },
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

---

## Phase 1: Query Analysis

Launch 1 **query-analyst** agent with the `parsed_query` object. It returns 4-6 search strategies with keyword queries, semantic queries, court filters, and rationale.

Review the strategies. If they miss an obvious angle, add strategies yourself.

Save the strategies to the state file under `search_strategies`.

Display the strategies to the user in a table before proceeding.

---

## Phase 2: Parallel Initial Search

For each search strategy, write its JSON to `/tmp/strategy_{strategy_id}.json`.

Launch all searches in parallel using `search_api.py`:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/search_api.py /tmp/strategy_S1.json > /tmp/search_raw_S1.json &
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/search_api.py /tmp/strategy_S2.json > /tmp/search_raw_S2.json &
# (one line per strategy)
wait
```

**Check each output file for errors.** Read `/tmp/search_raw_{strategy_id}.json` for each strategy:
- If it contains `{"error": ...}`: log via `log_session.py` (level `warn`) and skip that strategy.
- If **ALL** strategies failed: log a fatal error and **STOP** with message: "CourtListener API unavailable — all searches failed."
- If some succeeded: continue with successful results only.

For each successful result, run:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_state.py --state research-{request_id}-state.json \
  add-searches /tmp/search_raw_{strategy_id}.json --round 1
```

**Log search batch performance.** After all `add-searches` calls complete, log timing and result data for this batch:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/log_session.py ingest-search \
  --state-file research-{request_id}-state.json --phase "Phase 2" \
  /tmp/search_raw_S1.json [/tmp/search_raw_S2.json ...]
```

List all `/tmp/search_raw_{strategy_id}.json` files from this batch — include both succeeded and failed ones; the command handles missing or error files gracefully.

**Score new cases.** Read the current `cases_table` from the state file. Launch ONE **case-scorer** agent (model: haiku, tools: none) with this prompt:

> Research question: [paste `parsed_query.original_input`]
>
> Score the following cases for relevance to this research question. For each, provide `initial_relevance` (1-5) and `relevance_note` (one sentence). Return ONLY a JSON array — no preamble, no code fences.
>
> Cases: [paste the `cases_table` entries as a JSON array with fields: cluster_id, case_name, court, date_filed, cite_count, snippet]

Write the agent's output (the JSON array) to `/tmp/case_scores.json`.

**Merge scores into state:**
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

Add an `iteration_log` entry for round 1.

Display a round 1 report: queries executed, result counts, top 5-8 candidates (with scores).

**Session notes**: Log notable conditions using:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/log_session.py note --state-file research-{request_id}-state.json --message "..."
```
Log a note when you observe: low total case count (< 10), unexpectedly narrow or broad results, the `query_type` inference was non-obvious, or any other pattern worth capturing for later review.

**After writing to state, clear context.** Keep only: total case count, top candidate names/cluster_ids, and the list of cluster_ids for Phase 3. Run `/compact`.

---

## Phase 3: Deep Case Analysis

Select the top 8-12 cases for analysis. Run:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_state.py --state research-{request_id}-state.json top-candidates 12
```

Prioritize: highest initial_relevance, highest cite_count, court variety, recency. Log the selection with rationale to the user.

**Pre-fetch opinion text in parallel.** For each selected cluster_id:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_case_text.py {cluster_id_1} > /tmp/fetch_{cluster_id_1}.json &
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_case_text.py {cluster_id_2} > /tmp/fetch_{cluster_id_2}.json &
# (one line per case)
wait
```

Read each `/tmp/fetch_{cluster_id}.json`:
- **Success** (`"error": null`): add to `fetch_ok` list with `{cluster_id, case_name, date_filed, absolute_url, truncated}`
- **Failure** (`"error": "<message>"`): log via `log_session.py` (level `warn`) and skip this case

**Log fetch batch performance:**

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/log_session.py ingest-fetch \
  --state-file research-{request_id}-state.json --phase "Phase 3" \
  /tmp/fetch_{cluster_id_1}.json [/tmp/fetch_{cluster_id_2}.json ...]
```

List all `/tmp/fetch_{cluster_id}.json` files from this batch.

For each case in `fetch_ok`, launch **one case-analyzer agent** in parallel. Pass in the agent prompt:
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
     python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_state.py --state research-{request_id}-state.json \
       add-analysis /tmp/analysis_{cluster_id}.json
     ```
     This also auto-extracts follow-up leads into `pending_leads`.

2. Display per-case results: name, citation, relevance, position, one-line finding.
3. Note new search leads discovered.

**Clear context after writing to state.** Keep only per-case one-line summaries and the new search terms. Run `/compact`.

---

## Phase 3.6: Pivotal Case Detection

After all analyses are merged into state, scan for `pivotal_case` fields in the collected analyses.

Run:
```bash
python3 -c "
import json, sys
state = json.load(open('research-{request_id}-state.json'))
pivotal = []
seen = set()
for case in state.get('analyzed_cases', []):
    pc = case.get('pivotal_case')
    if pc and pc.get('name') and pc['name'] not in seen:
        pivotal.append(pc)
        seen.add(pc['name'])
if pivotal:
    state['pivotal_cases'] = pivotal
    json.dump(state, open('research-{request_id}-state.json', 'w'), indent=2)
    print(json.dumps(pivotal))
else:
    print('[]')
"
```

If the output is non-empty, log to the user:
```
### Pivotal Authority Identified
[Name of pivotal case] — [rule_adopted]
Note: [note from pivotal_case field]
Cases pre-dating [year] will be deprioritized in subsequent rounds.
```

If no pivotal cases are found, no log entry is needed.

---

## Phase 3.5: Automatic Depth Decision

**No user checkpoint.** The system decides automatically based on result quality and lead potential.

Run:
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_state.py --state research-{request_id}-state.json should-refine
```

The script returns a JSON decision:
```json
{"decision": "refine|skip", "reason": "...", "stats": {...}}
```

**Decision rules** (handled by the script):
- `depth_preference === "deep"` → always refine
- `depth_preference === "quick"` → always skip
- `depth_preference === "unspecified"`:
  - **Refine** if fewer than 3 cases with relevance >= 4, OR more than 3 unexplored citation leads
  - **Skip** if 3+ cases with relevance >= 4 AND fewer than 3 unexplored leads

Log the decision and reasoning to the user:
```
### Depth Decision: [Refining / Proceeding to output]
Reason: [reason from script]
High-relevance cases: [N] | Unexplored leads: [N]
[If low_factual_matches triggered: "Low factual match — triggering analogous scenario expansion."]
```

Log the depth decision as a session note:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/log_session.py note --state-file research-{request_id}-state.json --message "Depth decision: [refine|skip] — [reason from script]"
```

Set `workflow_mode` in the state file (`"deep"` if refining, `"quick"` if skipping).

- If **refine**: proceed to Phase 4.
- If **skip**: proceed to Phase 5.

---

## Phase 4: Iterative Refinement

Perform at least one refinement round. This phase uses prior results to drive new searches.

### Step 1: Analyze leads

Run:
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_state.py --state research-{request_id}-state.json get-leads
```

This returns unexplored citation leads and search terms that emerged from analyzed cases but haven't been searched yet.

### Step 2: Generate refined strategies

Based on the leads:
1. For **citation leads**: Use `mcp__plugin_legal_research_courtlistener__lookup_citation` to resolve cited cases. If they return a cluster_id not already in the cases_table, they're candidates for analysis.
2. For **new terminology**: Generate 2-3 new keyword/semantic queries using terms discovered in the analyzed cases.
3. For the 2-3 most important analyzed cases (highest relevance), use `mcp__plugin_legal_research_courtlistener__find_citing_cases` to find recent applications.
4. **If analogous expansion was triggered** (the `should-refine` reason includes "analogous expansion needed"): Launch a second **query-analyst** agent with an explicit instruction: *"The original factual pattern returned few results. Generate 2-3 additional strategies using broader factual framings, analogous party configurations, or the governing legal doctrine for this type of scenario. Tag all strategies as `strategy_type: 'analogous'`."* Incorporate these strategies into the refinement search round.

### Step 3: Execute searches

Write each refined strategy to `/tmp/strategy_{strategy_id}.json`. Launch all `search_api.py` processes in parallel (same pattern as Phase 2), outputting to `/tmp/search_raw_{strategy_id}.json`. After `wait`, check for errors. For successful results:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_state.py --state research-{request_id}-state.json \
  add-searches /tmp/search_raw_{strategy_id}.json --round 2
```

Log search batch performance (list all output files from this round):
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/log_session.py ingest-search \
  --state-file research-{request_id}-state.json --phase "Phase 4" \
  /tmp/search_raw_{strategy_id}.json [...]
```

After merging new cases: identify the **new cluster_ids** (those not yet in `cases_table` before this round). Launch ONE **case-scorer** agent for new cases only, passing the existing analyzed-case names and scores as calibration context. Write output to `/tmp/case_scores.json` and merge into state using the same python snippet as Phase 2.

### Step 4: Analyze new finds

Run `manage_state.py top-candidates 8` to get the best unanalyzed cases. Pre-fetch opinion text in parallel using `fetch_case_text.py` for each candidate (same pattern as Phase 3). Log fetch batch performance after the `wait`:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/log_session.py ingest-fetch \
  --state-file research-{request_id}-state.json --phase "Phase 4" \
  /tmp/fetch_{cluster_id}.json [...]
```
Launch **case-analyzer** agents for the top 5-8 cases that fetched successfully. Merge via `manage_state.py add-analysis`.

### Step 5: Check for another round

Run both checks (N = current round number, e.g., 2 for the first refinement round):
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_state.py --state research-{request_id}-state.json check-diminishing-returns --round N
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_state.py --state research-{request_id}-state.json get-leads
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
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_state.py --state research-{request_id}-state.json mark-explored /tmp/explored.json
```

Log refinement results: new terms discovered, strategy productivity, new cases found and analyzed.

Run `/compact` before Phase 5.

---

## Phase 5: Output

**Do NOT compose HTML manually.** Use the script.

### Step 1: Generate summary

Run:
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/manage_state.py --state research-{request_id}-state.json summary
```

Display the summary to the user: total queries, total cases, analyzed count, relevance distribution.

### Step 2: Generate Summary Answer

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

### Step 3: Generate HTML report

Run:
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/generate_html.py research-{request_id}-state.json
```

This reads the state file and produces `research-{request_id}-results.html` with all sections: About, Index, Query, Summary, Authorities, All Results, Search Process. All case data is rendered verbatim from the state file.

### Step 4: Quote validation

Run:
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/run_quote_validation.py research-{request_id}-state.json --annotate
```

This script:
1. Checks for opinion text files saved by case-analyzer agents (`/tmp/vq_opinion_{cluster_id}.txt`)
2. Runs the three-tier matcher against each excerpt
3. Annotates the HTML with verification labels
4. Updates the state file with validation results
5. Outputs a summary

If the script reports missing opinion files, run `fetch_case_text.py` for each missing cluster_id:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_case_text.py {cluster_id} > /tmp/fetch_{cluster_id}.json
```
Then rerun the validation script.

Display the quote validation summary to the user.

### Step 5: Write session log

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/log_session.py summary --state-file research-{request_id}-state.json --log-file ./legal-research-sessions.jsonl --mode interactive --output-file "$(pwd)/research-{request_id}-results.html"
```

### Step 6: Present results

```
Request ID: {request_id}
Results saved to: research-{request_id}-results.html (open in browser)
State saved to: research-{request_id}-state.json
To expand: /legal-research:research-continue {request_id} "<refinement direction>"
```

---

## Important Notes

- **No synthesizer agent**: HTML is generated by `generate_html.py` from state file data. No LLM re-interpretation.
- **Verbatim rendering**: Case data fields are rendered exactly as returned by analyzers.
- **Citation format**: Bluebook format throughout. Case-analyzer produces these — render as-is.
- **One case per analyzer**: Never pass multiple cases to a single case-analyzer agent.
- **State file always current**: Update after every phase via `manage_state.py`.
- **Scripts use `${CLAUDE_PLUGIN_ROOT}`**: All scripts are in the plugin's `scripts/` directory.
