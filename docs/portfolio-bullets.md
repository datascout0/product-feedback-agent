# Portfolio / CV Bullets

Grounded only in what was actually implemented and measured in this
repository (see `docs/results-log.md`/`docs/agent-progress.md` for the
underlying evidence for every claim below). No fabricated users, revenue,
adoption, conversion improvement, time savings, or production scale.

## Product thinking

- Designed a bounded, evidence-first product-discovery workflow (feedback
  -> opportunities -> underlying problems/needs -> evidence -> evidence
  gaps) that deliberately stops short of an autonomous roadmap decision,
  keeping a human as the sole approval boundary for any external action.
- Defined a transparent, published opportunity-scoring formula
  (evidence strength, severity, strategic alignment, segment breadth,
  confidence) with roadmap coverage explicitly excluded from the score, so
  scoring and prioritization stay legible and separable.

## AI system design

- Built a bounded 3-stage LLM pipeline (analyst -> deterministic validator
  -> critic -> at most one conditional revision) with a hard, structurally
  enforced request ceiling (exactly 2 or 3 model calls per run, never a
  4th) -- proven by both unit tests and a dedicated evaluation suite, not
  just documented.
- Diagnosed and fixed a recurring live LLM contract failure (a model
  producing an internally inconsistent `status` + `required_revisions`
  pair across three separate live attempts and two different schema-level
  mitigations) by identifying the redundant model-controlled field as the
  root cause and removing it from the wire contract entirely, deriving the
  status deterministically in code instead.

## Grounding / reliability

- Implemented a pure, deterministic validation layer that checks every
  model-produced claim (feedback-ID citations, verbatim-quote exactness,
  context citations, customer-segment support) against the evidence packet
  actually supplied to the model, and recomputes evidence counts/scores in
  code rather than trusting the model -- catching a real, live-observed
  failure mode (a `product_area` value used as if it were a customer
  segment) with a targeted, regression-tested check.
- Proved structurally, not just by convention, that customer feedback is
  treated as untrusted data: prompts embed feedback as JSON evidence after
  the fixed instruction text, and an evaluation case confirms
  prompt-injection-like feedback content stays confined to the evidence
  block and cannot bypass the GitHub-approval gate even when copied
  verbatim into rendered output.

## Deterministic evaluation

- Built a 34-case, fully offline evaluation suite (zero live model calls,
  zero live external mutations) covering 17 named requirements across
  grounding, scoring, critic-derivation, request-budget, and safety
  categories, reporting only measured pass/fail counts -- explicitly no
  fabricated accuracy/precision/recall/latency numbers -- and wired the
  same cases into the main automated test run so they can't silently drift
  from the code they check.
- Proved the architecture generalizes to a second domain (a synthetic
  customer-onboarding/support dataset) through the identical, unmodified
  pipeline code, with a static check confirming zero domain-specific
  branches anywhere in the core modules -- direct evidence the design is
  reusable, not coincidentally tied to its first dataset.

## Workflow automation

- Integrated a Python analysis/action backend with n8n via a small local
  HTTP bridge after diagnosing and repairing a live integration failure
  (n8n's `Execute Command` node unavailable/architecturally unsuitable for
  a host/container split), live-validating the repaired workflow end to
  end against a real self-hosted n8n instance.
- Diagnosed a live Gemini rate-limit failure mid-integration-testing and
  resolved it with a configurable model override
  (`PFA_GEMINI_MODEL`), without changing the pipeline's default model or
  its request budget.

## Human-in-the-loop governance

- Enforced human approval as a structural property, not a UI convention:
  the analysis-result schema has no field capable of representing an
  "approved" state, and the GitHub-action function independently
  re-verifies `approved=True`, a repo allow-list, and a deterministic
  duplicate-prevention marker before any external mutation, regardless of
  what any calling workflow sends.
- Live-validated both branches of the approval boundary end to end: a
  reject decision correctly making zero external calls, and an approve
  decision correctly creating exactly one real, evidence-backed GitHub
  issue against a disposable demo repository.

## Bounded model calls

- Enforced a hard ceiling of 3 model requests per analysis run (2 on the
  common path) at the code-structure level (no loop, no reachable 4th-call
  path) rather than by rate-limiting or prompt instruction alone, and
  verified the ceiling holds under real, live model responses.

## Reusable architecture

- Designed ingestion, retrieval, analyst, validation, scoring, critic, and
  human-approval interfaces to be domain-agnostic by construction, then
  verified that claim directly by running an unrelated synthetic dataset
  through the exact same code with zero modifications.
