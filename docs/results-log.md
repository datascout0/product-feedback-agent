# Product Feedback Agent — Results Log

This file records only observed or verified project results. Do not add estimated product impact or unmeasured claims.

---

## 2026-09-02 — Repository Foundation

### Result

Local repository created at:

`C:\Users\shubh\projects\product-feedback-agent`

GitHub remote:

`https://github.com/datascout0/product-feedback-agent.git`

### Verification

- Branch: `main`
- Local branch synchronized with `origin/main`
- Working tree verified clean after initial setup
- Initial commit: `1e341ab Initialize Product Feedback Agent`
- Gemini decision commit: `6200733 Record Gemini model and quota budget`

---

## 2026-09-02 — Gemini Runtime Configuration

### Selected model

`gemini-3.7-flash`

### Active Google AI Studio project limits

- RPM: 5 requests/minute
- TPM: 250,000 input tokens/minute
- RPD: 20 requests/day

### Derived workflow constraints

- Passing run: maximum 2 Gemini requests
- Revision run: maximum 3 Gemini requests
- Minimum sequential request spacing: 14 seconds

### Development implication

The 20-request daily limit is currently the most restrictive development constraint. Cached evidence packets and saved model-response fixtures will therefore be used for non-LLM debugging when practical.

---

## 2026-09-02 — Python Environment

### Verified versions

- Python: 3.12.10
- Pydantic: 2.13.5
- pytest: 9.1.1

### Environment

Project-local Python virtual environment created under `.venv`.

---

## 2026-09-02 — Packaging Issue

### Observed failure

Editable installation initially failed with:

`TOMLDecodeError: Invalid statement (at line 1, column 1)`

### Resolution

`pyproject.toml` was rewritten as UTF-8 without BOM and package installation subsequently succeeded.

### Lesson

Configuration files that require strict UTF-8 parsing should be written without a Windows PowerShell UTF-8 BOM.

---

## 2026-09-02 — PowerShell Command Issue

### Observed failure

`Format-Hex` was accidentally appended directly after a closing PowerShell expression, causing:

`Unexpected token 'Format-Hex' in expression or statement.`

### Resolution

The commands were executed separately.

---

## 2026-09-02 — Feedback Schema

### Implemented

- `FeedbackInput`
- `FeedbackRecord`
- Required feedback text and source validation
- Optional incoming feedback ID
- Required normalized feedback ID
- Preservation of original feedback text
- Optional product area
- Optional segment
- Optional date
- Optional customer ID
- Optional rating
- Optional metadata

### Observed schema issue

The first test collection failed because the field:

`date: date | None`

conflicted with the imported `datetime.date` name during Pydantic annotation evaluation.

### Resolution

The type import was aliased:

`from datetime import date as Date`

and the field became:

`date: Date | None`

---

## 2026-09-02 — Schema Test Result

Command:

`.\.venv\Scripts\python.exe -m pytest -v`

Result:

**7 / 7 tests passed**

Execution time:

**0.14 seconds**

Verified behaviors:

1. Minimum valid feedback is accepted.
2. Raw feedback may omit an ID.
3. Normalized feedback requires an ID.
4. Original feedback text is preserved.
5. Blank feedback text is rejected.
6. Blank source is rejected.
7. Optional fields are supported.

---

## Next Measurement

Feedback ingestion tests:

- CSV loading
- JSON loading
- Stable ID generation
- Exact duplicate removal
- Invalid-record handling
- Source normalization
---

## 2026-09-06 — Feedback Ingestion

### Implementation

Implemented feedback ingestion using Python standard-library CSV/JSON handling, existing Pydantic schemas, and deterministic SHA-256 identifiers.

Implemented:

- UTF-8 CSV ingestion
- UTF-8 JSON array ingestion
- Required-field validation
- Stable deterministic ID generation when source IDs are missing
- Preservation and trimming of supplied feedback IDs
- Original customer-text preservation
- Source-name normalization
- Date validation/normalization through the feedback schema
- Optional metadata preservation
- Unknown-field merging into metadata
- CSV metadata JSON-object parsing
- Exact duplicate removal
- Invalid-record collection without failing the complete dataset
- Clear whole-file failures for unsupported extensions and malformed JSON
- 200-record MVP input limit

### Evidence-integrity correction

Initial duplicate handling excluded `feedback_id` from the duplicate key.

This could have collapsed two otherwise identical source records carrying different supplied IDs into one evidence item.

The duplicate rule was corrected so the complete normalized record identity is considered.

Regression coverage verifies:

- Identical generated records are deduplicated.
- Identical records with the same supplied ID are deduplicated.
- Otherwise identical records with different supplied IDs are preserved.
- Identical feedback text from different customers is preserved.

### Claude Code

The ingestion implementation and regression correction were developed using Claude Code with the `sonnet` model alias.

No additional packages were installed and no LLM/API calls were made by the Product Feedback Agent runtime.

### Test Result

Command:

`.\.venv\Scripts\python.exe -m pytest -v`

Result:

**23 / 23 tests passed**

Execution time:

**0.24 seconds**

Breakdown:

- 16 ingestion tests passed
- 7 schema tests passed
- 0 failures
- 0 errors

### Deferred

Semantic duplicate detection has not yet been implemented and remains a later milestone.
---

## 2026-09-06 — Product Context Ingestion

### Implementation

Implemented product-context ingestion for UTF-8 Markdown and plain-text documents.

Implemented:

- `.md` and `.txt` support
- deterministic document IDs
- deterministic section IDs
- Markdown heading-based section splitting
- pre-heading Markdown content preservation
- document-type inference for strategy, roadmap, personas, features and research notes
- fallback document type of `context`
- empty-document rejection
- unsupported-extension rejection
- exact source-text preservation for later citations

### Citation-integrity correction

The initial implementation used `.strip()` when storing section text.

This was corrected so `.strip()` is used only to determine whether content is empty. Stored section text now preserves the original source content exactly, including leading/trailing whitespace and newlines where applicable.

### Test Result

Command:

`.\.venv\Scripts\python.exe -m pytest -v`

Result:

**36 / 36 tests passed**

Execution time:

**0.29 seconds**

Breakdown:

- 13 product-context tests passed
- 16 feedback-ingestion tests passed
- 7 schema tests passed
- 0 failures
- 0 errors
---

## 2026-09-07 — Local Embeddings and Semantic Retrieval

### Dependencies

Verified:

- sentence-transformers: 5.7.0
- chromadb: 1.5.9

### Implementation

Implemented:

- local Sentence Transformers embedding wrapper
- model: `sentence-transformers/all-MiniLM-L6-v2`
- lazy model loading
- normalized embeddings
- persistent Chroma storage
- separate `feedback` and `product_context` collections
- cosine-distance retrieval
- feedback indexing using stable feedback IDs
- context indexing using stable section IDs
- semantic feedback search
- semantic product-context search
- metadata preservation
- idempotent upsert behavior
- injectable fake embedder for fast/offline tests

### Automated Test Result

Command:

`.\.venv\Scripts\python.exe -m pytest -v`

Result:

**55 / 55 tests passed**

### Real Embedding Smoke Test

The real `all-MiniLM-L6-v2` model successfully loaded locally.

Embedding dimensions:

**384**

Feedback query:

`users need better search filtering`

Top semantic results included:

`Search results are hard to filter.`

Product-context query:

`onboarding and setup strategy`

Returned context results containing stable:

- `document_id`
- `section_id`
- section heading metadata

The smoke test completed successfully using persistent local Chroma storage.

### Observed Windows Behavior

A previous smoke test using Python `TemporaryDirectory` produced a Windows file-lock cleanup error after successful retrieval because Chroma still held an HNSW file handle.

The product implementation was not changed for this test-harness issue. The successful smoke test instead used the ignored local path:

`data/local/chroma-smoke`

### External Model Download

The public Sentence Transformers model was downloaded from Hugging Face without authentication.

The HF cache emitted a Windows symlink optimization warning, but model loading and inference completed successfully.
---

## 2026-09-07 — Deterministic Analysis Context Packet

### Implementation

Implemented the bounded analysis-context preparation layer that will later be exposed through the MCP `prepare_analysis_context` tool.

Implemented:

- deterministic statistics over the complete valid feedback dataset
- counts by source
- counts by product area
- counts by segment
- rating count and average rating
- one feedback semantic retrieval per packet
- one product-context semantic retrieval per packet
- deterministic diversity sampling
- diversity gain based on unseen source, product area and segment
- semantic-rank tie breaking
- exact feedback and context evidence preservation
- stable feedback, document and section IDs
- explicit retrieval parameters
- deterministic SHA-256 cache fingerprint
- cache invalidation when query, evidence content, metadata or parameters change
- input-order-independent cache fingerprinting
- cache hits that skip indexing, embedding and retrieval

### Diversity Correction

The initial implementation selected the first remaining result that added any new diversity dimension.

This was corrected so each remaining result receives a diversity gain:

- +1 for a new source
- +1 for a new product area
- +1 for a new segment

The result with the highest gain is selected. Semantic rank breaks ties.

### Automated Test Result

Command:

`.\.venv\Scripts\python.exe -m pytest -v`

Result:

**91 / 91 tests passed**

The suite includes a regression test verifying that a lower-ranked result with higher diversity gain is preferred over the immediately next semantic result.
---

## 2026-09-07 — MCP Server and Inspector Validation

### Implementation

Implemented the MCP v2 server for Product Feedback Agent using MCP SDK 2.1.1.

Exposed read-only tools:

- `prepare_analysis_context`
- `search_feedback`

The future `create_opportunity_issue` write tool is intentionally not exposed yet.

### Reusability

- input paths resolve relative to the cloned project root rather than process cwd
- absolute paths remain supported
- persistent Chroma storage is anchored under the project root
- analysis-context cache is anchored under the project root
- no user-specific absolute paths are hard-coded
- Sentence Transformer loading remains lazy

### Automated Test Result

**113 / 113 tests passed**

### MCP Inspector Validation

Validated the server through MCP Inspector.

`prepare_analysis_context` successfully returned:

- dataset statistics
- retrieved feedback
- retrieved product context
- diverse feedback sample
- stable feedback IDs
- stable document IDs
- stable section IDs
- cache key
- retrieval parameters

A non-default `diverse_sample_size=3` produced exactly 3 diverse feedback records.

`search_feedback` was called after preparation without resending the feedback file.

With `top_k=3`, it returned exactly 3 results.

The two strongest results were the two feedback records:

`Search results are hard to filter.`

This confirms MCP parameter handling, persistent indexed retrieval, and structured output through the actual MCP protocol.

---

## 2026-09-07 - Gemini Analyst Validation

### Implementation

Implemented the bounded Gemini analyst using `google-genai` 2.22.0 and
`gemini-3.7-flash`.

The analyst:

- consumes one deterministic `AnalysisContextPacket`
- uses only supplied feedback and product context
- produces exactly three structured opportunity drafts
- uses Pydantic structured-output validation
- proposes score components from 1 to 5
- does not generate final evidence counts or opportunity scores
- makes at most one Gemini request per analyst run
- loads `GEMINI_API_KEY` lazily from the environment
- records non-secret model-call metadata

### Automated Validation

Full offline suite:

**137 / 137 tests passed**

Tests use injected fake clients and do not call Gemini.

### Real Gemini Smoke Test

A single real `gemini-3.7-flash` analyst request was made using the cached
analysis-context packet:

`e4cfdd21879143b76031823715f6c104f4bd9a8ccfd24bb070050c8ebea44d0c`

Result:

- success: true
- opportunities returned: 3
- duration: 17.781 seconds
- total tokens reported: 8,064
- Gemini requests: 1

Opportunity themes:

1. Difficulty narrowing and filtering search results
2. Mobile application instability during photo uploads
3. Reporting export workflow friction

### Important Validation Finding

The response was structurally valid but demonstrated why deterministic
business-rule validation is still required.

Gemini included `mobile` and `reporting` in `affected_segments`, although those
values are product areas rather than customer segments in the supplied data.

This is intentionally not corrected manually in the analyst output. The next
deterministic validation stage should detect unsupported segments, validate
feedback/context IDs and exact quotations, recompute evidence counts, and
calculate final opportunity scores locally.

---

## 2026-09-07 — Deterministic Opportunity Validation and Scoring

### Implementation

Implemented `product_feedback_agent.validation`: a pure, offline function
`validate_analyst_output(output, packet) -> ValidationResult` with no client
parameter and no I/O.

Implemented checks:

- feedback ID existence, for both `feedback_ids` and
  `representative_evidence[].feedback_id`
- exact-substring verification of every representative excerpt against the
  cited feedback's verbatim text
- context citation existence and correct document/section pairing
- affected-segment support against the `segment` metadata of the
  opportunity's own valid cited feedback, with an explicit distinct message
  when the value is actually a `product_area` in the dataset
- locally computed `evidence_count` from deduplicated, packet-valid
  feedback IDs only
- locally computed opportunity score using the locked weights (evidence
  strength 30%, severity 25%, strategic alignment 20%, segment breadth 15%,
  confidence 10%), with `roadmap_status` excluded from scoring

### Real-Finding Regression

Replayed the real 2026-09-07 Gemini analyst smoke output
(`data/local/analyst-smoke.json`) against its originating cached packet
(`data/local/analysis-context-cache/e4cfdd21...json`) through the new
validator, offline, with no model call.

Result: both `mobile` and `reporting` were correctly flagged as unsupported
`affected_segments` values, with the validator's message identifying each as
a `product_area` in the dataset rather than a customer segment. This test is
skipped (not failed) when the gitignored local fixtures are absent, so the
required suite never depends on them.

### Automated Test Result

Command:

`.\.venv\Scripts\python.exe -m pytest -q`

Result:

**162 / 162 tests passed**

Breakdown:

- 25 new deterministic validation/scoring tests passed
- 137 previously-passing tests remained passing
- 0 failures, 0 errors
- No network or Gemini requests were made

---

## 2026-09-07 — Gemini Critic

### Implementation

Implemented `product_feedback_agent.critic`, mirroring `analyst.py`'s
lazy-client / dependency-injection / structured-output / one-request-per-run
pattern exactly, using `google-genai` and `gemini-3.7-flash`.

The critic:

- consumes the `AnalysisContextPacket`, the analyst's `AnalystOutput` draft,
  and the Stage 10 `ValidationResult` together
- reviews evidence support, invalid/invented citations, exact-quotation
  problems, unsupported affected segments, duplicate opportunities, ignored
  contradictions, roadmap/strategy overreach, score-rationale quality,
  feature-vs-underlying-need confusion, confidence overstatement, and
  next-step fit against evidence gaps
- returns a structured `CriticOutput` (`status`, `issues`,
  `unsupported_claims`, `citation_errors`, `duplicate_opportunities`,
  `required_revisions`) with an enforced constraint that `status="revise"`
  requires at least one `required_revisions` entry
- has no field or capability that could decide a roadmap, create a GitHub
  issue, or change a permission
- makes at most one Gemini request per critic run, with no retry loop
- loads `GEMINI_API_KEY` lazily and never at import time
- records non-secret model-call metadata with `stage="critic"`

### Automated Test Result

Command:

`.\.venv\Scripts\python.exe -m pytest -q`

Result:

**187 / 187 tests passed**

Breakdown:

- 25 new critic tests passed
- 162 previously-passing tests remained passing
- 0 failures, 0 errors
- No network or Gemini requests were made

---

## 2026-09-07 — Bounded Analyst/Critic/Revision Pipeline

### Implementation

Implemented `product_feedback_agent.pipeline`: `run_pipeline(...)` runs
analyst (request 1) -> deterministic validation -> critic (request 2), and,
only when the critic returns `status="revise"`, exactly one revision
request (request 3, via a new `GeminiRevisionClient` mirroring
`analyst.py`/`critic.py`'s lazy-client pattern) followed by a local
re-validation. The critic is never called a second time. There is no loop
in this module; no code path can reach a 4th model request.

### Seeded Revision Demonstration

`tests/test_pipeline.py::test_remaining_deterministic_issues_after_revision_are_surfaced_not_hidden`
seeds a critic "revise" verdict (on an opportunity that misuses the
product_area value "mobile" as a segment) and a revision response that
still fails to fix it. Verified offline:

- exactly 3 total model requests were made (1 analyst + 1 critic +
  1 revision)
- the critic was not invoked a second time
- the deterministic validator still flagged `segment == "mobile"` as
  unsupported in the final result, i.e. the unresolved issue was surfaced,
  not hidden and not silently repaired

`test_revised_output_is_revalidated_against_the_original_packet` seeds a
successful revision (segment corrected to "enterprise") and confirms the
final `validation_result` matches a fresh direct
`validate_analyst_output(revised_output, packet)` call -- i.e. revalidation
genuinely re-runs against the revised opportunities, not the original
draft.

### Automated Test Result

Command:

`.\.venv\Scripts\python.exe -m pytest -q`

Result:

**200 / 200 tests passed**

Breakdown:

- 13 new pipeline/revision tests passed
- 187 previously-passing tests remained passing
- 0 failures, 0 errors
- No network or Gemini requests were made
- Confirmed via test assertions: critic-pass runs make exactly 2 model
  requests; critic-revise runs make exactly 3; no execution path exceeds 3

---

## 2026-09-07 — Live Group 1 Smoke: Critic Contract Failure and Fix

### Observed live result

A real `run_pipeline(packet)` smoke run reached the analyst request and the
critic request. The critic (real Gemini `gemini-3.7-flash` call) returned
`status="revise"` with `required_revisions=[]`.

`CriticOutput`'s existing Pydantic model-validator (`status="revise"`
requires at least one `required_revisions` entry) correctly rejected this
output, so `run_critic` raised `CriticError`. The revision request was
never made. No successful full live pipeline run (analyst -> critic ->
optional revision) is claimed from this smoke test.

Token/request counts for this smoke run were not read from
`logs/model-calls.jsonl` and are therefore not recorded here.

### Fix

Strengthened `_CRITIC_INSTRUCTIONS` in
`src/product_feedback_agent/critic.py` to explicitly state the
`status="revise"` => non-empty `required_revisions` rule, prohibit an empty
`required_revisions` list under `status="revise"`, require every listed
`issues` entry to have a matching `required_revisions` entry, and require
deterministic validation findings (unsupported segments, invalid IDs,
non-exact quotations) that need analyst correction to produce
`status="revise"` plus actionable revisions. The `CriticOutput` schema and
its existing validator were not weakened. No retries were added and the
2/3 Gemini request budget in `pipeline.py` is unchanged.

### Automated Test Result

Command:

`.\.venv\Scripts\python.exe -m pytest -q`

Result:

**204 / 204 tests passed**

Breakdown:

- 4 new critic prompt regression tests passed
- 200 previously-passing tests remained passing
- 0 failures, 0 errors
- No network or Gemini requests were made

### Status

Group 1 has not yet completed a successful full live pipeline run. One
controlled live retry is recommended to confirm the strengthened critic
prompt produces schema-valid output before any Group 2 work begins.

---

## 2026-09-07 — Live Group 1 Smoke: Second Critic Contract Failure and Schema-Level Fix

### Observed live result

A second real `run_pipeline(packet)` smoke run (live attempt 2, after the
prompt strengthening above) again reached the analyst request and the
critic request. The critic (real Gemini `gemini-3.7-flash` call) again
returned `status="revise"` with `required_revisions=[]` -- the same invalid
shape as live attempt 1, despite the strengthened prompt explicitly
prohibiting it. `CriticOutput`'s Pydantic model-validator again correctly
rejected the output, so `run_critic` raised `CriticError` before any
revision request was made. No successful full live pipeline run is claimed
from this smoke test either.

### Conclusion

Prompt-only enforcement of the `status="revise"` => non-empty
`required_revisions` relationship is insufficient: two separate live
attempts, the second with an explicit prohibition in the prompt, produced
the same invalid shape. The rule needed to be enforced at the JSON Schema
boundary sent to Gemini, not only in the prompt text and the post-hoc
`CriticOutput` validator.

### Fix

Added a wire (structured-output) schema layer in
`src/product_feedback_agent/critic.py`: `CriticPassResponse` and
`CriticReviseResponse` are two Pydantic models representing the pass/revise
alternatives, unioned as `CriticWireResponse` and exposed as an `anyOf`
JSON Schema via `build_critic_response_schema()` (backed by
`TypeAdapter(CriticWireResponse).json_schema()`). `CriticPassResponse`
constrains `required_revisions` to `maxItems: 0`; `CriticReviseResponse`
constrains it to `minItems: 1` (and marks it required). `run_critic` now
sends this schema to the critic client instead of
`CriticOutput.model_json_schema()`, validates the raw response through the
wire `TypeAdapter` first, and then re-validates the parsed result through
the existing `CriticOutput` domain model (whose own `model_validator` is
unchanged and unremoved) before returning it -- so the rest of the pipeline
keeps using the same `CriticOutput` type.

No prompt-only fix was relied on this time; the strengthened prompt text
from the previous fix is kept as an additional instruction layer, not the
primary enforcement. No retries were added, the 2/3 Gemini request budget
in `pipeline.py` is unchanged, and no required revisions are fabricated in
Python -- a `revise` + `[]` response is still rejected outright, now at two
layers (wire schema, then `CriticOutput`).

### Automated Test Result

Command:

`.\.venv\Scripts\python.exe -m pytest -q`

Result:

**213 / 213 tests passed**

Breakdown:

- 9 new critic wire-schema regression tests passed (two-alternative
  `anyOf`, revise-branch `minItems: 1`, pass-branch `maxItems: 0`, valid
  pass/revise parsing, revise+`[]` and pass+non-empty rejections at the
  wire layer, the real live failure shape rejected end-to-end through
  `run_critic`, and the unremoved `CriticOutput` guardrail rejecting the
  same shape directly)
- 204 previously-passing tests remained passing
- 0 failures, 0 errors
- No network or Gemini requests were made

### Status

Group 1 has still not completed a successful full live pipeline run. Full
live Group 1 success is NOT claimed. The critic contract is now enforced at
the JSON Schema boundary sent to Gemini (not only by prompt wording), which
is a structural, not just textual, guarantee against this specific failure
shape recurring. One controlled live retry is recommended next, and only
one -- per repository instruction, no further retries beyond that single
attempt should be made without human review of the result.

---

## 2026-09-07 — Live Group 1 Smoke: Successful Full Pipeline Run

### Observed live result

The controlled live retry recommended above was run. A real
`run_pipeline(packet)` call completed successfully end-to-end for the first
time: analyst request -> deterministic validation -> critic request ->
critic `status="revise"` (schema-valid, with non-empty `required_revisions`)
-> exactly one revision request -> local re-validation of the revised
output.

Result:

- `model_request_count = 3`
- `revised = True`
- critic status = `revise`
- final remaining deterministic validation issues = **0**
- local ignored artifact saved: `data/local/group1-live-smoke.json`

### Model call traces

Analyst:

- model: `gemini-3.7-flash`
- duration: 80.563 seconds
- total tokens: 7,907
- success: true

Critic:

- model: `gemini-3.7-flash`
- duration: 44.719 seconds
- total tokens: 7,457
- success: true

Revision:

- model: `gemini-3.7-flash`
- duration: 10.203 seconds
- total tokens: 9,595
- success: true

### Measured totals

- model requests: 3
- model-call duration: 135.485 seconds
- total reported tokens: 24,959

### Interpretation

- The analyst produced 3 opportunities.
- Deterministic validation ran against the original draft.
- The critic correctly returned `revise` and satisfied the schema-level
  `revise` => non-empty `required_revisions` contract added in the
  2026-09-07 Stage 12c fix (no `CriticError` this time).
- Exactly one revision request was made; the critic was not called again.
- The revised output was deterministically re-validated against the
  original packet.
- Final validation issues after revision: 0.
- No fourth model request occurred; the 3-request bound held.

This is the first successful full live Group 1 pipeline run. The two prior
live attempts recorded above (2026-09-07 "Critic Contract Failure and Fix"
and "Second Critic Contract Failure and Schema-Level Fix") are preserved
as-is in this log as reliability findings that motivated the prompt- and
schema-level fixes that made this run succeed -- they are not erased or
superseded, only resolved.

### Status

Group 1 (Stages 10-12) is now complete and live-validated. This closes the
"one controlled live retry" recommended in the prior entry. No further live
retries were performed. Group 2 work has not been started.

---

## 2026-09-07 — Stage 13: End-to-End Application Orchestration

### Implementation

Added `product_feedback_agent.orchestrator.run_analysis`, sequencing the
existing ingestion, `prepare_analysis_context`, and unmodified `run_pipeline`
behind one reusable function with explicit (never hard-coded) inputs, plus a
CLI/demo entry point (`main`, exposed as the `product-feedback-agent`
console script).

### Automated Test Result

Command:

`.\.venv\Scripts\python.exe -m pytest -q`

Result:

**229 / 229 tests passed**

Breakdown:

- 16 new orchestrator tests passed
- 213 previously-passing tests remained passing
- 0 failures, 0 errors
- No network or Gemini requests were made (all tests use fake retrieval and
  fake analyst/critic/revision clients)

### Verified behaviors

- `prepare_analysis_context` is invoked exactly once per `run_analysis`
  call (verified via fake-retrieval index/search call counters).
- `run_analysis` returns exactly 3 opportunities.
- Pass-run metadata (`revised=False`, `model_request_count=2`) and
  revise-run metadata (`revised=True`, `model_request_count=3`) both
  propagate correctly from `run_pipeline` unchanged.
- No execution path makes more than 3 total model requests.
- Unresolved deterministic validation issues after revision propagate into
  `unresolved_issue_count` rather than being hidden.
- `OrchestrationResult.human_review_status` is always `pending_review`; the
  enum has no other value, and the field set rejects an injected `approved`
  or `github_issue_url` field.
- The orchestrator module makes no GitHub call and imports nothing
  GitHub-related.

No real Gemini call was made for Stage 13; this stage only adds
orchestration around the already live-validated Group 1 pipeline.

---

## 2026-09-07 — Stage 14: Safe GitHub Opportunity Issue Creation

### Implementation

Added `product_feedback_agent.github_action.create_opportunity_issue`, a
human-approved, dry-run-capable, allow-listed, duplicate-safe GitHub issue
action built on the Python standard library (`urllib.request`, no new
dependency), plus a matching `create_opportunity_issue` MCP tool.

### Automated Test Result

Command:

`.\.venv\Scripts\python.exe -m pytest -q`

Result:

**254 / 254 tests passed**

Breakdown:

- 21 new `github_action` tests passed
- `test_mcp_server.py` net +4 (5 new tool tests added; 1 now-superseded
  "tool not present yet" test removed)
- 229 previously-passing tests remained passing
- 0 failures, 0 errors
- No network or Gemini requests were made
- No real GitHub API call was made: every `GitHubRestIssueClient` network
  path in the test suite uses a monkeypatched `urllib.request.urlopen`

### Verified behaviors

- `approved=False` (explicit or omitted) blocks the write and makes zero
  client calls.
- `dry_run=True` (the default) makes zero client calls even when approved,
  while still returning the exact rendered payload.
- A repo outside the allow-list is refused with zero client calls; the
  default allow-list (`GITHUB_DEMO_REPO` env var) is empty when unset, so
  nothing is writable by default.
- A pre-existing idempotency marker (deterministic SHA-256 of
  `cache_key:opportunity_id`) blocks a duplicate issue creation.
- `GITHUB_TOKEN` is read only inside the real client's request methods,
  never at import time or in `__init__`; a real (secret) token value never
  appears in a raised exception message or in the non-secret trace log
  (`logs/github-actions.jsonl`), verified with the real token value present
  in the environment during the test.
- Rendered issue content (problem, underlying need, affected segments,
  evidence quotes + `feedback_id`, evidence count, full score breakdown,
  roadmap context, contradicting evidence, evidence gaps, unresolved
  deterministic validation notes, recommended next step, run/cache
  reference) is drawn only from fields already present on the supplied
  `OrchestrationResult` -- nothing is fabricated.

### Status

No real GitHub issue was created. Per the task instructions, that step is
left as an explicit, separate, human-approved verification step (see
`docs/agent-progress.md` Stage 14).

---

## 2026-09-07 — Stage 15: n8n Workflow (Static Artifact Only)

### Implementation

Added `n8n/workflows/product-feedback-agent.json` (intake form -> Stage 13
orchestrator CLI -> structured review -> human approval gate -> Stage 14
github_action CLI only on approval, with a dead-end rejection path) and
`docs/n8n-setup.md`. Made two small CLI adjustments (JSON-only stdout on
`orchestrator.main()`, a new `github_action.main()` CLI) so the two
existing Python entry points pipe cleanly through n8n's Execute Command
node.

### Automated Test Result

Command:

`.\.venv\Scripts\python.exe -m pytest -q`

Result:

**271 / 271 tests passed**

Breakdown:

- 13 new static workflow-structure tests passed (`tests/test_n8n_workflow.py`)
- 258 previously-passing tests remained passing (2 of them updated, not
  weakened, for the stdout/stderr CLI split)
- 0 failures, 0 errors
- No network, Gemini, or GitHub requests were made
- No live n8n instance was reached

### Verified (statically) behaviors

- Removing only the Approval Gate's "approve" edge makes the GitHub-issue
  node unreachable from the intake form -- there is exactly one path to a
  GitHub mutation in this workflow graph, and it passes through the
  approval gate.
- The Approval Gate's conditions require the case-sensitive literal string
  `"approve"` plus a non-blank opportunity_id and a non-blank repo.
- The rejection node has no outgoing connection.
- The `github_action` CLI (with `--approved --no-dry-run`) is invoked from
  exactly one node in the whole workflow.
- The workflow JSON contains no hard-coded Windows user path and no
  embedded token/API-key value; `PFA_PROJECT_ROOT` is referenced only via
  an environment-variable lookup.

### Status

**HUMAN CHECKPOINT REQUIRED**: this workflow has not been imported into or
executed against a live n8n instance, and no real GitHub issue was created.
See `docs/agent-progress.md` Stage 15 for the exact human verification
steps remaining.

---

## 2026-09-07 — Stage 15 Live Finding + HTTP Boundary Repair

### Live finding

The Stage 15 workflow was imported into the user's real self-hosted n8n
Docker instance (`docker.n8n.io/n8nio/n8n`, container `n8n`, port
`5678:5678`). The import itself succeeded, but both `Execute Command` nodes
("Run Analysis (Python CLI)" and "Create GitHub Issue (Approved)") came up
as unrecognized "?" nodes: "Install this node to use it. This node is not
currently installed." Execute Command was not available on that instance.
Separately, even if it had been enabled, the command would have executed
*inside the n8n Docker container*, not against the Windows host's `.venv`
where this project's Python code actually runs -- so the Execute Command
design would not have worked for this local architecture regardless.

### Repair

Replaced the Execute Command integration boundary with an HTTP boundary:

```
n8n (Docker) -> HTTP Request node -> Product Feedback Agent HTTP service
(Windows host, host.docker.internal:8000) -> existing orchestrator.py /
github_action.py (unmodified)
```

- Added `src/product_feedback_agent/api.py`: a small Starlette + uvicorn
  HTTP service exposing `GET /health`, `POST /analyze` (delegates to the
  existing Stage 13 `run_analysis`), and `POST /github/issues` (delegates
  to the existing Stage 14 `create_opportunity_issue`). Both endpoints are
  thin request/response translation only -- no analysis or GitHub logic is
  duplicated, and every existing safety control (approved=True required,
  dry-run default-safe, repo allow-list, duplicate-marker prevention) is
  preserved because the HTTP layer only forwards to the unmodified
  function. Starlette and uvicorn were already transitive dependencies of
  `mcp[cli]`/`chromadb`; no new package was installed, only formalized as a
  direct dependency in `pyproject.toml`.
- Updated `n8n/workflows/product-feedback-agent.json`: the two Execute
  Command nodes were replaced with `n8n-nodes-base.httpRequest` nodes
  ("Run Analysis (HTTP)", "Create GitHub Issue (Approved)") calling
  `http://host.docker.internal:8000/analyze` and `.../github/issues`
  respectively. The two "Build ... Command" shell-quoting Code nodes were
  replaced with "Build Analyze Request" / "Build GitHub Issue Request",
  which build the equivalent JSON request bodies instead of shell commands
  -- the same data transformation (splitting the comma-separated
  context-files field, carrying `approved=true`/`dry_run=false`/the
  single-repo allow-list only on the approved path) is preserved. The
  `PFA_PROJECT_ROOT`/`PFA_PYTHON` environment variables the old Execute
  Command design needed are no longer referenced anywhere in the workflow.

### Automated Test Result

Command:

`.\.venv\Scripts\python.exe -m pytest -q`

Result:

**292 / 292 tests passed**

Breakdown:

- 15 new tests in `tests/test_api.py` (all offline; `TestClient` in-process,
  no socket opened): `/health`, `/analyze` delegates exactly once and
  returns exactly 3 opportunities, `/analyze` never invokes
  `create_opportunity_issue`, `/github/issues` delegates to
  `create_opportunity_issue`, an unapproved request is refused, dry-run
  stays safe by default, the default allow-list stays empty when
  `GITHUB_DEMO_REPO` is unset, and a duplicate idempotency marker is still
  refused through the HTTP layer (via a monkeypatched
  `GitHubRestIssueClient`, no real network) -- plus a full-stack test
  proving the HTTP boundary adds no Gemini requests beyond the existing
  2/3-request Group 1 budget.
- `tests/test_n8n_workflow.py` grew from 13 to 19 tests (2 obsolete
  CLI-command tests removed, 8 new ones added): no `Execute Command` node
  remains anywhere in the file; both Python boundaries use
  `n8n-nodes-base.httpRequest`; both HTTP Request URLs target
  `host.docker.internal` on the correct endpoint path
  (`/analyze`, `/github/issues`); the rejection path is still structurally
  incapable of reaching the GitHub HTTP Request node; approval is still
  required (unchanged existing tests); no hard-coded Windows path or secret
  is present in either new node.
- 271 previously-passing tests remained passing, unmodified.
- 0 failures, 0 errors.
- No network, Gemini, or GitHub requests were made in the test suite.
- No live n8n instance was reached during this repair.

### Status

The repaired workflow has **not yet been re-imported into or run against
the live n8n instance**. Live re-import/re-test remains a required human
verification step -- see `docs/agent-progress.md` Stage 15 for details. No
real GitHub issue was created and no real Gemini call was made during this
repair.

## 2026-09-07 — Stage 15 Live Validation Round 2 + Human Approval Form Repair

### Live validation results

The HTTP-repaired workflow (commit `dccfaca`) was re-imported into the
user's real self-hosted n8n Docker instance, on Windows:

1. The Product Feedback Agent HTTP API (`src/product_feedback_agent/api.py`)
   started successfully on the Windows host.
2. `GET http://localhost:8000/health` returned `status=ok`.
3. The repaired workflow JSON imported successfully into n8n (no import
   errors).
4. Both former Execute Command "?" nodes are now recognized, built-in
   `n8n-nodes-base.httpRequest` nodes -- the Stage 15 HTTP-boundary repair
   is confirmed live, not just statically tested.

### Live finding

One remaining compatibility issue was found: the "Human Review & Approval"
node imported with **Page Type = Form Ending** instead of **Page Type =
Next Form Page**. Form Ending is a terminal page type in the installed
Form node and has no further outgoing connection slot in the n8n UI, so a
node stuck on that page type would silently strand the human's decision
before it ever reaches the Approval Gate. Manually switching the field to
Next Form Page in the n8n UI exposed the installed node's page-mode
options: Define Form ("Using Fields Below" / "Using JSON") and Form
Elements.

### Repair

Updated only the "Human Review & Approval" node in
`n8n/workflows/product-feedback-agent.json` to use the installed Form
node's JSON-defined form mode instead of the UI field builder, so the
approval form no longer needs manual reconfiguration on import:

- `operation` changed from `"completion"` (Form Ending) to `"page"` (Next
  Form Page).
- `defineForm` set to `"json"` (Using JSON), with a `jsonOutput` string
  defining exactly three fields, matching the installed node's actual JSON
  field schema (`fieldLabel`/`fieldType`/`fieldOptions.values[].option`/
  `requiredField`, the same shape already used by the pre-existing Intake
  Form and Decision dropdown):
  - `Decision` -- dropdown, options `approve`/`reject` (lowercase, unchanged),
    required.
  - `opportunity_id` -- text, required, placeholder explaining that one of
    the 3 opportunity IDs shown in the summary must be supplied.
  - `repo` -- text, required, placeholder `owner/repository`.
- The opportunity summary (previously an inline `html` field) now renders
  via `formDescription` (`={{ $json.summaryMarkdown }}`), since the JSON
  field list itself is limited to exactly the three fields the downstream
  Approval Gate reads.
- The Approval Gate and "Build GitHub Issue Request" Code node were
  updated to read the new, shorter submitted field names (`$json['Decision']`,
  `$json['opportunity_id']`, `$json['repo']`) instead of the old
  UI-builder field labels (`opportunity_id to act on (leave blank to
  reject)`, `Target GitHub repo (owner/name)`). The safety gate itself is
  unchanged: `Decision == "approve"` (case-sensitive) AND a non-blank
  `opportunity_id` AND a non-blank `repo`, combined with `and`, still
  required before the GitHub HTTP Request node is reachable.

### Automated Test Result

Command:

`.\.venv\Scripts\python.exe -m pytest -q`

Result:

**301 / 301 tests passed**

Breakdown:

- `tests/test_n8n_workflow.py` grew from 19 to 28 tests (9 new): the Human
  Review node's `operation` is `"page"` and not `"completion"`; `defineForm`
  is `"json"`; the parsed `jsonOutput` contains exactly `Decision` (required
  dropdown, options `{approve, reject}`), `opportunity_id` (required text),
  and `repo` (required text, placeholder `owner/repository`), in that
  order and no others; the Approval Gate's three conditions reference
  exactly `$json['Decision']`, `$json['opportunity_id']`, `$json['repo']`;
  "Build GitHub Issue Request" reads the same two submitted field names;
  and the node's parameters contain no literal `"Form Ending"` string.
- All 292 previously-passing tests remained passing, unmodified.
- 0 failures, 0 errors.
- `git diff --check` reported no whitespace errors.
- No network, Gemini, or GitHub requests were made in the test suite. No
  live n8n instance was reached during this repair -- it was applied
  statically against the JSON file based on the live finding reported
  above.

### Status

**Not yet fully re-verified live.** The JSON-level form repair has not
been re-imported into the live n8n instance. One final live re-import is
still required to confirm: the node now shows as Next Form Page (not Form
Ending) on import, the JSON-defined `Decision`/`opportunity_id`/`repo`
fields render correctly in the n8n form UI with no further manual
reconfiguration, and a real approve/reject submission reaches the
Approval Gate with the expected field values. No real GitHub issue was
created and no real Gemini call was made during this repair. Group 3 has
not been started.

## 2026-09-07 — Stage 15 Live Validation Round 3 — `$env` access denied

### Live validation results

The human-approval-form-repaired workflow (commit `6ab44f1`) was
re-imported into the user's real self-hosted n8n Docker instance, on
Windows:

1. The workflow imported correctly; "Human Review & Approval" showed
   Page Type = Next Form Page, Define Form = Using JSON, with correct
   Decision/opportunity_id/repo fields -- confirming the round-2 human
   approval form repair live.
2. The Windows HTTP service was live: `GET http://localhost:8000/health`
   returned `status=ok`.

### Live finding

The first real n8n test failed in "Run Analysis (HTTP)" with `access to
env vars denied`. The installed n8n instance blocks `$env` access from
node expressions, so the URL expression that read an environment-variable
override (falling back to `http://host.docker.internal:8000`) could not
evaluate. No request reached the Python API and no Gemini call was made.

### Repair

Removed the environment-variable read from both HTTP Request node URLs
in `n8n/workflows/product-feedback-agent.json`. Each now uses a plain,
hardcoded, non-secret local Docker Desktop endpoint string instead:

- "Run Analysis (HTTP)": `http://host.docker.internal:8000/analyze`
- "Create GitHub Issue (Approved)": `http://host.docker.internal:8000/github/issues`

The Python API process remains responsible for its own `GEMINI_API_KEY`,
`GITHUB_TOKEN`, and `GITHUB_DEMO_REPO` configuration; n8n does not
receive or store those secrets. No other node, connection, or field
changed.

### Automated Test Result

Command:

`.\.venv\Scripts\python.exe -m pytest -q`

Result:

**305 / 305 tests passed** (301 baseline + 4 new).

Breakdown:

- `tests/test_n8n_workflow.py` grew from 28 to 32 tests (4 new): the
  workflow file contains no `$env` expression anywhere; contains no
  reference to the removed environment-variable override name; the
  "Run Analysis (HTTP)" URL is exactly
  `http://host.docker.internal:8000/analyze`; the "Create GitHub Issue
  (Approved)" URL is exactly
  `http://host.docker.internal:8000/github/issues`.
- All 301 previously-passing tests remained passing, unmodified.
- 0 failures, 0 errors.
- `git diff --check` reported no whitespace errors.
- No network, Gemini, or GitHub requests were made in the test suite. No
  live n8n instance was reached during this repair -- it was applied
  statically against the JSON file based on the live finding above.

### Status

**Not yet fully re-verified live.** The workflow has not been
re-imported into the live n8n instance since this repair. One final live
re-import/re-test is still required: confirm "Run Analysis (HTTP)" now
shows the plain hardcoded URL with no expression error, and that a real
form submission actually reaches the Windows-host Python API (and, from
there, produces a real Gemini-backed analysis and, on approval, a real
GitHub issue against a disposable demo repo) for the first time in this
workflow. No real GitHub issue was created and no real Gemini call was
made during this repair. Group 3 has not been started.

---

## 2026-09-07 — Live Stage 15 n8n Validation: Third Critic Contract Failure and Architectural Fix

### Observed live result

The `$env`-removed n8n workflow (commit `83652c3`) was run against the
user's real self-hosted n8n Docker instance, on Windows. This run made
real, confirmed progress beyond every prior Stage 15 attempt:

- The workflow imported and ran.
- "Run Analysis (HTTP)" successfully reached the Windows-host Python API:
  `POST /analyze` was received.
- Ingestion and retrieval began successfully inside the API process.
- A Hugging Face "unauthenticated requests" warning was logged; this was
  informational only -- the local MiniLM embedding weights loaded and were
  used successfully. It was NOT the failure.

This is the first live confirmation that Docker's `host.docker.internal`
networking, the imported n8n workflow, and the HTTP Request node all work
correctly end-to-end up to the point of reaching this repository's own
code.

The request then failed inside the Gemini critic stage. The real critic
returned `status="revise"` with `required_revisions` entirely missing.
`CriticWireResponse` (at the time, the `CriticPassResponse |
CriticReviseResponse` anyOf union from the prior fix) rejected it with
`CriticReviseResponse.required_revisions: Field required`. `run_critic`
raised `CriticError`, which propagated unhandled through Starlette and
produced a generic HTTP 500 with a raw traceback -- no structured error
reached n8n.

This is the same failure family recorded twice before in this log
("Critic Contract Failure and Fix" and "Second Critic Contract Failure and
Schema-Level Fix", both above, both `2026-09-07`), now observed a third
time, against a stronger schema-level enforcement than either prior fix.

### Conclusion

Prompt wording (fix 1) and a schema-level pass/revise `anyOf` union with
per-branch `minItems`/`maxItems` (fix 2) both failed to prevent the same
inconsistency a real Gemini call can still produce. The common root cause
across all three failures is architectural, not a matter of stricter
constraints: asking the model to produce `status` AND `required_revisions`
creates two pieces of state expressing the same thing, and the model can
make them disagree regardless of how tightly the schema constrains each
branch. The fix is to stop asking the model for `status` at all and derive
it deterministically in Python from `required_revisions`, which is the
only piece of state that actually needs to come from the model.

### Fix

- `src/product_feedback_agent/critic.py`: removed the pass/revise `anyOf`
  wire union entirely. `CriticWireResponse` is now one flat Pydantic model
  (`extra="forbid"`) with `issues`, `unsupported_claims`,
  `citation_errors`, `duplicate_opportunities`, and `required_revisions:
  list[str]` (no default, so it is always present in the JSON Schema's
  `"required"` list; an empty array is valid). There is no `status` field
  in this schema, and `build_critic_response_schema()` returns a plain
  object schema (no `anyOf`/`oneOf`).
- Added `derive_critic_status(required_revisions) -> CriticStatus`:
  non-empty => `REVISE`, empty => `PASS`. This is the only place
  `CriticStatus` is decided; it is never parsed from the model's raw JSON.
- `run_critic` parses the raw response as `CriticWireResponse` (rejecting
  a stray `status` field outright via `extra="forbid"`, not silently
  ignoring it), derives `status`, and constructs the unchanged domain
  `CriticOutput` (which still has a `status` field for
  `pipeline.py`/`orchestrator.py`/`api.py`) from the wire findings plus the
  derived status.
- The critic prompt (`build_critic_prompt`) no longer mentions `status`.
  It now instructs the model that `required_revisions` alone is its
  verdict: always present, an empty array when no change is needed, and
  one or more concrete actionable strings otherwise -- including for
  deterministic validation findings that require an analyst-side
  correction, preserving the Stage 12b safe-behavior guarantee.
- `src/product_feedback_agent/api.py` was hardened for the unhandled-500
  half of this finding: `/analyze` now catches `AnalystError`,
  `CriticError`, and `RevisionError` explicitly and returns
  `{"error": "analysis_failed", "stage": <analyst|critic|revision>,
  "error_type": <exception class name>}` with HTTP 502, instead of letting
  Starlette surface an unhandled ASGI exception. No exception message,
  model response content, or stack trace is included. Any other exception
  type still propagates unchanged.

No retries were added, the critic is still called at most once, and the
existing 2-request (pass) / 3-request (revise) total Gemini request budget
in `pipeline.py` is unchanged.

### Automated Test Result

Command:

`.\.venv\Scripts\python.exe -m pytest -q`

Result:

**313 / 313 tests passed** (305 baseline).

Breakdown:

- `tests/test_critic.py`: removed the obsolete pass/revise `anyOf`-union
  tests and the removed `CriticOutput` cross-field validator test; added
  tests proving the wire schema is one flat object with no
  `anyOf`/`oneOf` and no `status` property, `required_revisions` is a
  required array with no `minItems`/`maxItems`, a stray `status` field
  from the model is rejected outright, `derive_critic_status` maps `[]` to
  `PASS` and a non-empty list to `REVISE`, and `run_critic` derives both
  outcomes correctly from wire responses with no `status` field present at
  all.
- `tests/test_pipeline.py` / `tests/test_orchestrator.py`: fake critic
  payloads updated to the new wire shape (no `status` key); the few call
  sites that construct `CriticOutput` directly (the domain model) still
  supply `status` explicitly.
- `tests/test_api.py`: added tests proving a `CriticError`/`AnalystError`/
  `RevisionError` raised during `/analyze` comes back as the structured
  502 body above, with no leaked exception text anywhere in the response.
- All previously-passing tests remained passing, unmodified in behavior.
- 0 failures, 0 errors.
- `git diff --check` reported no whitespace errors.
- No network, Gemini, GitHub, or live n8n execution occurred during this
  repair -- it was applied statically based on the live finding above.

### Status

**Full successful live n8n analysis is NOT yet claimed.** This repair
confirms, for the first time live, that the n8n workflow can reach the
Windows-host API over `host.docker.internal` and that ingestion/retrieval
begin correctly -- real, valuable progress. It does not yet confirm a
successful live Gemini critic call, a completed revision path, or a
successful end-to-end run through Human Review & Approval and GitHub issue
creation. One controlled live retry is recommended next, per repository
instruction, to determine whether the redundant-`status` removal resolves
the critic failure in practice. The two prior critic-contract failures
recorded above, and this third one, are preserved as-is in this log as
reliability findings -- not erased or superseded, only built upon.
