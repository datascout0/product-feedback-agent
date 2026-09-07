# Agent Progress Log — Group 1 (Stages 10-12)

This file tracks autonomous progress on branch `agent/finish-mvp` for Group 1
only: deterministic validation/scoring, Gemini critic, and the bounded
conditional revision flow. Do not use this file as a substitute for
`docs/results-log.md` (measured results) or `docs/decisions.md` (locked
decisions) -- it exists so work can resume safely across context/usage
limits.

---

## Stage 10 — Deterministic validation + scoring

**Status:** Complete

### Files added

- `src/product_feedback_agent/validation.py`
- `tests/test_validation.py`

### What it does

`validate_analyst_output(output: AnalystOutput, packet: AnalysisContextPacket) -> ValidationResult`
is a pure function (no client parameter, no I/O) that checks every
`OpportunityDraft` against the `AnalysisContextPacket` it was actually built
from:

- rejects feedback IDs (in `feedback_ids` and `representative_evidence`) not
  present in `retrieved_feedback` / `diverse_feedback_sample`
- rejects non-exact `representative_evidence` excerpts (must be an exact
  substring of the cited feedback text)
- rejects `related_context` citations with an unknown `document_id`, unknown
  `section_id`, or a `document_id`/`section_id` pair that doesn't match any
  entry in `retrieved_context`
- rejects `affected_segments` values not supported by the `segment` metadata
  of the opportunity's own *valid* cited feedback -- and explicitly flags the
  case where the value is actually a `product_area` in the dataset (the real
  "mobile"/"reporting" finding from the 2026-09-07 Gemini smoke test)
- computes `evidence_count` locally from deduplicated, packet-valid
  `feedback_ids` only (never trusts the model)
- computes the final opportunity score locally via `compute_score(...)` using
  the locked weights (evidence_strength 30%, severity 25%, strategic_alignment
  20%, segment_breadth 15%, confidence 10%); `roadmap_status` never
  participates in scoring

All findings are structured `ValidationIssue` entries (`opportunity_id`,
`issue_type`, `message`, plus whichever of `feedback_id`/`document_id`/
`section_id`/`segment` applies) -- nothing is silently repaired.

### Tests

`tests/test_validation.py` — 25 tests, all offline, covering every case
listed in the task brief plus a skip-if-absent regression test that replays
the real Gemini smoke output (`data/local/analyst-smoke.json` +
`data/local/analysis-context-cache/e4cfd...json`, both gitignored) and
asserts `"mobile"` and `"reporting"` are flagged as unsupported segments.
This optional test is `pytest.skip`-ped, not required, when those local files
are absent, so the normal suite never depends on ignored local state.

### Test result

Full suite: **162 / 162 passed** (137 baseline + 25 new).

### Unresolved issues / human validation needed

None for Stage 10 itself. The scoring formula and weights are locked per
`docs/decisions.md`; if that changes, `compute_score` and its tests must be
updated together.

---

## Stage 11 — Gemini critic

**Status:** Complete

### Files added

- `src/product_feedback_agent/critic.py`
- `tests/test_critic.py`

### What it does

`run_critic(packet, analyst_output, validation_result, client=None) -> CriticOutput`
mirrors `analyst.py`'s engineering pattern exactly: lazy `GeminiCriticClient`
(no `google.genai` import, no env read, no network at import time or in
`__init__`), dependency-injected `CriticClient` protocol for tests, at most
one model request per call, no retry loop, and non-secret model-call tracing
(`stage="critic"`, `model`, `cache_key`, `success`, `duration_seconds`,
`usage` when available) to `logs/model-calls.jsonl`.

The critic prompt (`build_critic_prompt`) is built from all three required
inputs: the original `AnalysisContextPacket`, the analyst's `AnalystOutput`
draft, and the Stage 10 `ValidationResult` -- so the critic reviews
reasoning quality (evidence support, duplicate opportunities, ignored
contradictions, roadmap/strategy overreach, rationale quality,
feature-vs-need confusion, confidence overstatement, next-step fit) on top
of, not instead of, the deterministic checks.

`CriticOutput` is structured exactly as specified: `status` (`pass`/`revise`
enum), `issues` (typed `CriticIssueType` enum + message + opportunity_id),
`unsupported_claims`, `citation_errors`, `duplicate_opportunities`,
`required_revisions`. A Pydantic model-validator enforces that
`status="revise"` requires at least one `required_revisions` entry. The
schema has no field that could let the critic decide a roadmap, create a
GitHub issue, or change a permission -- `test_critic_output_has_no_roadmap_or_action_fields`
locks the exact field set.

### Tests

`tests/test_critic.py` — 25 tests, all offline (fake clients; real
`google.genai` is monkeypatched into `sys.modules` for the "real client"
tests, never actually imported/network-reachable). Covers schema
constraints/enums, prompt content, the one-request boundary, error paths,
lazy API-key loading (subprocess import test mirroring analyst.py's), and
that no API key ever leaks into trace logs.

### Test result

Full suite: **187 / 187 passed** (162 from Stage 10 + 25 new).

### Unresolved issues / human validation needed

No real Gemini critic call has been made yet (by design -- Group 1 stays
offline unless a human explicitly requests a real smoke test, same rule
applied to the Stage 9 analyst).

---

## Stage 12 — One conditional revision

**Status:** Complete

### Files added

- `src/product_feedback_agent/pipeline.py`
- `tests/test_pipeline.py`

### What it does

`run_pipeline(packet, *, analyst_client=None, critic_client=None, revision_client=None) -> PipelineResult`
is the bounded Group 1 orchestration function:

1. `run_analyst(packet, ...)` — request 1.
2. `validate_analyst_output(...)` — deterministic, no request.
3. `run_critic(packet, analyst_output, validation_result, ...)` — request 2.
4. If `critic_output.status == "pass"`: return immediately.
   `model_request_count == 2`, `revised == False`. The revision client is
   never invoked.
5. If `critic_output.status == "revise"`: make exactly one
   `run_revision(...)` request (request 3), reusing `AnalystOutput`/
   `OpportunityDraft` as the revision's output schema, then re-run
   `validate_analyst_output(...)` locally against the revised opportunities.
   `model_request_count == 3`, `revised == True`. The critic is never called
   a second time -- this matches the locked V1 architecture. Any
   deterministic issues still present after the single revision remain in
   the returned `validation_result.issues` for human review; they are never
   hidden and never trigger another model call.

`run_revision` mirrors `analyst.py`/`critic.py`'s pattern exactly: a
`GeminiRevisionClient` that is lazy (no `google.genai` import, no env read,
no network at import time), a dependency-injected client protocol
(`RevisionClient = AnalystClient`, since the shape is identical), one
request with no retry, and non-secret tracing with `stage="revision"`.

There is no loop anywhere in this module. The only two code paths are
"return after critic pass" and "return after exactly one revision" -- a 4th
request is not structurally reachable.

### Tests

`tests/test_pipeline.py` — 13 tests, all offline (fake clients for analyst,
critic, and revision). Covers: exactly 2 requests on critic pass, exactly 3
requests on critic revise, revision client never invoked on a pass, critic
never invoked twice, no path exceeds 3 total requests (checked for both
pass and revise branches), revised output is re-validated against the
original packet (equality-checked against a fresh direct
`validate_analyst_output` call), and a case where the "revised" draft still
contains an unsupported segment -- confirming that remaining issue is
surfaced in the final `validation_result.issues` rather than hidden or
triggering a second critic/revision call.

### Test result

Full suite: **200 / 200 passed** (187 from Stage 11 + 13 new).

### Unresolved issues / human validation needed

No real Gemini analyst/critic/revision run of the full bounded pipeline has
been made yet -- Group 1 stays offline by design unless a human explicitly
requests a real end-to-end smoke test (consistent with the Stage 9 analyst
smoke test being a deliberate, separate, human-triggered step). A real
end-to-end smoke run (analyst -> critic -> conditional revision against a
live packet) is recommended before Group 2 work, to confirm real Gemini
critic/revision output actually validates against the `CriticOutput`/
`AnalystOutput` schemas the way the offline fixtures do.

---

## Stage 12b — Live smoke finding + critic contract fix

**Status:** Complete (fix only; no successful full live pipeline run claimed)

### Live finding

A real `run_pipeline(packet)` smoke run made an analyst request and a critic
request. The critic (Gemini) returned `status="revise"` with
`required_revisions=[]`. `CriticOutput`'s existing model-validator (added in
Stage 11) correctly rejected this as invalid, so `run_critic` raised
`CriticError` and the run stopped before the revision request was made. No
required revisions were fabricated in Python and the run was not silently
converted to a pass -- this is the correct, intended behavior of the
existing Pydantic invariant.

### Fix

Only `_CRITIC_INSTRUCTIONS` in `src/product_feedback_agent/critic.py` was
changed -- the `CriticOutput` schema and its `revise => non-empty
required_revisions` validator are unchanged. The prompt now explicitly
states, as separate rules: `pass` only when nothing requires changes;
`revise` whenever anything requires changes, including deterministic
validation findings (unsupported segments, invalid IDs, non-exact
quotations) that need an analyst-side correction; every listed `issues`
entry must have a matching `required_revisions` entry; and status="revise"
with an empty `required_revisions` is explicitly prohibited and described as
invalid, with instructions to return "pass" instead if no concrete revision
can be named.

No retries were added, the 2/3 Gemini request budget in `pipeline.py` is
unchanged, and no fake revisions are synthesized in Python.

### Tests

Added to `tests/test_critic.py`: prompt contains the explicit `revise =>
non-empty required_revisions` rule, the prompt explicitly prohibits
`revise` + empty `required_revisions`, the prompt requires deterministic
findings to drive `revise` + revisions, and the prompt requires every issue
to have a matching required-revision entry. The existing
`test_revise_status_without_required_revisions_is_rejected` and
`test_run_critic_raises_on_invalid_client_output` tests continue to prove
the schema-level rejection is unchanged; `test_revise_status_with_
required_revisions_is_valid` and `test_run_critic_returns_revise_output`
continue to prove a valid revise response still passes. All
`tests/test_pipeline.py` request-count tests (2 requests on pass, 3 on
revise, no path exceeds 3) pass unchanged, since `pipeline.py` was not
modified.

### Test result

Full suite: **204 / 204 passed** (200 baseline + 4 new prompt regression
tests).

### Unresolved issues / human validation needed

No successful full live `run_pipeline` run has been completed yet. One
controlled live retry (a single real smoke run, same budget: analyst +
critic, plus revision only if triggered) is recommended to confirm the
strengthened prompt produces a schema-valid `revise` response before
Group 2 work begins.

---

## Stage 12c — Second live critic failure + schema-level fix

**Status:** Complete (fix only; full live Group 1 success is NOT claimed)

### Live finding

A second real `run_pipeline(packet)` smoke run reached the analyst and
critic requests again. The critic (real Gemini) returned `status="revise"`
with `required_revisions=[]` again -- the identical invalid shape from
Stage 12b, despite the Stage 12b prompt strengthening explicitly
prohibiting it. `CriticOutput`'s model-validator again correctly rejected
it and `run_critic` raised `CriticError` before any revision request was
made.

### Conclusion

Two live occurrences of the same invalid shape, the second after an
explicit prompt-level prohibition, show that prompt-only enforcement of the
`status="revise"` <=> non-empty `required_revisions` relationship is
insufficient. The invariant needed to move into the JSON Schema boundary
sent to Gemini's structured-output API, which the model is far more
strongly bound to follow than free-text prompt instructions.

### Fix

`src/product_feedback_agent/critic.py` now defines a wire (structured-output)
schema layer, separate from the existing domain `CriticOutput`:

- `CriticPassResponse` (status: `Literal["pass"]`, `required_revisions`
  constrained to `max_length=0`)
- `CriticReviseResponse` (status: `Literal["revise"]`, `required_revisions`
  constrained to `min_length=1`, and required rather than defaulted)
- `CriticWireResponse = CriticPassResponse | CriticReviseResponse`
- `build_critic_response_schema()` returns
  `TypeAdapter(CriticWireResponse).json_schema()` -- an `anyOf` of the two
  alternatives, each with a `$defs` entry carrying `required_revisions`'
  `minItems`/`maxItems` constraint directly in the schema sent to Gemini.

`run_critic` now sends `build_critic_response_schema()` (not
`CriticOutput.model_json_schema()`) to the critic client, parses the raw
response through `TypeAdapter(CriticWireResponse)` first (so a
structurally-invalid `revise` + `[]` or `pass` + non-empty response is
rejected at that layer), and then re-validates the parsed result through
the existing `CriticOutput` domain model before returning it. `CriticOutput`
and its `model_validator` are unchanged and unremoved -- this is
defense-in-depth, not a replacement. The rest of the pipeline
(`pipeline.py`, `PipelineResult`, the 2/3 request budget) is untouched.

The Stage 12b prompt strengthening is kept as an additional instruction
layer; schema enforcement is now the primary repair.

### Tests

Added to `tests/test_critic.py` (9 new tests): the response schema exposes
exactly two `anyOf` alternatives named `CriticPassResponse`/
`CriticReviseResponse`; the revise alternative has `minItems: 1` and marks
`required_revisions` required; the pass alternative has `maxItems: 0`;
valid pass and valid revise (with a revision) payloads parse through the
wire `TypeAdapter`; a revise + `[]` payload and a pass + non-empty payload
are both rejected at the wire layer; the literal live-failure shape
(`revise` + `[]`) is still rejected end-to-end through `run_critic`; and
`CriticOutput.model_validate` still rejects the same shape directly,
confirming the original guardrail was not removed. Updated
`test_run_critic_passes_response_schema_to_client` and the real-client
happy-path test to assert against `build_critic_response_schema()` instead
of the now-unused `CriticOutput.model_json_schema()` as the schema sent
over the wire.

### Test result

Full suite: **213 / 213 passed** (204 baseline + 9 new).

### Unresolved issues / human validation needed

No successful full live pipeline run has been completed yet. Full live
Group 1 success is NOT claimed. One controlled live retry (a single real
smoke run, same 2/3 request budget) is recommended next to confirm the
schema-enforced critic response is accepted by real Gemini output before
any Group 2 work begins.

---

## Stage 12d — Live validation: successful full pipeline run

**Status:** Complete. Group 1 (Stages 10-12) is now live-validated.

### Result

The single controlled live retry recommended in Stage 12c was run. A real
`run_pipeline(packet)` call completed the full bounded flow successfully:
analyst (request 1) -> deterministic validation -> critic (request 2,
schema-valid `status="revise"` this time, satisfying the Stage 12c wire
schema) -> exactly one revision (request 3) -> local re-validation of the
revised output, with **0** remaining deterministic validation issues.

`model_request_count = 3`, `revised = True`, no fourth model request
occurred. Full model-call traces, per-call token/duration figures, and the
measured totals (3 requests, 135.485s total call duration, 24,959 total
reported tokens) are recorded in `docs/results-log.md` under "2026-09-07 —
Live Group 1 Smoke: Successful Full Pipeline Run". The raw run output was
saved to the gitignored local artifact `data/local/group1-live-smoke.json`.

This is a live-only finding: no implementation code was changed for this
entry. The two prior live failures (Stage 12b, Stage 12c) that motivated
the prompt- and schema-level critic fixes remain recorded above and are not
erased -- this run confirms those fixes hold under a real Gemini call.

### Tests

No test or implementation changes accompany this entry. The existing
213/213 offline suite is unaffected; this was a live smoke run only.

### Unresolved issues / human validation needed

None for Group 1. Stages 10-12 are complete and live-validated. Group 2 has
not been started.

---

# Agent Progress Log — Group 2 (Stages 13-15)

This section tracks autonomous progress on branch `agent/finish-mvp` for
Group 2: end-to-end application orchestration, human-approved GitHub
opportunity issue creation, and the n8n human-approval workflow. Group 1
(Stages 10-12, above) is complete and live-validated and is not modified in
this group except where explicitly noted.

## Stage 13 — End-to-end application orchestration

**Status:** Complete

### Files added

- `src/product_feedback_agent/orchestrator.py`
- `tests/test_orchestrator.py`

### What it does

`run_analysis(feedback_file, context_files, analysis_query, ...) -> OrchestrationResult`
is the reusable application-level orchestration layer above the existing
Group 1 pipeline. It sequences, without duplicating any of their logic:

1. `load_feedback_file` / `load_context_file` (existing ingestion).
2. `prepare_analysis_context` -- called exactly once per `run_analysis` call.
3. `run_pipeline` (existing, unmodified Group 1 analyst -> deterministic
   validator -> critic -> optional single revision).

All inputs (feedback file, context files, query, top-k/sample-size,
cache dir, Chroma persistence dir) are explicit function/CLI arguments; no
path is hard-coded. `retrieval`/`analyst_client`/`critic_client`/
`revision_client` are injectable, matching the existing dependency-injection
pattern in `analyst.py`/`critic.py`/`pipeline.py`; production callers can
omit all four.

`OrchestrationResult` is a `ConfigDict(extra="forbid")` Pydantic model
containing exactly: `cache_key`, `opportunities` (`min_length=3,
max_length=3`), `validation_result`, `critic_output`, `revised`,
`model_request_count`, `unresolved_issue_count`, `human_review_status`. It
has no field that could represent a completed or automatic external action
(no issue URL, no "approved" flag). `human_review_status` is a one-value
enum (`HumanReviewStatus.PENDING_REVIEW`) -- nothing in this module can set
it to anything else, since approval is a separate, explicitly human-driven
step (Stage 14/15). This module makes no GitHub call and imports nothing
GitHub-related.

A CLI/demo entry point (`main()`, registered as the `product-feedback-agent`
console script in `pyproject.toml`) runs one analysis, prints the structured
JSON result, and prints an explicit "HUMAN REVIEW REQUIRED" notice. It takes
no approval action itself.

### Tests

`tests/test_orchestrator.py` -- 16 tests, all offline (a `FakeRetrieval`
that indexes real ingested `FeedbackRecord`/`ContextDocument` objects and
returns them verbatim, plus the existing `FakeClient` pattern from
`test_pipeline.py`). Covers: `prepare_analysis_context` invoked exactly once
per run (asserted via the fake retrieval's index/search call counters);
exactly 3 opportunities returned; the `OrchestrationResult` field set is
locked and rejects an injected `approved`/`github_issue_url` field; pass-run
metadata (`revised=False`, `model_request_count=2`) and revise-run metadata
(`revised=True`, `model_request_count=3`) both propagate correctly; no
execution path exceeds 3 total model requests; unresolved deterministic
issues after revision propagate into `unresolved_issue_count`;
`human_review_status` is always `PENDING_REVIEW` and the enum has no other
value; the module imports nothing GitHub-related; `OrchestrationError` is
raised (not swallowed) for zero valid feedback, an unsupported context file
extension, and a blank query; importing the module never loads
`sentence_transformers`; and the CLI `main()` prints the JSON result plus
the human-review notice on success and returns a non-zero exit code with a
clear message on `OrchestrationError`.

### Test result

Command:

`.\.venv\Scripts\python.exe -m pytest -q`

Result: **229 / 229 passed** (213 baseline + 16 new). 0 failures, 0 errors,
no network or Gemini requests made.

### Unresolved issues / human validation needed

None for Stage 13 itself. No real Gemini call was made or is needed here --
this stage only adds orchestration around the already live-validated Group 1
pipeline; fake clients are sufficient to prove the wiring is correct.

---

## Stage 14 — Safe GitHub opportunity issue creation

**Status:** Complete (offline only; no real GitHub issue was created)

### Files added

- `src/product_feedback_agent/github_action.py`
- `tests/test_github_action.py`
- `src/product_feedback_agent/mcp_server.py` (new `create_opportunity_issue` tool)
- `tests/test_mcp_server.py` (new tool's tests; removed the now-superseded
  `test_create_opportunity_issue_tool_not_present_yet`, updated
  `test_expected_tools_are_registered`)

### What it does

`create_opportunity_issue(result: OrchestrationResult, opportunity_id, repo,
*, approved=False, dry_run=True, allowed_repos=None, client=None) ->
IssueActionResult` in the new `github_action.py` contains no analysis logic
-- it only renders an issue payload from an already-produced Stage 13
`OrchestrationResult` and, only when explicitly approved, submits it:

1. `render_issue_payload` builds the exact issue title/body/labels purely
   from fields already present on the supplied opportunity and its matching
   `ValidatedOpportunity` -- problem statement, underlying need, affected
   segments, representative evidence quotes with `feedback_id`, the locally
   computed `evidence_count`, the full deterministic score breakdown,
   roadmap/strategy context, contradicting evidence, evidence gaps,
   unresolved deterministic validation notes, recommended next step, and
   the run's `cache_key`. Nothing is fabricated; nothing not already on the
   result is added.
2. A deterministic idempotency marker (`build_idempotency_marker`, a SHA-256
   digest of `cache_key:opportunity_id`) is embedded as an HTML comment in
   every rendered body.
3. `create_opportunity_issue` checks, in order: the target `repo` is in the
   allow-listed set (explicit `allowed_repos`, or else the lazily-read
   `GITHUB_DEMO_REPO` env var, comma-separated; empty/unset means nothing is
   allowed) -> `approved is True` -> `dry_run is False`. Any failing check
   returns a structured `IssueActionResult` (never raises) with the payload
   still attached, and makes no client call. Only once all three pass does
   it call `client.find_issue_by_marker(...)`; a match refuses creation as
   `refused_duplicate` instead of filing a second issue. Only then does it
   call `client.create_issue(...)`.
4. `GitHubRestIssueClient` (the real, injectable-away implementation) uses
   only the Python standard library (`urllib.request`) -- no new
   dependency. It mirrors `analyst.py`/`critic.py`/`pipeline.py`'s lazy-
   credential pattern exactly: `GITHUB_TOKEN` is read only inside
   `_headers()`, never in `__init__` or at import time, and is never
   included in a raised exception message (`type(exc).__name__` only, same
   rule as the Gemini clients) or in the non-secret trace log written to
   `logs/github-actions.jsonl` (gitignored, mirroring `logs/model-calls.jsonl`).

`create_opportunity_issue` is also exposed as an MCP tool in
`mcp_server.py`, following the file's existing "thin wrapper" design: it
takes the same arguments (`orchestration_result` as a nested Pydantic
object, `opportunity_id`, `repo`, `approved`, `dry_run`, `allowed_repos`)
and wraps `GitHubActionError` as `ToolError`, exactly like the two existing
tools wrap `IngestionError`/`ContextIngestionError`/`ValueError`.

### Tests

`tests/test_github_action.py` -- 21 tests, all offline. Covers: rendered
payload contains every required field and no more; the idempotency marker
is deterministic and unique per (cache_key, opportunity_id); an unknown
`opportunity_id` raises `GitHubActionError` rather than silently rendering
nothing; `approved=False` (explicit and default-omitted) blocks the write
with zero client calls; `dry_run=True` (the default) makes zero client
calls even when `approved=True`, while still returning the rendered
payload; an unsupported repo is blocked with zero client calls; an allowed
repo is accepted and creates an issue via the injected fake client; the
default allow-list is read from `GITHUB_DEMO_REPO` (comma-separated) and is
empty (nothing allowed) when unset; a pre-existing idempotency marker
blocks a duplicate `create_issue` call; `IssueActionResult`'s field set is
locked; importing the module in a fresh subprocess with `GITHUB_TOKEN`
stripped succeeds; constructing `GitHubRestIssueClient()` touches neither
env nor network; a missing token raises a clear `GitHubActionError` whose
trace log never references a token value; and, with `urllib.request.urlopen`
mocked (no real network), a full success path (create + find-no-match) and
a simulated network failure both prove the real token value never appears
in a raised exception message or the trace log file.

`tests/test_mcp_server.py` -- `test_expected_tools_are_registered` now
includes `create_opportunity_issue`; 5 new tests cover the tool blocking an
unapproved write, a dry run producing the rendered payload with zero
network calls, an unsupported repo being blocked, a fully mocked success
path (`GitHubRestIssueClient` monkeypatched to a fake, proving the MCP path
never reaches the real network either), and a clear error for an unknown
`opportunity_id`.

### Test result

Command:

`.\.venv\Scripts\python.exe -m pytest -q`

Result: **254 / 254 passed** (229 baseline + 21 new github_action tests + 4
net new/changed mcp_server tests). 0 failures, 0 errors, no network or
Gemini requests made, and NO real GitHub API call was made anywhere in this
suite (all `GitHubRestIssueClient` network calls are monkeypatched).

### Unresolved issues / human validation needed

No real GitHub issue was created during this stage, by design -- per the
task instructions, autonomous implementation must not create a real GitHub
issue. "Create one approved real GitHub issue" remains unchecked in
`docs/implementation-checklist.md`'s GitHub REST Action section and is left
as an explicit, separate, human-approved verification step: a human must
set `GITHUB_TOKEN` and `GITHUB_DEMO_REPO` (or pass `allowed_repos`
explicitly) and call `create_opportunity_issue(..., approved=True,
dry_run=False)` themselves against a real allow-listed demo repository.

---

## Stage 15 — n8n + human approval workflow

**Status:** Complete as a static artifact. **HUMAN CHECKPOINT REQUIRED**
for live n8n import/execution -- not performed by Claude Code this session
(no live n8n instance was reachable).

### Files added

- `n8n/workflows/product-feedback-agent.json`
- `docs/n8n-setup.md`
- `tests/test_n8n_workflow.py`
- CLI changes to support clean piping between stages (see below):
  `src/product_feedback_agent/orchestrator.py` (JSON to stdout only, human
  notice moved to stderr), `src/product_feedback_agent/github_action.py`
  (new `main()` CLI entry point), `pyproject.toml` (new
  `product-feedback-agent-github-action` console script).

### What it does

The workflow represents exactly the required flow: Intake Form -> Build
Analysis Command -> Run Analysis (Python CLI) -> Parse Orchestration Result
-> Render Review Summary -> Human Review & Approval (Form) -> Approval Gate
(If) -> **approved path**: Build GitHub Action Command -> Create GitHub
Issue (Approved) -> Parse GitHub Action Result -> Approved Summary;
**rejected/anything-else path**: No Action (Rejected), a dead end with no
outgoing connection.

No core product logic is duplicated in n8n expressions: the "Run Analysis"
and "Create GitHub Issue" nodes are `n8n-nodes-base.executeCommand` nodes
that shell out to the existing, unmodified Stage 13 `orchestrator` CLI and
Stage 14 `github_action` CLI respectively. Every other node is pure
JSON formatting/branching on their output. The Approval Gate requires all
three of: `Decision == "approve"` (case-sensitive), a non-blank
`opportunity_id`, and a non-blank target `repo` -- anything else (reject,
blank, malformed) routes to the dead-end rejection node. Even past the
gate, the `github_action` CLI independently re-checks `approved`/
`dry_run`/the repo allow-list/the duplicate marker before any GitHub
request, so n8n misconfiguration cannot itself force a write.

Two small CLI changes from Stage 13/14 were needed to make the pipe-through-
n8n design work cleanly, and were made deliberately as part of this stage:

1. `orchestrator.main()` now writes ONLY the JSON `OrchestrationResult` to
   stdout; the "HUMAN REVIEW REQUIRED" notice moved to stderr. This means
   an `Execute Command` node's `stdout` field is directly `JSON.parse`-able
   by the next node, with no string-stripping required. `tests/test_orchestrator.py`'s
   CLI tests were updated to check `captured.err`/`captured.out` accordingly
   (still asserting the notice is printed, just on the correct stream).
2. `github_action.py` gained a `main()` CLI entry point (`--result-file`,
   `--opportunity-id`, `--repo`, `--approved`, `--no-dry-run`,
   `--allowed-repo`, repeatable) mirroring `orchestrator.main()`'s design:
   JSON result to stdout only, errors to stderr, non-zero exit only for a
   genuine failure (bad input file, unknown opportunity_id, or a real
   GitHub request failure) -- never for an ordinary refusal, which is
   reported in the printed result's `status` field instead.

### Tests

`tests/test_n8n_workflow.py` -- 13 tests, pure static analysis of the
workflow JSON (no n8n dependency, no network): the file parses as JSON;
every connection's source/target resolves to a real node; every non-trigger
node has an incoming connection; every node is reachable from the intake
form; the rejection node has zero outgoing connections; **the core safety
property** -- removing only the Approval Gate's "approve" edge makes the
GitHub-issue node unreachable from the intake form entirely (proving there
is no second, hidden path to a GitHub mutation); the Approval Gate's
conditions require the literal case-sensitive string `"approve"` plus a
non-blank opportunity_id and a non-blank repo; the `github_action` CLI
(with `--approved --no-dry-run`) is invoked from exactly one node, never
any other; the `orchestrator` CLI's command-building node never includes a
GitHub-related flag; the workflow file contains no hard-coded Windows user
path (`C:\Users`, `C:/Users`, `/home/...`); `PFA_PROJECT_ROOT` is referenced
only via an `$env.` lookup, never assigned a literal path in the JSON; and
the file contains no `GITHUB_TOKEN`/`GEMINI_API_KEY`/token-shaped literal.

`tests/test_orchestrator.py`'s two CLI tests were updated (not weakened) for
the stdout/stderr split above; both still assert the same content, on the
now-correct stream.

### Test result

Command:

`.\.venv\Scripts\python.exe -m pytest -q`

Result: **271 / 271 passed** (258 baseline + 13 new n8n-workflow tests).
0 failures, 0 errors, no network or Gemini requests made, and no real n8n
instance or GitHub API was reached anywhere in this suite.

### Unresolved issues / human validation needed

**HUMAN CHECKPOINT REQUIRED** (per task instructions, since Claude Code has
no access to a live localhost n8n instance):

1. Import `n8n/workflows/product-feedback-agent.json` into a real n8n
   instance and confirm every node opens without a schema-mismatch/
   migration warning for your installed n8n version (node-parameter field
   names, especially for the Form/Form-Trigger/If/Set nodes, were written
   from the n8n workflow-JSON schema at authoring time and were not
   round-tripped through a live n8n import).
2. Set `PFA_PROJECT_ROOT` (and optionally `PFA_PYTHON`), `GEMINI_API_KEY`,
   and `GITHUB_TOKEN` on the n8n host, per `docs/n8n-setup.md`.
3. Run the reject path once (expect a dead end, no GitHub call) and the
   approve path once against a real, disposable demo repository you
   control (expect a real GitHub issue -- this is the explicit,
   deliberate, human-approved verification step called for in both Stage
   14 and Stage 15's task instructions; it was intentionally not performed
   autonomously).

No real GitHub issue and no live n8n execution occurred during this
session.

---

## Stage 15 repair — Live n8n finding + HTTP boundary replacement

**Status:** Complete (offline repair only; live re-import/re-test NOT yet
performed).

### Live finding (2026-09-07)

The Stage 15 workflow was imported into the user's real self-hosted n8n
instance (Docker, image `docker.n8n.io/n8nio/n8n`, container `n8n`, port
`5678:5678`). Import succeeded, but both nodes intended as "Run Analysis
(Python CLI)" and "Create GitHub Issue (Approved)" -- both
`n8n-nodes-base.executeCommand` -- appeared as unrecognized "?" nodes:
"Install this node to use it. This node is not currently installed. It is
either from a newer version of n8n, a custom node, or has an invalid
structure." Separately, the Product Feedback Agent Python repo/`.venv` runs
on the Windows host, not inside the n8n container -- so even with Execute
Command enabled, a command run there would execute inside the container,
not against this project's `.venv`. The Execute Command design was not
viable for this local architecture.

### Repair: HTTP boundary

Replaced the Execute Command integration boundary with an HTTP boundary:
`n8n (Docker) -> HTTP Request node -> small Product Feedback Agent HTTP
service on the Windows host -> existing orchestrator.py / github_action.py
(unmodified)`. Docker Desktop containers reach the Windows host at
`host.docker.internal`.

### Files added/changed

- `src/product_feedback_agent/api.py` (new) -- a small Starlette + uvicorn
  HTTP service. Both packages were already transitive dependencies of
  `mcp[cli]` (starlette) and `mcp[cli]`/`chromadb` (uvicorn); no new
  third-party package was installed, they were only formalized as direct
  dependencies in `pyproject.toml`, alongside a new
  `product-feedback-agent-api` console script.
- `n8n/workflows/product-feedback-agent.json` -- the two
  `n8n-nodes-base.executeCommand` nodes were replaced with
  `n8n-nodes-base.httpRequest` nodes; the two shell-command-building Code
  nodes were replaced with JSON-request-body-building Code nodes (same
  data transformation, different output shape). `PFA_PROJECT_ROOT`/
  `PFA_PYTHON` are no longer referenced anywhere in the workflow.
- `docs/n8n-setup.md` -- rewritten for the two-terminal local sequence
  (start the HTTP service, then use n8n already running in Docker) and the
  `host.docker.internal:8000` addressing explanation.
- `tests/test_api.py` (new), `tests/test_n8n_workflow.py` (updated).

### What `api.py` does

`GET /health` returns `{"status": "ok"}`. `POST /analyze` validates an
explicit `AnalyzeRequest` (feedback_file, context_files, analysis_query,
top-k/sample-size), resolves relative paths against the project root
(mirroring `mcp_server.py`'s existing convention, not the process cwd),
calls the existing Stage 13 `run_analysis` unmodified, and returns its
`OrchestrationResult` as JSON -- it imports `create_opportunity_issue` only
for the other route and never calls it. `POST /github/issues` validates an
explicit `GitHubIssueRequest` (result, opportunity_id, repo, approved,
dry_run, allowed_repos) and delegates directly to the existing Stage 14
`create_opportunity_issue` unmodified -- every existing safety control
(`approved=True` required for mutation, `dry_run=True` default, the repo
allow-list, duplicate-marker prevention) is preserved because this layer
never reimplements them. No secret is read, logged, or returned anywhere in
this module. The service binds `0.0.0.0:8000` by default (configurable via
`PFA_API_HOST`/`PFA_API_PORT`) because Docker Desktop's
`host.docker.internal` cannot reach a service bound only to `127.0.0.1`.

### n8n workflow changes

"Build Analysis Command" (shell-quoting Code node) became "Build Analyze
Request" (builds the `AnalyzeRequest` JSON body); "Run Analysis (Python
CLI)" (Execute Command) became "Run Analysis (HTTP)"
(`n8n-nodes-base.httpRequest`, `POST` to
`{{ $env.PFA_API_BASE_URL || 'http://host.docker.internal:8000' }}/analyze`).
"Build GitHub Action Command" became "Build GitHub Issue Request" (builds
the `GitHubIssueRequest` JSON body, still carrying `approved: true,
dry_run: false` and a single-repo allow-list only on the already-gated
approved path); "Create GitHub Issue (Approved)" stayed the same node name
but became an `n8n-nodes-base.httpRequest` node posting to `.../github/issues`.
"Parse Orchestration Result" and "Parse GitHub Action Result" were
simplified (no more `exitCode`/`stdout` parsing) but keep their same
wrapper-key contract (`orchestrationResult`, `actionResult`) so every
downstream node -- Render Review Summary, Human Review & Approval, Approval
Gate, Approved Summary, No Action (Rejected) -- is unchanged. The approval
boundary itself (case-sensitive `"approve"` + non-blank opportunity_id +
non-blank repo, rejection path with zero outgoing connections) is
unchanged.

### Tests

`tests/test_api.py` -- 15 new tests, all offline (Starlette `TestClient`,
in-process, no socket opened): `/health` responds; `/analyze` delegates
exactly once to `run_analysis` and returns exactly 3 opportunities;
`/analyze` never invokes `create_opportunity_issue` (proven by monkeypatching
it to raise if called); `/analyze` rejects missing required fields and
unknown fields (422) and surfaces `OrchestrationError` as a structured 400;
`/github/issues` delegates to `create_opportunity_issue` with the exact
arguments supplied; an unapproved request is refused
(`refused_not_approved`); `approved` defaults to `False`; dry-run is the
default and makes no mutation (checked by unsetting `GITHUB_TOKEN` and
confirming the request still succeeds, proving no real client is
constructed); the default allow-list stays empty when `GITHUB_DEMO_REPO` is
unset (`refused_unsupported_repo`, not silently allowed); a duplicate
idempotency marker is still refused through the HTTP layer (via a
monkeypatched `GitHubRestIssueClient`, still zero real network calls);
`/github/issues` rejects unknown fields; and a full-stack test (real
`run_analysis`, fake retrieval/analyst/critic/revision clients, real HTTP
round-trip via `TestClient`) proves the HTTP boundary introduces no
additional Gemini requests beyond the existing 2-request pass-path budget.

`tests/test_n8n_workflow.py` -- grew from 13 to 19 tests. Removed 2 tests
that asserted CLI-command-string content no longer applicable (the
`github_action`/`orchestrator` CLI invocation checks, and the
`PFA_PROJECT_ROOT` environment-variable-reference check, since neither CLI
is shelled out to anymore). Added: no `n8n-nodes-base.executeCommand` node
remains anywhere in the workflow (checked both structurally and as a raw
string search for `"executeCommand"`); exactly the two expected nodes are
`n8n-nodes-base.httpRequest`; the analysis HTTP node's URL contains
`host.docker.internal` and ends with `/analyze`; the GitHub-action HTTP
node's URL contains `host.docker.internal` and ends with `/github/issues`;
the rejection path's reachable-node set is exactly `{"No Action
(Rejected)"}` (re-proving the core safety property against the new node
names); the two new HTTP nodes contain no hard-coded Windows path; the
"Build GitHub Issue Request" node is the only place `approved: true` /
`dry_run: false` appear; the "Build Analyze Request" node never references
`approved` or anything GitHub-related.

### Test result

Command:

`.\.venv\Scripts\python.exe -m pytest -q`

Result: **292 / 292 passed** (271 baseline + 15 new `test_api.py` + 6 net
new `test_n8n_workflow.py`, i.e. 8 added minus 2 removed). 0 failures, 0
errors, no network/Gemini/GitHub/n8n execution anywhere in the suite.

### Unresolved issues / human validation needed

**HUMAN CHECKPOINT REQUIRED**: the repaired workflow has **not yet been
re-imported into or re-run against the live n8n instance**. Before treating
this as demo-ready:

1. Start the HTTP service on the Windows host (`.\.venv\Scripts\python.exe
   -m product_feedback_agent.api`, or the `product-feedback-agent-api`
   console script) and confirm `GET http://127.0.0.1:8000/health` responds.
2. Re-import (or open, if the running workflow is edited in place)
   `n8n/workflows/product-feedback-agent.json` into the live n8n Docker
   instance and confirm both `HTTP Request` nodes open cleanly (no
   unrecognized-node warning this time) and that
   `http://host.docker.internal:8000/health` is reachable from inside the
   container.
3. Run the reject path once (expect a dead end, no GitHub call) and the
   approve path once against a real, disposable demo repository (expect a
   real GitHub issue) -- the same two live checks called for, and not yet
   completed, in the original Stage 15 entry above.

No real GitHub issue, no real Gemini call, and no live n8n execution
occurred during this repair.

## Stage 15 repair round 2 — Live validation + human approval form schema fix

**Status:** In progress. JSON-level repair complete; one final live
re-import validation still required. Group 3 has NOT been started.

### Live validation results (2026-09-07)

Re-testing the HTTP-repaired workflow (commit `dccfaca`) against the
user's real self-hosted n8n Docker instance, on Windows, confirmed:

1. The Product Feedback Agent HTTP API started successfully on the
   Windows host.
2. `GET http://localhost:8000/health` returned `status=ok`.
3. The repaired workflow JSON imported successfully into n8n.
4. Both former Execute Command "?" nodes are now recognized, built-in
   `n8n-nodes-base.httpRequest` nodes -- the round-1 repair is now
   confirmed live.

### Live finding

The "Human Review & Approval" node imported with **Page Type = Form
Ending** instead of **Page Type = Next Form Page**. Manually switching it
to Next Form Page in the n8n UI exposed the installed Form node's
page-mode options (Define Form: "Using Fields Below" / "Using JSON", plus
Form Elements) -- confirming the node type itself is fine, but the
`operation` parameter value in the tracked JSON was wrong.

### Repair

Updated only the "Human Review & Approval" node in
`n8n/workflows/product-feedback-agent.json`, switching it to the
installed Form node's JSON-defined form mode so no manual reconfiguration
is needed on import:

- `operation`: `"completion"` -> `"page"`.
- Added `defineForm: "json"` and a `jsonOutput` string (the same
  `fieldLabel`/`fieldType`/`fieldOptions.values[].option`/`requiredField`
  shape already used elsewhere in this workflow) defining exactly three
  fields, in order: `Decision` (dropdown, `approve`/`reject`, required),
  `opportunity_id` (text, required, placeholder pointing at the 3
  opportunity IDs in the summary), `repo` (text, required, placeholder
  `owner/repository`).
- The opportunity summary moved from an inline `html` form field to
  `formDescription` (`={{ $json.summaryMarkdown }}`), since the JSON field
  list is now limited to exactly the three fields the Approval Gate reads.
- "Approval Gate" and "Build GitHub Issue Request" were updated to read
  the new, shorter field names (`$json['Decision']`, `$json['opportunity_id']`,
  `$json['repo']`) instead of the old UI-builder labels. The safety gate's
  logic is unchanged: case-sensitive `Decision == "approve"` AND non-blank
  `opportunity_id` AND non-blank `repo`, `and`-combined, still gates the
  only edge into the GitHub HTTP Request node.

### Tests

`tests/test_n8n_workflow.py` grew from 19 to 28 tests (9 new): the Human
Review node's `operation` is `"page"` (never `"completion"`); `defineForm`
is `"json"`; the parsed `jsonOutput` contains exactly `Decision`
(required dropdown, options `{approve, reject}`), `opportunity_id`
(required text), `repo` (required text, placeholder `owner/repository`),
in that order and no others; the Approval Gate's three conditions
reference exactly `$json['Decision']`, `$json['opportunity_id']`,
`$json['repo']`; "Build GitHub Issue Request" reads the same two field
names; and the node's parameters contain no literal `"Form Ending"`
string.

### Test result

Command:

`.\.venv\Scripts\python.exe -m pytest -q`

Result: **301 / 301 passed** (292 baseline + 9 new). 0 failures, 0
errors. `git diff --check` reported no whitespace errors. No network,
Gemini, GitHub, or live n8n execution anywhere in this repair -- it was
applied statically against the JSON file based on the live finding above.

### Unresolved issues / human validation needed

**HUMAN CHECKPOINT REQUIRED**: this JSON-level repair has **not yet been
re-imported into the live n8n instance**. Before treating the human
approval step as demo-ready:

1. Re-import `n8n/workflows/product-feedback-agent.json` and confirm
   "Human Review & Approval" now shows Page Type = Next Form Page (not
   Form Ending) with no unrecognized/misconfigured warning.
2. Confirm the JSON-defined `Decision`/`opportunity_id`/`repo` fields
   render correctly in the live form UI with no further manual
   reconfiguration required.
3. Submit the form once with `Decision = reject` (or blank
   `opportunity_id`/`repo`) and confirm the run dead-ends at "No Action
   (Rejected)"; submit once with `Decision = approve` plus a real
   `opportunity_id` and disposable demo `repo` and confirm the submitted
   values reach "Build GitHub Issue Request" correctly -- these, plus the
   round-1 approve-path GitHub-issue check, remain the required live
   checks before Group 3 (or any real GitHub/Gemini call) begins.

No real GitHub issue, no real Gemini call, and no live n8n execution
occurred during this repair.

## Stage 15 repair round 3 — Removed `$env` dependency from HTTP node URLs

**Status:** Complete (JSON-level repair). One final live re-import
validation still required. Group 3 has NOT been started.

### Live validation results (2026-09-07)

Re-testing the human-approval-form-repaired workflow (commit `6ab44f1`)
against the user's real self-hosted n8n Docker instance, on Windows,
confirmed:

1. The repaired workflow imported correctly, including "Human Review &
   Approval" showing Page Type = Next Form Page, Define Form = Using
   JSON, with correct Decision/opportunity_id/repo fields (round-2 repair
   confirmed live).
2. The Windows HTTP service was live: `GET http://localhost:8000/health`
   returned `status=ok`.

### Live finding

The first real execution failed in "Run Analysis (HTTP)" with `access to
env vars denied`. The installed n8n instance blocks node expressions from
reading environment variables at all, so the URL expression that fell
back to `http://host.docker.internal:8000` when an override variable was
unset could not evaluate. No request reached the Python API and no Gemini
call was made.

### Repair

Removed the environment-variable read from both HTTP Request node URLs in
`n8n/workflows/product-feedback-agent.json`, replacing each with a plain,
hardcoded, non-secret local Docker Desktop endpoint string (no `={{ }}`
expression needed, since the value is now static):

- "Run Analysis (HTTP)": `http://host.docker.internal:8000/analyze`
- "Create GitHub Issue (Approved)": `http://host.docker.internal:8000/github/issues`

Both node `notes` fields were updated to record the live finding. The
Python API process remains solely responsible for its own Gemini/GitHub
credentials; n8n never receives or stores them -- this was already true
before this repair and is unchanged by it. No other node, connection, or
field changed: HTTP Request node types, the intake form, JSON request
bodies, the human approval form, the exact Decision/opportunity_id/repo
fields, the Approval Gate, the rejected dead-end path, and the
approved-only GitHub path are all preserved exactly as in round 2.

### Tests

`tests/test_n8n_workflow.py` grew from 28 to 32 tests (4 new): the
workflow file contains no `$env` expression anywhere; contains no
reference to the removed environment-variable override name anywhere;
the "Run Analysis (HTTP)" URL is exactly
`http://host.docker.internal:8000/analyze`; and the "Create GitHub Issue
(Approved)" URL is exactly
`http://host.docker.internal:8000/github/issues`. All pre-existing
round-1/round-2 tests (Execute Command absence, HTTP node types, rejection
path dead-end, approval-gate field names, Human Review page-type/JSON-form
checks, no embedded secrets) were re-run unmodified and still pass against
the new hardcoded URLs.

### Test result

Command:

`.\.venv\Scripts\python.exe -m pytest -q`

Result: **305 / 305 passed** (301 baseline + 4 new). 0 failures, 0 errors.
`git diff --check` reported no whitespace errors. No network, Gemini,
GitHub, or live n8n execution occurred during this repair -- it was
applied statically against the JSON file based on the live finding above.

### Unresolved issues / human validation needed

**HUMAN CHECKPOINT REQUIRED**: this JSON-level repair has **not yet been
re-imported into the live n8n instance**. Before treating the workflow as
demo-ready:

1. Re-import `n8n/workflows/product-feedback-agent.json` and confirm
   "Run Analysis (HTTP)" now shows the plain
   `http://host.docker.internal:8000/analyze` URL with no expression
   editor error.
2. Submit the intake form once and confirm the request actually reaches
   the Windows-host Python API this time (check the API process's own
   logs / the `Parse Orchestration Result` node's output), producing a
   real Gemini-backed analysis result for the first time in this workflow.
3. Continue through Human Review & Approval and confirm both the
   reject-path dead-end and the approve-path GitHub-issue creation
   (against a disposable demo repo) still work end-to-end, now that a
   request can actually reach the API -- these, plus the checks listed in
   round 2, remain the required live checks before Group 3 (or any real
   GitHub/Gemini call) begins.

No real GitHub issue, no real Gemini call, and no live n8n execution
occurred during this repair.

## Stage 15 repair round 4 — Removed redundant critic `status` from the wire contract

**Status:** Complete (architectural repair). One controlled live n8n retry
still required. Group 3 has NOT been started.

### Live validation results (2026-09-07)

Re-testing the `$env`-removed workflow (commit `83652c3`) against the
user's real self-hosted n8n Docker instance, on Windows, confirmed real
progress: the n8n workflow imported, ran, and its "Run Analysis (HTTP)"
node successfully reached this repository's Windows-host Python API --
`POST /analyze` was received, and ingestion/retrieval began successfully.
This is the first live confirmation that n8n (in Docker) can reach the
Windows host at `host.docker.internal:8000` and drive a real request into
the Python service. The Hugging Face "unauthenticated requests" warning
observed during this run was informational only; the local MiniLM
embedding weights loaded successfully and were not the failure.

### Live finding

The request then failed inside the Gemini critic stage. The real critic
again returned `status="revise"` with `required_revisions` entirely
missing (not merely an empty list) -- the third distinct live occurrence
of this failure family (see the two `2026-09-07` entries in
`docs/results-log.md`: "Critic Contract Failure and Fix" and "Second
Critic Contract Failure and Schema-Level Fix"). The `CriticWireResponse`
union in place at the time (`CriticPassResponse | CriticReviseResponse`)
rejected the response with `CriticReviseResponse.required_revisions: Field
required`, `run_critic` raised `CriticError`, and that error propagated
unhandled through Starlette, producing a generic HTTP 500 with a raw
traceback rather than a structured error body for n8n.

### Conclusion

Three separate live attempts, using three different enforcement
strategies (prompt wording only; then a schema-level pass/revise `anyOf`
union), all failed the same way: Gemini produced an inconsistent
`status`/`required_revisions` pair. The recurring root cause is that
`status` is redundant, model-controlled state that duplicates what
`required_revisions` already expresses -- asking the model to produce both
creates a state the model can make internally inconsistent no matter how
tightly the schema around it is constrained. The fix is architectural, not
another prompt or schema patch: remove `status` from the wire contract
entirely.

### Repair

`src/product_feedback_agent/critic.py`:

- Replaced `CriticPassResponse`/`CriticReviseResponse`/`CriticWireResponse`
  (the pass/revise `anyOf` union) with a single flat `CriticWireResponse`
  Pydantic model (`extra="forbid"`): `issues`, `unsupported_claims`,
  `citation_errors`, `duplicate_opportunities`, and a `required_revisions:
  list[str]` field with no default, so it is always present in
  `model_json_schema()`'s `"required"` list, matching the schema Gemini
  actually receives via `build_critic_response_schema()`. There is no
  `status` field anywhere in this schema, and no `anyOf`/`oneOf`.
- Added `derive_critic_status(required_revisions) -> CriticStatus`: the
  single place `CriticStatus` is decided -- non-empty `required_revisions`
  => `REVISE`, empty => `PASS`. `status` is never read from the model's raw
  JSON.
- `run_critic` now parses the raw response as `CriticWireResponse`
  (rejecting a stray `status` field the model might still emit, since
  `extra="forbid"` is unchanged), derives `status` via
  `derive_critic_status`, and constructs the domain `CriticOutput` (which
  still has a `status` field, since `pipeline.py`/`orchestrator.py`/`api.py`
  all read it) from the wire findings plus the derived status.
- The critic prompt in `build_critic_prompt` no longer mentions `status` at
  all; it instructs the model that `required_revisions` alone is its
  verdict -- always present, empty when no change is needed, one or more
  concrete actionable strings otherwise, including for deterministic
  validation findings that require an analyst-side correction (the
  existing safe behavior from the Stage 12b fix is preserved, just
  reworded around `required_revisions` instead of `status="revise"`).
- No retries were added, the critic is still called at most once, and the
  2/3 (pass) / 3/3 (revise) total Gemini request budget in `pipeline.py` is
  unchanged.

`src/product_feedback_agent/api.py` (hardening for the unhandled-500
finding above):

- `/analyze` now catches `AnalystError`, `CriticError`, and `RevisionError`
  explicitly and returns a small structured JSON body
  (`{"error": "analysis_failed", "stage": <analyst|critic|revision>,
  "error_type": <exception class name>}`) with HTTP 502, instead of letting
  the exception propagate through Starlette as an unhandled ASGI exception
  (generic HTTP 500, raw traceback). No exception message, model response
  content, or stack trace is included in the response. Any other,
  unexpected exception type is left to propagate unchanged -- this is not a
  blanket try/except, and no error is swallowed or turned into a fake
  success.

### Tests

`tests/test_critic.py`: removed the tests that locked the obsolete
pass/revise `anyOf` union (`CriticPassResponse`/`CriticReviseResponse`
alternatives, per-branch `minItems`/`maxItems`) and the now-removed
`CriticOutput` cross-field validator, replacing them with tests proving
the new deterministic design: the wire schema is one flat object with no
`anyOf`/`oneOf`; it has no `status` property; `required_revisions` is in
`schema["required"]` with no `minItems`/`maxItems` tying it to anything
else; a stray `status` field from the model is rejected outright (not
silently dropped); `derive_critic_status([])` is `PASS` and
`derive_critic_status([...])` is `REVISE`; and `run_critic` now derives
`PASS` from an empty `required_revisions` array with no `status` field
present at all -- the literal shape that would have been an error under
the old contract is simply a valid pass under the new one. The domain
`CriticOutput` tests (still requiring `status`, still exposing exactly the
same six fields) are unchanged. `tests/test_pipeline.py` and
`tests/test_orchestrator.py`'s fake critic payloads were updated to the
new wire shape (no `status` key). `tests/test_api.py` gained tests proving
a `CriticError`/`AnalystError`/`RevisionError` raised by `run_analysis`
comes back as the structured 502 body above, with no leaked exception text
in the response.

### Test result

Command:

`.\.venv\Scripts\python.exe -m pytest -q`

Result: **313 / 313 passed** (305 baseline + 8 net new/changed). 0
failures, 0 errors. `git diff --check` reported no whitespace errors. No
network, Gemini, GitHub, or live n8n execution occurred during this
repair -- it was applied statically based on the live finding above.

### Unresolved issues / human validation needed

**HUMAN CHECKPOINT REQUIRED**: this repair has **not yet been re-tested
live**. Before treating full n8n analysis as demo-ready:

1. Re-run the n8n workflow once (single controlled retry, per repository
   instruction) and confirm "Run Analysis (HTTP)" now completes with a
   real critic verdict derived from `required_revisions` (pass with `[]`,
   or revise with one or more concrete instructions) instead of a
   `CriticError`/HTTP 500.
2. If the critic still fails for a genuinely different reason, confirm the
   API now returns the structured 502 body (`error`/`stage`/`error_type`)
   instead of an unhandled traceback, so the failure is visible to n8n
   without exposing internals.
3. Continue through Human Review & Approval and the approve-path
   GitHub-issue creation (against a disposable demo repo) if the analysis
   completes -- these, plus the checks listed in prior rounds, remain the
   required live checks before Group 3 (or any further live Gemini/GitHub
   call) begins.

A full successful live n8n analysis (intake through GitHub issue) is NOT
yet claimed. No real GitHub issue, no real Gemini call, and no live n8n
execution occurred during this repair.

---

## Stage 15 final — Live end-to-end success

**Status:** Complete. Group 2 (Stages 13-15) is now fully live-validated.

The controlled live retry recommended above succeeded. Full details
(exact trace-log timestamps, durations, token counts, and the interpreted
sequence) are recorded in `docs/results-log.md`, "2026-09-07 — Live Stage
15 Success: n8n End-to-End Run, Reject Path, Approved GitHub Issue #1" --
this entry only summarizes it here so Group 2's status is unambiguous
before Group 3 begins:

- The repaired HTTP-boundary n8n workflow ran live, start to finish,
  against the user's real self-hosted n8n Docker instance and the
  Windows-host `product_feedback_agent.api` service.
- Two live analyst requests against the default `gemini-3.7-flash` model
  hit that Google AI Studio project's rate limit
  (`error_type: RateLimitError`); the environment was switched to
  `PFA_GEMINI_MODEL=gemini-3.5-flash-lite` for continued live testing.
  `gemini-3.7-flash` remains the recorded default; this was a runtime
  override, not a default change.
- A full live pipeline run (analyst -> critic -> revision, 3 model
  requests, `revised=True`) completed, followed by a `reject` decision on
  **Human Review & Approval** that correctly dead-ended at **No Action
  (Rejected)** with zero GitHub calls (no `github_action` trace entry in
  the following ten minutes).
- A second full live pipeline run against the same cached analysis-context
  packet (again 3 model requests, `revised=True`) completed, followed by
  an `approve` decision that correctly created exactly one real GitHub
  issue: `datascout0/product-feedback-agent` issue **#1**, "[Opportunity]
  Search Result Filtering Friction" (per the human tester's direct report,
  corroborated by the `find`/`create` entries in `logs/github-actions.jsonl`).

This closes every remaining Stage 15 "HUMAN CHECKPOINT REQUIRED" item from
every prior repair round above. No implementation code changed for this
entry -- it is a live-validation record only. The offline suite result at
the time was **324 passed, 1 warning** (see the "Make Gemini model
configurable" commit and its predecessors, which added tests beyond what
Stage 15 repair round 4's "313 passed" entry above reflects).

---

# Agent Progress Log — Group 3 (Stages 16-18)

This section tracks autonomous progress on branch `agent/finish-mvp` for
Group 3, the final implementation/documentation group: a deterministic
evaluation suite, a secondary-domain reuse proof, and portfolio-quality
documentation. Groups 1 and 2 (Stages 10-15, above) are complete and
live-validated and are not redesigned in this group -- Stage 16 makes one
small, additive deterministic-validation addition (see below), and nothing
else in `src/product_feedback_agent/` changes.

## Stage 16 — Deterministic evaluation suite

**Status:** Complete.

### Files added

- `eval/__init__.py`, `eval/cases.py`, `eval/run_evaluation.py`
- `eval/evaluation_report.json`, `eval/evaluation_report.md` (generated
  artifacts, committed so the measured results are visible without running
  anything)
- `tests/test_eval_suite.py`

### Files changed

- `src/product_feedback_agent/validation.py`: added
  `ValidationIssueType.DUPLICATE_OPPORTUNITY_ID` and a deterministic check
  (`_find_duplicate_ids`) flagging any `opportunity_id` used by more than
  one opportunity in an `AnalystOutput`. This was the one genuine gap found
  while building the Stage 16 suite -- requirement 2 ("unique opportunity
  IDs") had no existing deterministic check to exercise. The fix is
  additive only: it does not touch scoring, the critic wire contract, or
  the model-request budget, and every pre-existing `validate_analyst_output`
  test still passes unchanged.
- `tests/test_validation.py`: 2 new tests for the above.

### What it does

`eval/cases.py` defines 34 self-contained, fully offline evaluation cases
(no shared state with `tests/`) against the real, unmodified production
functions (`validate_analyst_output`, `compute_score`, `derive_critic_status`,
`run_critic`, `run_pipeline`, `create_opportunity_issue`), covering all 17
requirements in the Stage 16 task brief: exactly-3-opportunities, unique
opportunity IDs, feedback-ID citation resolution, exact-quote verification,
segment-support checks, evidence-count correctness, the scoring formula,
critic status derivation, the 2-request pass / 3-request revise (never 4)
budget, prompt-injection-as-data, GitHub approval/allow-list/idempotency
safety, empty/weak-retrieval non-fabrication, and product-area-vs-segment
confusion (including replaying the real 2026-09-07 analyst smoke finding
when the gitignored local fixture is present). Every case is tagged with a
`category` (`structure`/`grounding`/`scoring`/`critic_budget`/`safety`) and
an `is_violation_check` flag (whether the case proves a bad input is
correctly caught, vs. a good input passing cleanly), which
`eval/run_evaluation.py` uses to compute exactly the four metric types the
task brief calls "suitable": cases passed/executed, grounding checks
passed/total, expected validation violations caught/total, and safety cases
passed/total. No accuracy/precision/recall/quality percentage and no
latency number is computed anywhere in this suite.

`eval/run_evaluation.py` runs the suite and writes both a machine-readable
(`evaluation_report.json`) and human-readable (`evaluation_report.md`)
report from the measured results, exiting non-zero on any failure so it can
also serve as a CI gate. `tests/test_eval_suite.py` wires the same case
registry into the main `pytest` run (asserting all 34 pass and that all 17
requirement numbers are covered) and statically proves the suite has zero
live dependency (no real Gemini/GitHub client class is ever constructed,
`urlopen` never appears in the module source).

### Measured results

`.\.venv\Scripts\python.exe -m eval.run_evaluation`:

- Cases passed/executed: **34/34**
- Grounding checks passed/total: **10/10**
- Expected validation violations caught/total: **19/19**
- Safety cases passed/total: **9/9**

### Test result

`.\.venv\Scripts\python.exe -m pytest -q`: **329 passed, 1 warning** (326
baseline + 2 duplicate-ID unit tests + 3 eval-suite wiring tests). 0
failures, 0 errors, no network/Gemini/GitHub requests made.

### Unresolved issues / human validation needed

None. This suite is fully offline by design and requires no human
verification step.

---

## Stage 17 — Secondary-domain reuse proof

**Status:** Complete.

### Files added

- `data/samples/secondary_domain/onboarding_feedback.csv` (8 records)
- `data/samples/secondary_domain/onboarding_strategy.md` (4 sections)
- `tests/test_secondary_domain.py` (6 tests)

### What it does

A small, realistic synthetic dataset in a customer onboarding/support/
workflow-friction domain -- deliberately different product areas
(`sso`, `onboarding`, `workflow`, `notifications`) and segments
(`enterprise`, `smb`, `startup`) from the primary search/reporting/mobile
feedback used everywhere else in this project. The tests ingest it with
the unmodified `load_feedback_file`/`load_context_file`, index and retrieve
it through the real (unmodified) `SemanticRetrieval`/Chroma path using the
same deterministic offline bag-of-words embedder pattern already used in
`tests/test_retrieval.py`, and run it through the real (unmodified) Stage
13 `orchestrator.run_analysis` with fake analyst/critic clients (no live
Gemini call) that cite this dataset's real feedback/context IDs.

Verified: the dataset ingests cleanly (8/8 valid, 0 duplicates); retrieval
indexes and returns all of it; the shared pipeline produces exactly 3
opportunities with zero unresolved deterministic validation issues (every
citation and quote resolves); the same 2-request pass-path budget and
`pending_review` human-approval boundary hold; `evidence_count` for each
opportunity matches its distinct cited feedback IDs; each opportunity's
score matches the same `compute_score` formula (imported and called
directly, not reimplemented); and none of the ten core pipeline modules
contain the literal strings `onboarding`, `sso`, or `startup` -- i.e., no
domain-specific branch or conditional exists anywhere in the core pipeline.

**Changed between domains:** DATA + CONTEXT only.
**Unchanged:** CORE ARCHITECTURE, SCHEMAS, VALIDATION, SCORING, CRITIC
CONTRACT, HUMAN APPROVAL MODEL.

### Test result

`.\.venv\Scripts\python.exe -m pytest -q`: **335 passed, 1 warning** (329
baseline + 6 new). 0 failures, 0 errors, no network/Gemini/GitHub requests
made.

### Unresolved issues / human validation needed

None. This proof is fully offline by design.

---

## Stage 18 — README / architecture / demo / portfolio documentation

**Status:** Complete.

### Files added/changed

- `README.md` -- rewritten as portfolio-facing documentation (problem,
  target user, principles, architecture summary, evidence-grounding design,
  deterministic validation/scoring, critic/revision, human approval/GitHub
  safety, model configuration, n8n integration, evaluation approach and
  measured results, secondary-domain reuse proof, limitations, local
  setup/run instructions, demo flow).
- `docs/architecture.md` -- a Mermaid diagram of the full pipeline
  (ingestion -> local embeddings/Chroma -> retrieval -> Gemini analyst ->
  deterministic validation/scoring -> Gemini critic -> optional single
  revision -> human review -> approved GitHub action), including the n8n
  orchestration layer, the Python HTTP bridge, the 2/3 request ceiling, and
  which components are deterministic vs. model-controlled.
- `docs/demo-script.md` -- a concise, portfolio-appropriate demo script
  using only what is actually implemented and live-validated.
- `docs/screenshots.md` -- identifies which parts of the demo are best
  shown as a screenshot (n8n reject/approve paths, the human approval
  form, the real created GitHub issue) vs. which are already better
  evidenced by committed test/evaluation output, without fabricating any
  screenshot.
- `docs/portfolio-bullets.md` -- CV/portfolio bullets grounded only in what
  was actually built and measured; no fabricated users, revenue, adoption,
  or time-savings claims.
- `docs/implementation-checklist.md` -- rewritten in clean UTF-8 (fixing a
  pre-existing mojibake encoding issue from an earlier save) and updated to
  reflect Stage 16/17/18 completion and the live Stage 15 GitHub issue.
- `docs/n8n-setup.md` -- the top "HUMAN CHECKPOINT REQUIRED" banner and the
  closing "what was NOT done" section were updated to reflect the Stage 15
  final live success recorded above; nothing about the workflow itself
  changed in this stage.

### Unresolved issues / human validation needed

Screenshots themselves (as opposed to the plan for which ones matter) are a
human action against the live n8n/GitHub UI and are not captured by Claude
Code -- see `docs/screenshots.md`.
