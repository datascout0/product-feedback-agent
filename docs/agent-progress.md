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

**Status:** pending

---

## Next step

Implement Stage 12: a small orchestration function (e.g.
`src/product_feedback_agent/pipeline.py`) that runs analyst (1 request) ->
deterministic validation -> critic (2nd request) -> if `status == "revise"`,
exactly one revision request (3rd, final) -> re-run deterministic
validation locally. Must never exceed 3 model requests total. Revision
client will need its own small Pydantic output schema (still exactly three
opportunities) and prompt, reusing `OpportunityDraft`/`AnalystOutput` shapes
where possible rather than inventing a new one.
