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

## Next step (Group 2)

Group 1 (Stages 10-12) is complete. Group 2 work -- explicitly out of scope
for this session -- starts with: end-to-end n8n integration wiring analyst
-> validator -> critic -> revision -> human approval, GitHub issue creation
(dry-run first, then one approved real issue), the evaluation suite, and
secondary-domain reuse. See the "n8n", "GitHub REST Action", and
"Evaluation" sections of `docs/implementation-checklist.md` for the
itemized remaining work.
