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

**Status:** pending

---

## Stage 12 — One conditional revision

**Status:** pending

---

## Next step

Implement Stage 11 (`src/product_feedback_agent/critic.py`), mirroring
`analyst.py`'s lazy-client / dependency-injection / structured-output /
one-request pattern, consuming the `AnalysisContextPacket`, the
`AnalystOutput` draft, and the Stage 10 `ValidationResult`.
