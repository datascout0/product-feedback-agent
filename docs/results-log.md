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