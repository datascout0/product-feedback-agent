# Screenshot / Evidence Plan

This identifies which parts of the demo are worth a screenshot for a
portfolio/demo, and which are already better evidenced by committed,
source-controlled test/evaluation output. No screenshot is listed here that
hasn't actually been produced by a real run described in
`docs/results-log.md` -- this file plans *which* captures matter, it does
not claim they already exist as image files in this repository.

## Worth a screenshot (UI-only evidence; no test output shows this)

1. **n8n reject path.** The "Human Review & Approval" form submitted with
   `Decision = reject`, and the resulting run ending at **No Action
   (Rejected)** in the n8n execution view. This is the clearest visual
   proof of the human-approval boundary actually working live.
2. **n8n approve path.** The same form submitted with `Decision = approve`
   plus a real `opportunity_id`/`repo`, and the resulting **Approved
   Summary** node output (or the raw `POST /github/issues` response)
   showing `status: "created"` and a real `issue_url`.
3. **The human approval form itself**, mid-fill, showing the rendered
   opportunity summary (`formDescription`) alongside the `Decision` /
   `opportunity_id` / `repo` fields -- this is the one moment a human
   actually exercises judgment in the whole workflow, and is worth showing
   as a UI, not just describing.
4. **The real created GitHub issue** (`datascout0/product-feedback-agent`
   issue #1, "[Opportunity] Search Result Filtering Friction") as rendered
   by GitHub's own UI -- title, labels, and the evidence-backed body
   (quotes, `feedback_id`s, score breakdown, idempotency marker comment).

These four are already live-confirmed per `docs/results-log.md` ("Live
Stage 15 Success") -- capturing them is a human action against the live
n8n/GitHub UI in this session's environment, not something this repository
or Claude Code can produce as a file.

## Not worth a screenshot (better shown as committed output)

- **Exactly 3 opportunities / schema enforcement** -- shown far more
  convincingly by `AnalystOutput`'s `min_length=3, max_length=3` and the
  passing `eval/cases.py` R1a/R1b/R1c cases than by a screenshot of a form.
- **Citation/quote/segment grounding** -- `eval/evaluation_report.md`'s
  per-case detail (R3-R5, R16, R17) is a more precise, reproducible record
  than a screenshot of one example.
- **Deterministic scoring** -- the formula and its boundary tests
  (`tests/test_validation.py`, `eval/cases.py` R7) are exact; a screenshot
  of one computed number adds nothing a reader can verify.
- **The 2-request / 3-request model budget** -- proven by
  `tests/test_pipeline.py` and `eval/cases.py` R9/R10, and by the real
  trace-log entries quoted in `docs/results-log.md` (timestamps, durations,
  token counts) -- more precise than a screenshot of a log line.
- **Secondary-domain reuse** -- `tests/test_secondary_domain.py`'s passing
  output and the "no domain-specific branch" static check are the actual
  evidence; a screenshot of a second CSV file proves nothing by itself.
- **Full pytest suite result** -- a terminal screenshot of `pytest -q` is
  strictly less useful than the actual command in
  `docs/results-log.md`/`docs/agent-progress.md`, which a reader can
  re-run themselves.

## Principle

A screenshot earns its place only where the UI *is* the evidence (a human
decision happening, or an external system's own rendering of something this
project created). Everywhere the evidence is a deterministic, re-runnable
check, the committed test/evaluation output is the stronger artifact --
duplicating it as a screenshot would only make it staler and harder to
verify.
