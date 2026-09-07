# n8n Workflow Setup — Product Feedback Agent

Workflow file: `n8n/workflows/product-feedback-agent.json`

> **HUMAN CHECKPOINT REQUIRED.** This workflow was authored and structurally
> validated (JSON well-formed, every node reachable, every connection target
> exists) but was **not** imported into or run against a live n8n instance
> by Claude Code during this session — Claude Code had no access to a
> running localhost n8n instance. Live import, node-parameter compatibility
> with your installed n8n version, and an end-to-end run are a required
> human verification step before this workflow is considered demo-ready.
> Do not treat this document as evidence that a live n8n run occurred.

## What this workflow does

```
Intake Form (human submits feedback/context paths + query)
        |
Build Analysis Command  --(Code node, shell-quotes inputs)
        |
Run Analysis (Python CLI)  --(Execute Command: python -m product_feedback_agent.orchestrator)
        |
Parse Orchestration Result  --(Code node: JSON.parse(stdout); fails loudly on non-zero exit)
        |
Render Review Summary  --(Code node: formats the 3 opportunities, scores, unresolved issues)
        |
Human Review & Approval (Form)  --(human reads the summary, types an opportunity_id + repo, picks approve/reject)
        |
Approval Gate (If)
   |-- approve + opportunity_id + repo all present --> Build GitHub Action Command
   |                                                      |
   |                                                    Create GitHub Issue (Approved)  --(Execute Command: python -m product_feedback_agent.github_action --approved --no-dry-run)
   |                                                      |
   |                                                    Parse GitHub Action Result --> Approved Summary
   |
   \-- anything else (reject / blank / malformed) --> No Action (Rejected)  [dead end, no GitHub node reachable]
```

Core product logic (retrieval, the Gemini analyst/critic/revision pipeline,
deterministic validation/scoring, GitHub issue rendering and mutation) is
**never** re-implemented in n8n expressions. Every "logic" node in this
workflow either shells out to the existing, already-tested Python CLIs
(`product_feedback_agent.orchestrator`, `product_feedback_agent.github_action`
— see Stage 13/14) or does pure JSON formatting/branching on their output.

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
Python function the CLI calls) independently re-checks `approved=True`,
`dry_run=False`, and the repo allow-list before making any network request,
and separately checks for an existing duplicate-issue marker. n8n cannot
bypass those checks by, for example, being misconfigured to always route to
the approved branch — the CLI itself refuses an unapproved or disallowed
write regardless of what n8n sends it.

## Prerequisites

- n8n (Docker or local install) with the **Execute Command** node enabled.
  Some managed/cloud n8n instances disable Execute Command for security
  reasons — this workflow requires a self-hosted instance where it is
  allowed.
- The Product Feedback Agent repository checked out and its dependencies
  installed (`pip install -e .`) somewhere the n8n process can execute
  `python`.
- A POSIX shell (`bash`/`sh`) available to Execute Command — the workflow's
  Code nodes build `cd ... && python ...` commands and a `cat > file <<EOF`
  heredoc for the GitHub-action step. This works out of the box on n8n's
  official Docker image (Linux) or n8n running under WSL. **It does not
  work if n8n's Execute Command runs through `cmd.exe`** (e.g. n8n
  installed natively on Windows without WSL) — use the Docker image or WSL
  instead, or adapt the two "Build ... Command" Code nodes to emit a
  PowerShell-compatible command for your environment.
- Environment variables set on the **n8n process** (not in the workflow
  JSON, and never inside a Code node): 
  - `PFA_PROJECT_ROOT` — absolute path to the cloned repository on the n8n
    host (e.g. `/opt/product-feedback-agent` in a container, or your own
    checkout path). This is intentionally supplied by you at setup time,
    not hard-coded in the workflow.
  - `PFA_PYTHON` — optional; path to the Python executable with this
    project installed. Defaults to `python` on the PATH if unset.
  - `GEMINI_API_KEY` — required by the analyst/critic/revision pipeline.
  - `GITHUB_TOKEN` — required only for the approved GitHub-action step.
  - `GITHUB_DEMO_REPO` — not required by the workflow itself (the workflow
    passes `--allowed-repo` explicitly from the human-supplied repo field),
    but you may still want it set for direct CLI use outside n8n.

None of these values are stored in the workflow JSON or in this repository.

## Import steps

1. Open your n8n instance -> **Workflows** -> **Import from File** (or
   **Import from URL** if you host the raw file) -> select
   `n8n/workflows/product-feedback-agent.json`.
2. Set the environment variables above on the n8n host/container, then
   restart n8n if it does not pick up new environment variables live.
3. Open the imported workflow and re-check each **Execute Command** node's
   `command` expression renders as expected for your shell (Docker/WSL
   bash assumed — see Prerequisites).
4. Open the **Intake Form** node, copy its production/test form URL.

## Running a local demo

1. Visit the Intake Form URL. Submit:
   - Feedback file path: `data/samples/feedback_sample.csv` (relative to
     `PFA_PROJECT_ROOT`, or an absolute path)
   - Context file paths: `data/samples/roadmap.md, data/samples/product_strategy.md`
   - Analysis query: any short phrase relevant to the sample feedback (see
     `data/samples/`)
2. Wait for **Run Analysis (Python CLI)** to complete. This runs the real
   Group 1 pipeline — expect 1 real Gemini request for a critic `pass`, or
   2 if the critic requests a revision, on top of the analyst request
   (2 or 3 total Gemini requests per the locked Stage 9-12 budget; see
   `docs/decisions.md` D005). This step is NOT a dry run — it makes real
   Gemini API calls if `GEMINI_API_KEY` is set.
3. You are shown the **Human Review & Approval** form with the 3 rendered
   opportunities. To test the reject path: choose `reject` (or submit with
   opportunity_id/repo blank) and confirm the run ends at **No Action
   (Rejected)** with no GitHub node executed.
4. To test the approve path against a real repository: choose `approve`,
   type the `opportunity_id` (e.g. `opp_1`) you want to act on, and type
   the exact `owner/name` of a repository you control and are prepared to
   receive a real test issue in. This is the point at which a real GitHub
   issue CAN be created — do this deliberately, not as an automated test.
5. Inspect **Approved Summary** (or the raw output of **Create GitHub Issue
   (Approved)**): `status` will be `created` (a new issue was filed),
   `refused_duplicate` (an issue with this run's idempotency marker already
   exists), `refused_unsupported_repo`, or `refused_not_approved` — the
   underlying CLI's own guards, independent of what the n8n form collected.

## What was NOT done by Claude Code in this session

- No live n8n instance was reached; this workflow was not imported or
  executed live.
- No real GitHub issue was created (Stage 14's own scope also explicitly
  excludes this — see `docs/agent-progress.md` Stage 14).
- Node-parameter field names for the Form Trigger/Form/If/Set nodes were
  written from the current n8n workflow-JSON schema at authoring time,
  but n8n's form-node field schema in particular has changed across
  versions; verify each node opens without a "node type unrecognized" or
  parameter-migration warning after import, and adjust field names if your
  installed version differs.
