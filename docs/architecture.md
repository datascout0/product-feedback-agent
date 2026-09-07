# Architecture

This diagram shows the actual, implemented pipeline -- not an aspirational
version of it. Every box corresponds to a real module or n8n node named
below it.

```mermaid
flowchart TD
    A["CSV / JSON feedback\n+ Markdown/text product context"] --> B["Ingestion / normalization\ningestion.py, context.py"]
    B --> C["Local embeddings + Chroma\nembeddings.py, retrieval.py"]
    C --> D["Batched retrieval / analysis-context packet\nanalysis_context.py\n(ONE retrieval call, cached by content hash)"]
    D --> E["Gemini analyst\nanalyst.py -- model request 1"]
    E --> F["Deterministic validation + scoring\nvalidation.py -- NO model call"]
    F --> G["Gemini critic\ncritic.py -- model request 2"]
    G -->|"required_revisions empty"| H["PipelineResult\nrevised=False, 2 requests total"]
    G -->|"required_revisions non-empty"| I["ONE revision\npipeline.py -- model request 3"]
    I --> J["Re-validate deterministically\nvalidation.py -- NO model call"]
    J --> K["PipelineResult\nrevised=True, 3 requests total"]
    H --> L["OrchestrationResult\norchestrator.py"]
    K --> L
    L --> M["Human review\nhuman_review_status=pending_review"]
    M -->|"approve + opportunity_id + repo"| N["Approved GitHub issue\ngithub_action.py\napproval + allow-list + idempotency gated"]
    M -->|"reject / blank / anything else"| O["No action\n(dead end, zero GitHub calls)"]

    subgraph N8N["n8n orchestration (human-facing workflow only)"]
        P["Intake Form"] --> Q["Build Analyze Request"]
        Q --> R["HTTP Request: POST /analyze"]
        R --> S["Render Review Summary"]
        S --> T["Human Review & Approval (Form)"]
        T --> U{"Approval Gate\nDecision==approve AND\nopportunity_id AND repo"}
        U -->|yes| V["HTTP Request: POST /github/issues"]
        U -->|no| W["No Action (Rejected)\ndead end"]
    end

    subgraph BRIDGE["Python HTTP bridge (api.py, Windows host)"]
        X["POST /analyze\n-> run_analysis() unmodified"]
        Y["POST /github/issues\n-> create_opportunity_issue() unmodified"]
    end

    R -.->|"host.docker.internal:8000"| X
    V -.->|"host.docker.internal:8000"| Y
    X -.-> D
    Y -.-> N

    classDef deterministic fill:#dff0d8,stroke:#3c763d,color:#1a1a1a;
    classDef modelControlled fill:#fcf3cf,stroke:#9c7a05,color:#1a1a1a;
    classDef human fill:#d9edf7,stroke:#31708f,color:#1a1a1a;
    class B,C,D,F,J,N deterministic;
    class E,G,I modelControlled;
    class M,T human;
```

## Deterministic vs. model-controlled components

| Component | Deterministic (code) | Model-controlled (Gemini) |
|---|---|---|
| Ingestion / normalization | Yes | -- |
| Embeddings + Chroma retrieval | Yes | -- |
| Analysis-context packet (stats, diversity sampling, cache key) | Yes | -- |
| Opportunity drafting | -- | Yes (analyst, request 1) |
| Citation/quote/segment validation, `evidence_count`, score | Yes | -- |
| Reasoning-quality review (`required_revisions`) | -- | Yes (critic, request 2) |
| `PASS`/`REVISE` status | Yes (`derive_critic_status`, from `required_revisions`) | -- |
| One bounded revision draft | -- | Yes (revision, request 3, conditional) |
| Re-validation after revision | Yes | -- |
| GitHub issue payload rendering | Yes | -- |
| Approval / allow-list / idempotency decision | Yes (code) + human (the approval itself) | -- |

## Request ceiling

Every run makes **exactly 2** model requests (analyst + critic, critic
returns no required revisions) **or exactly 3** (analyst + critic + one
revision). There is no loop anywhere in `pipeline.py`, and no code path can
reach a 4th request -- this is proven by `tests/test_pipeline.py` and
`eval/cases.py` (R9/R10), not just documented.

## n8n orchestration and the Python HTTP bridge

n8n owns only the human-facing workflow: an intake form, a review/approval
form, and branching on the approval decision. It contains no retrieval,
analyst/critic/revision, validation, scoring, or GitHub-mutation logic of
its own -- both "HTTP Request" nodes call a small local Starlette+uvicorn
service (`src/product_feedback_agent/api.py`) running on the Windows host,
which itself only forwards, unmodified, to `orchestrator.run_analysis` and
`github_action.create_opportunity_issue`. Docker Desktop's n8n container
reaches that service at `host.docker.internal:8000`. See
`docs/n8n-setup.md` for the full setup guide and
`docs/results-log.md` ("Live Stage 15 Success") for the live end-to-end
validation of this exact path, including the reject path making zero
GitHub calls and the approve path creating one real GitHub issue.

## Why the request ceiling and the human-approval boundary are both load-bearing

The two together are what make this safe to demo against a real GitHub
repository and a metered Gemini quota at the same time: the request ceiling
bounds cost and API usage per run regardless of how the critic responds,
and the approval boundary means a bug, a bad model response, or a
misconfigured workflow can produce a wrong *recommendation* but never a
silent, unapproved external mutation.
