# Product Feedback Agent — Implementation Checklist

Last updated: 2026-09-07

(Note: this file was previously saved with a mojibake-corrupted title/arrow
characters from an earlier encoding issue; this update rewrites it cleanly
in UTF-8 without changing any decision history recorded elsewhere.)

## Project Foundation

- [x] Create local project directory
- [x] Initialise Git repository
- [x] Configure `main` branch
- [x] Create private GitHub repository
- [x] Configure and verify `origin`
- [x] Create README
- [x] Create `.gitignore`
- [x] Create `.env.example`
- [x] Create decision log

## Product Decisions

- [x] Confirm 3-day MVP with optional Day 4
- [x] Rename project to Product Feedback Agent
- [x] Preserve human roadmap-decision boundary
- [x] Add Evidence-Backed Opportunity Tree concept
- [x] Lock bounded analyst → validator → critic → optional revision architecture

## Runtime Model

- [x] Select `gemini-3.7-flash`
- [x] Verify active project quota in Google AI Studio
- [x] Record RPM: 5
- [x] Record TPM: 250,000 input tokens/minute
- [x] Record RPD: 20
- [x] Set maximum passing run to 2 Gemini requests
- [x] Set maximum revision run to 3 Gemini requests
- [x] Set minimum sequential spacing to 14 seconds
- [x] Make the runtime model configurable (`PFA_GEMINI_MODEL`), default unchanged

## Python Foundation

- [x] Create Python 3.12 virtual environment
- [x] Create `src/product_feedback_agent`
- [x] Create `tests`
- [x] Create `data/samples`
- [x] Create `pyproject.toml`
- [x] Install Pydantic
- [x] Install pytest
- [x] Implement `FeedbackInput`
- [x] Implement `FeedbackRecord`
- [x] Add schema validation tests
- [x] Pass all 7 schema tests

## Feedback Ingestion

- [x] Implement CSV loading
- [x] Implement JSON loading
- [x] Validate required fields
- [x] Generate stable IDs when missing
- [x] Preserve original feedback text
- [x] Normalise source names
- [x] Normalise dates
- [x] Preserve optional metadata
- [x] Remove exact duplicates deterministically
- [ ] Flag likely semantic duplicates without automatic deletion
- [x] Report invalid records safely
- [x] Enforce reasonable V1 dataset-size limit
- [x] Add ingestion tests

## Product Context

- [x] Define product-context schema
- [x] Ingest Markdown/text product-context documents
- [x] Preserve stable document IDs
- [x] Preserve stable section IDs
- [x] Add product-context tests

## Embeddings and Retrieval

- [x] Select local embedding model
- [x] Implement feedback embeddings
- [x] Implement product-context embeddings
- [x] Configure local vector store
- [x] Implement semantic feedback search
- [x] Implement product-context retrieval
- [x] Implement diversity sampling
- [x] Implement deterministic dataset statistics
- [x] Implement cached analysis-context packet

## MCP

- [x] Implement `prepare_analysis_context`
- [x] Implement `search_feedback`
- [x] Implement `create_opportunity_issue`
- [x] Validate MCP tools with Inspector
- [ ] Validate MCP server with Claude Code
- [ ] Save known-good context-packet fixtures

## Analyst and Opportunity Model

- [x] Define opportunity-card schema
- [x] Add Evidence-Backed Opportunity Tree structure
- [x] Write Gemini analyst prompt
- [x] Produce exactly 3 opportunity candidates
- [x] Distinguish requested solutions from underlying needs
- [x] Require evidence citations
- [x] Add clarification-question logic (`discovery_questions`)

## Validation and Scoring

- [x] Validate opportunity schema deterministically
- [x] Reject nonexistent feedback IDs
- [x] Verify exact quotations
- [x] Recompute evidence counts locally
- [x] Implement transparent opportunity scoring
- [x] Keep roadmap coverage separate from score
- [x] Reject duplicate opportunity IDs (Stage 16)

## Critic Workflow

- [x] Write Gemini critic prompt
- [x] Validate evidence support
- [x] Detect duplicate opportunities
- [x] Check contradictory evidence
- [x] Check unsupported segment claims
- [x] Check roadmap/context citations
- [x] Implement conditional single revision
- [x] Demonstrate one seeded revision case
- [x] Complete controlled live end-to-end validation (analyst -> critic -> revision), 0 remaining deterministic issues

## n8n

- [x] Build intake form
- [x] Connect Python ingestion
- [x] Call one batched analysis-context operation
- [x] Add Gemini analyst
- [x] Add deterministic validator
- [x] Add Gemini critic
- [x] Add conditional revision
- [x] Display structured results
- [x] Add human approval
- [x] Export final workflow JSON
- [x] Live-validate the full workflow against a real n8n instance (see
      `docs/n8n-setup.md` and `docs/results-log.md`, "Live Stage 15 Success")

Note: ingestion/analysis-context/analyst/validator/critic/revision are not
separate n8n nodes -- per the task instructions ("prefer invoking reusable
application/MCP/API boundaries rather than copying Python business logic
into n8n"), the workflow's "Run Analysis (HTTP)" node calls the small local
HTTP service (`src/product_feedback_agent/api.py`), which delegates
directly to the single reusable Stage 13 `product_feedback_agent.orchestrator.run_analysis`,
which performs all of them internally, unmodified.

## GitHub REST Action

- [x] Implement dry-run opportunity issue creation
- [x] Require explicit approval
- [x] Restrict to allow-listed demo repository
- [x] Add duplicate-action prevention
- [x] Create one approved real GitHub issue (`datascout0/product-feedback-agent`
      issue #1, "[Opportunity] Search Result Filtering Friction" -- see
      `docs/results-log.md`, "Live Stage 15 Success")
- [x] Demonstrate that an unapproved action is blocked

## Evaluation

- [x] Create a deterministic evaluation suite (`eval/`) covering the 17
      Stage 16 requirements (grounding, scoring, critic/budget, safety)
- [x] Measure citation correctness (deterministic, offline)
- [x] Measure exact-quote correctness (deterministic, offline)
- [x] Record unsupported claims (deterministic validation issues)
- [x] Test prompt-injection-like feedback is treated as data, not instruction
- [x] Test empty/weak retrieval behavior (no fabricated evidence)
- [x] Test action safety (approval, allow-list, idempotency)
- [x] Record measured Gemini request counts (2 pass / 3 revise, never 4)
- [x] Run a secondary-domain dataset through the unmodified pipeline (Stage 17)
- [ ] Retrieval Recall@k over a larger labelled gold set (out of scope for
      this offline, deterministic evaluation suite -- would require a much
      larger hand-labelled dataset than this MVP's samples)

## Portfolio Assets

- [x] Complete README
- [x] Complete architecture documentation (`docs/architecture.md`)
- [x] Complete evaluation documentation (`eval/evaluation_report.md`,
      `docs/results-log.md`)
- [x] Document the screenshot/evidence plan (`docs/screenshots.md`)
- [x] Write a concise demo script (`docs/demo-script.md`)
- [x] Create evidence-based CV/portfolio bullets (`docs/portfolio-bullets.md`)
- [x] Document limitations (README + `docs/architecture.md`)
- [ ] Capture the actual screenshots listed in `docs/screenshots.md` (human
      action against the live n8n/GitHub UI, not something Claude Code can
      capture itself)
