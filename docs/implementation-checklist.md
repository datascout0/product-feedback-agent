# Product Feedback Agent ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â Implementation Checklist

Last updated: 2026-09-02

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
- [x] Lock bounded analyst ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ validator ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ critic ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚Â ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ optional revision architecture

## Runtime Model

- [x] Select `gemini-3.7-flash`
- [x] Verify active project quota in Google AI Studio
- [x] Record RPM: 5
- [x] Record TPM: 250,000 input tokens/minute
- [x] Record RPD: 20
- [x] Set maximum passing run to 2 Gemini requests
- [x] Set maximum revision run to 3 Gemini requests
- [x] Set minimum sequential spacing to 14 seconds

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

- [ ] Define opportunity-card schema
- [ ] Add Evidence-Backed Opportunity Tree structure
- [x] Write Gemini analyst prompt
- [x] Produce exactly 3 opportunity candidates
- [ ] Distinguish requested solutions from underlying needs
- [ ] Require evidence citations
- [ ] Add clarification-question logic

## Validation and Scoring

- [x] Validate opportunity schema deterministically
- [x] Reject nonexistent feedback IDs
- [x] Verify exact quotations
- [x] Recompute evidence counts locally
- [x] Implement transparent opportunity scoring
- [x] Keep roadmap coverage separate from score

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

- [ ] Build intake form
- [ ] Connect Python ingestion
- [ ] Call one batched analysis-context operation
- [x] Add Gemini analyst
- [ ] Add deterministic validator
- [ ] Add Gemini critic
- [ ] Add conditional revision
- [ ] Display structured results
- [ ] Add human approval
- [ ] Export final workflow JSON

## GitHub REST Action

- [x] Implement dry-run opportunity issue creation
- [x] Require explicit approval
- [x] Restrict to allow-listed demo repository
- [x] Add duplicate-action prevention
- [ ] Create one approved real GitHub issue
- [x] Demonstrate that an unapproved action is blocked

## Evaluation

- [ ] Create labelled feedback gold set
- [ ] Define expected opportunity themes
- [ ] Create retrieval evaluation queries
- [ ] Measure Recall@5
- [ ] Measure citation correctness
- [ ] Measure exact-quote correctness
- [ ] Record unsupported claims
- [ ] Test prompt injection
- [ ] Test empty retrieval behavior
- [ ] Test action safety
- [ ] Record latency and Gemini request counts
- [ ] Run secondary-domain dataset

## Portfolio Assets

- [ ] Complete README
- [ ] Complete architecture documentation
- [ ] Complete evaluation documentation
- [ ] Capture screenshots
- [ ] Record 60ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã¢â‚¬Å“90 second demo
- [ ] Create evidence-based CV bullets
- [ ] Document limitations