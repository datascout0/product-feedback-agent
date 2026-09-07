# n8n Workflow Setup — Product Feedback Agent

Workflow file: `n8n/workflows/product-feedback-agent.json`

> **HUMAN CHECKPOINT REQUIRED.** This workflow was structurally validated
> (JSON well-formed, every node reachable, every connection target exists,
> no `Execute Command` node remains) but the **repaired** version below has
> **not yet been re-imported into or re-run against the live n8n instance**.
> A first version of this workflow *was* imported into a real self-hosted
> n8n Docker instance and the import succeeded, but its two
> `Execute Command` nodes came up as unrecognized "?" nodes ("Install this
> node to use it") — see "Live finding" below. The architecture was changed
> to an HTTP boundary in response; that change has only been verified
> offline (structural JSON tests + an offline HTTP-API test suite), not
> against the live instance. Do not treat this document as evidence that a
> live end-to-end n8n run occurred.

## Live finding (2026-09-07)

The workflow imported successfully into the user's real self-hosted n8n
instance (Docker, image `docker.n8n.io/n8nio/n8n`, container `n8n`, port
`5678:5678`). Opening either "Run Analysis (Python CLI)" or "Create GitHub
Issue (Approved)" (both `n8n-nodes-base.executeCommand` at the time) showed:

> Install this node to use it. This node is not currently installed. It is
> either from a newer version of n8n, a custom node, or has an invalid
> structure.

Separately, this project's repo/`.venv` runs on the **Windows host**, not
inside the n8n container — so even with `Execute Command` enabled, a
command run there would execute *inside the container*, not against this
project's `.venv`. The `Execute Command` design was not viable for this
local architecture, independent of the node-availability issue.

## Repaired architecture: HTTP boundary

```
n8n (Docker)
   -> HTTP Request node
   -> Product Feedback Agent HTTP service (Windows host, api.py)
   -> existing orchestrator.py / github_action.py (unmodified)
```

Docker Desktop containers reach the Windows host at `host.docker.internal`.
The two Python integration boundaries are now n8n's built-in **HTTP
Request** node, calling a small local HTTP service
(`src/product_feedback_agent/api.py`, Starlette + uvicorn) that exposes:

- `GET /health`
- `POST /analyze` — delegates to the existing Stage 13 `run_analysis`.
- `POST /github/issues` — delegates to the existing Stage 14
  `create_opportunity_issue`.

Neither endpoint duplicates any analysis or GitHub logic; both are thin
request/response translation over the existing, already-tested Python
functions, and every existing safety control (approved=True required for a
mutation, dry-run default-safe, the repo allow-list, duplicate-marker
prevention) is preserved unchanged.

## What this workflow does

```
Intake Form (human submits feedback/context paths + query)
        |
Build Analyze Request  --(Code node: builds the /analyze JSON body)
        |
Run Analysis (HTTP)  --(HTTP Request: POST http://host.docker.internal:8000/analyze)
        |
Parse Orchestration Result  --(Code node: wraps the response; fails loudly on an error body)
        |
Render Review Summary  --(Code node: formats the 3 opportunities, scores, unresolved issues)
        |
Human Review & Approval (Form)  --(human reads the summary, types an opportunity_id + repo, picks approve/reject)
        |
Approval Gate (If)
   |-- approve + opportunity_id + repo all present --> Build GitHub Issue Request
   |                                                      |
   |                                                    Create GitHub Issue (Approved)  --(HTTP Request: POST http://host.docker.internal:8000/github/issues)
   |                                                      |
   |                                                    Parse GitHub Action Result --> Approved Summary
   |
   \-- anything else (reject / blank / malformed) --> No Action (Rejected)  [dead end, no GitHub node reachable]
```

Core product logic (retrieval, the Gemini analyst/critic/revision pipeline,
deterministic validation/scoring, GitHub issue rendering and mutation) is
**never** re-implemented in n8n expressions or in `api.py`. Every "logic"
node in this workflow either calls the HTTP service (which itself only
forwards to the existing, already-tested `orchestrator.run_analysis` /
`github_action.create_opportunity_issue`) or does pure JSON
formatting/branching on their output.

## Where human approval happens

The **only** node that can authorize a GitHub write is **Human Review &
Approval**. The **Approval Gate** immediately after it requires all three of:

1. `Decision` exactly equals the string `approve` (case-sensitive).
2. A non-blank `opportunity_id to act on`.
3. A non-blank `Target GitHub repo`.

Anything else — `reject`, a blank decision, a timed-out/abandoned form, or a
decision of `approve` with no opportunity_id/repo named — routes to **No
Action (Rejected)**, which has no outgoing connection. There is no path in
this workflow graph from the Intake Form to a GitHub mutation that does not
pass through the Approval Gate's `approve` branch.

Even after the gate passes, `github_action.create_opportunity_issue` (the
Python function `POST /github/issues` calls, unmodified) independently
re-checks `approved=True`, `dry_run=False`, and the repo allow-list before
making any network request, and separately checks for an existing
duplicate-issue marker. Neither n8n nor `api.py` can bypass those checks —
the underlying function itself refuses an unapproved or disallowed write
regardless of what request it receives.

## Prerequisites

- n8n (Docker or local install) — no special node needs to be enabled;
  `HTTP Request` is a built-in n8n node available on every edition.
- The Product Feedback Agent repository checked out and its dependencies
  installed on the **Windows host** (`pip install -e .` inside the
  project's `.venv`) — this is where the HTTP service and the real
  analysis/GitHub logic run, not inside the n8n container.
- If n8n runs in Docker Desktop (Windows or Mac), the container can reach
  the Windows host at `host.docker.internal` with no extra network
  configuration. If n8n runs natively on Linux (not Docker Desktop),
  `host.docker.internal` may not resolve — use `--add-host
  host.docker.internal:host-gateway` on `docker run`, or set
  `PFA_API_BASE_URL` (below) to the host's real LAN/bridge IP instead.
- Environment variables:
  - On the **Windows host**, for the HTTP service process: `GEMINI_API_KEY`
    (required by the analyst/critic/revision pipeline), `GITHUB_TOKEN`
    (required only for the approved GitHub-action step), `PFA_API_HOST` /
    `PFA_API_PORT` (optional; default `0.0.0.0:8000`).
  - On the **n8n process** (optional): `PFA_API_BASE_URL` — overrides the
    default `http://host.docker.internal:8000` target if your setup needs a
    different host/port. Not required for the standard Docker Desktop case.

None of these values are stored in the workflow JSON or in this repository.

## Running a local demo

**Terminal 1 — start the Product Feedback Agent HTTP service (Windows host):**

```
cd <path-to-your-checkout>\product-feedback-agent
.\.venv\Scripts\python.exe -m product_feedback_agent.api
```

This binds `0.0.0.0:8000` by default (see `PFA_API_HOST`/`PFA_API_PORT`
above to change it). Confirm it's up:

```
curl http://127.0.0.1:8000/health
```

should return `{"status":"ok"}`.

**Terminal 2 / Docker — n8n:**

n8n is already running at `localhost:5678` (the existing Docker container,
`docker.n8n.io/n8nio/n8n`, port `5678:5678`). From inside that container,
`host.docker.internal:8000` addresses the Windows-host API started in
Terminal 1 — no extra Docker networking setup is required on Docker
Desktop.

**Then:**

1. Open n8n at `http://localhost:5678`, import (or re-import)
   `n8n/workflows/product-feedback-agent.json`, and open it.
2. Open the **Intake Form** node, copy its production/test form URL, and
   visit it. Submit:
   - Feedback file path: `data/samples/feedback_sample.csv` (resolved by
     the HTTP service relative to its project root, or an absolute path)
   - Context file paths: `data/samples/roadmap.md, data/samples/product_strategy.md`
   - Analysis query: any short phrase relevant to the sample feedback (see
     `data/samples/`)
3. Wait for **Run Analysis (HTTP)** to complete. This runs the real Group 1
   pipeline through `POST /analyze` — expect 1 real Gemini request for a
   critic `pass`, or 2 if the critic requests a revision, on top of the
   analyst request (2 or 3 total Gemini requests per the locked Stage 9-12
   budget; see `docs/decisions.md` D005). This step is NOT a dry run — it
   makes real Gemini API calls if `GEMINI_API_KEY` is set on the HTTP
   service process.
4. You are shown the **Human Review & Approval** form with the 3 rendered
   opportunities. To test the reject path: choose `reject` (or submit with
   opportunity_id/repo blank) and confirm the run ends at **No Action
   (Rejected)** with no GitHub node executed.
5. To test the approve path against a real repository: choose `approve`,
   type the `opportunity_id` (e.g. `opp_1`) you want to act on, and type
   the exact `owner/name` of a repository you control and are prepared to
   receive a real test issue in. This is the point at which a real GitHub
   issue CAN be created — do this deliberately, not as an automated test.
6. Inspect **Approved Summary** (or the raw output of **Create GitHub Issue
   (Approved)**): `status` will be `created` (a new issue was filed),
   `refused_duplicate` (an issue with this run's idempotency marker already
   exists), `refused_unsupported_repo`, or `refused_not_approved` — the
   underlying function's own guards, independent of what the n8n form or
   HTTP request body carried.

## What was NOT done by Claude Code in this session

- The repaired (HTTP-boundary) workflow was not imported into or executed
  live against the n8n instance. The prior version *was* imported live —
  see "Live finding" above — but that import surfaced the Execute Command
  incompatibility this document's repair addresses.
- No real GitHub issue was created (Stage 14's own scope also explicitly
  excludes this — see `docs/agent-progress.md` Stage 14).
- Node-parameter field names for the HTTP Request node (and the
  Form Trigger/Form/If/Set nodes carried over unchanged from the original
  import) were written from the current n8n workflow-JSON schema at
  authoring time; verify each node opens without a "node type
  unrecognized" or parameter-migration warning after import, and adjust
  field names if your installed version differs.
