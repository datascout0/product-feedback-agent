"""Deterministic evaluation suite for Product Feedback Agent (Stage 16).

Everything in this package runs fully offline: no live Gemini call, no live
GitHub mutation, no network dependency of any kind. Every case exercises the
real, unmodified production functions (`validate_analyst_output`,
`run_pipeline`, `create_opportunity_issue`, ...) against fixtures/fakes built
in this package -- it never re-implements or mocks the logic under test.

Entry points:
- `eval.cases.run_all()` returns the full list of `EvalCaseResult`s.
- `python -m eval.run_evaluation` runs the suite and writes
  `eval/evaluation_report.md` / `eval/evaluation_report.json` with only
  actually-measured counts (no invented accuracy/precision/recall/latency).
"""
