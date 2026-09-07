"""Human-approved GitHub opportunity issue creation.

Design notes (see docs/decisions.md D002):
- This module contains NO analysis logic. It only renders an issue payload
  from an already-produced `OrchestrationResult` (Stage 13) and, only when
  explicitly approved, submits it. It never decides which opportunity
  matters -- a human names the `opportunity_id` and passes `approved=True`.
- `approved` defaults to False and `dry_run` defaults to True: the safe
  default for every call is "render only, mutate nothing".
- No GitHub mutation happens unless `approved=True` AND `dry_run=False` AND
  the target `repo` is in the allow-listed set. All three failing
  independently produce a structured refusal, never a raised exception and
  never a silent success.
- The GitHub token is read from the environment lazily, only inside
  `GitHubRestIssueClient._headers` (called only when a real network request
  is about to happen) -- never at import time, never in `__init__`, and
  never included in a raised exception message or the non-secret trace log.
- Duplicate prevention: every rendered issue body carries a deterministic
  HTML-comment marker derived from (cache_key, opportunity_id). Before
  creating a real issue, the target repo is searched for that marker; a
  match refuses creation instead of filing a duplicate.
- Repository safety: only repos in `allowed_repos` (explicit argument) or,
  if omitted, the comma-separated `GITHUB_DEMO_REPO` environment variable
  can ever be written to. No local filesystem path is involved.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, Sequence

from pydantic import BaseModel, ConfigDict

from product_feedback_agent.orchestrator import OrchestrationResult
from product_feedback_agent.validation import ValidatedOpportunity

GITHUB_TOKEN_ENV = "GITHUB_TOKEN"
GITHUB_DEMO_REPO_ENV = "GITHUB_DEMO_REPO"
GITHUB_API_BASE = "https://api.github.com"
DEFAULT_ACTION_LOG_PATH = "logs/github-actions.jsonl"

_ISSUE_LABELS = ["product-feedback-agent", "opportunity"]


class GitHubActionError(Exception):
    """Raised only when a real, approved GitHub request cannot be completed."""


class IssueCreationStatus(str, Enum):
    DRY_RUN = "dry_run"
    REFUSED_NOT_APPROVED = "refused_not_approved"
    REFUSED_UNSUPPORTED_REPO = "refused_unsupported_repo"
    REFUSED_DUPLICATE = "refused_duplicate"
    CREATED = "created"


class IssuePayload(BaseModel):
    """The exact, evidence-backed content that would be (or was) sent to GitHub."""

    model_config = ConfigDict(extra="forbid")

    title: str
    body: str
    labels: list[str]
    idempotency_marker: str


class IssueActionResult(BaseModel):
    """The complete, structured outcome of one create_opportunity_issue call.

    Always returned, never raised, for every refusal path -- callers can
    inspect `status` without needing to catch an exception for the ordinary
    "not approved" / "unsupported repo" / "duplicate" cases.
    """

    model_config = ConfigDict(extra="forbid")

    status: IssueCreationStatus
    repo: str
    opportunity_id: str
    payload: IssuePayload
    approved: bool
    dry_run: bool
    issue_url: str | None = None
    message: str


# ---------------------------------------------------------------------------
# Payload rendering (pure, no I/O, no fabricated content)
# ---------------------------------------------------------------------------


def _find_opportunity(result: OrchestrationResult, opportunity_id: str):
    for opportunity in result.opportunities:
        if opportunity.opportunity_id == opportunity_id:
            return opportunity
    raise GitHubActionError(f"opportunity_id '{opportunity_id}' not found in this result.")


def _find_validated_opportunity(result: OrchestrationResult, opportunity_id: str) -> ValidatedOpportunity:
    for validated in result.validation_result.opportunities:
        if validated.opportunity_id == opportunity_id:
            return validated
    raise GitHubActionError(
        f"opportunity_id '{opportunity_id}' has no matching deterministic validation result."
    )


def build_idempotency_marker(cache_key: str, opportunity_id: str) -> str:
    """A deterministic, stable marker embedded in every rendered issue body.

    Same (cache_key, opportunity_id) always yields the same marker, so a
    duplicate-check search for this exact string reliably finds a
    previously created issue for the same opportunity from the same run.
    """
    digest = hashlib.sha256(f"{cache_key}:{opportunity_id}".encode("utf-8")).hexdigest()[:16]
    return f"<!-- product-feedback-agent:opportunity:{digest} -->"


def render_issue_payload(result: OrchestrationResult, opportunity_id: str) -> IssuePayload:
    """Render the exact issue payload for one opportunity. No I/O, no model call.

    Only fields already present on the supplied `OrchestrationResult` are
    used -- nothing here invents evidence, scores, or context.
    """
    opportunity = _find_opportunity(result, opportunity_id)
    validated = _find_validated_opportunity(result, opportunity_id)
    marker = build_idempotency_marker(result.cache_key, opportunity_id)

    lines: list[str] = []
    lines.append(f"## Problem / Job to be Done\n\n{opportunity.problem_statement}\n")
    lines.append(f"**Underlying need:** {opportunity.underlying_need}\n")
    segments = ", ".join(opportunity.affected_segments) if opportunity.affected_segments else "(none)"
    lines.append(f"**Affected segments:** {segments}\n")

    lines.append("## Evidence\n")
    lines.append(f"Evidence count (deterministically computed): {validated.evidence_count}\n")
    for quote in opportunity.representative_evidence:
        lines.append(f'- "{quote.excerpt}" (feedback_id: `{quote.feedback_id}`)')

    lines.append("\n## Deterministic Score\n")
    score = validated.score
    lines.append(
        f"- evidence_strength: {score.evidence_strength}/5\n"
        f"- severity: {score.severity}/5\n"
        f"- strategic_alignment: {score.strategic_alignment}/5\n"
        f"- segment_breadth: {score.segment_breadth}/5\n"
        f"- confidence: {score.confidence}/5\n"
        f"- **total: {score.total:.2f}**\n"
    )

    lines.append(f"\n## Roadmap / Strategy Context\n\n{opportunity.roadmap_status}\n")

    if opportunity.contradicting_evidence:
        lines.append("\n## Contradicting Evidence\n")
        lines.extend(f"- {item}" for item in opportunity.contradicting_evidence)

    if opportunity.evidence_gaps:
        lines.append("\n## Evidence Gaps\n")
        lines.extend(f"- {item}" for item in opportunity.evidence_gaps)

    if validated.issues:
        lines.append("\n## Unresolved Deterministic Validation Notes\n")
        lines.extend(f"- {issue.issue_type.value}: {issue.message}" for issue in validated.issues)

    lines.append(f"\n## Recommended Next Step\n\n{opportunity.recommended_next_step}\n")
    lines.append(f"\n---\nRun/cache reference: `{result.cache_key}`\n")
    lines.append(f"\n{marker}\n")

    return IssuePayload(
        title=f"[Opportunity] {opportunity.title}",
        body="\n".join(lines),
        labels=list(_ISSUE_LABELS),
        idempotency_marker=marker,
    )


# ---------------------------------------------------------------------------
# GitHub client boundary
# ---------------------------------------------------------------------------


class GitHubIssueClient(Protocol):
    """A client capable of finding-by-marker and creating exactly one issue.

    Tests inject a fake implementation; production uses GitHubRestIssueClient.
    """

    def find_issue_by_marker(self, *, repo: str, marker: str) -> str | None:
        """Return the existing issue's html_url if `marker` is already present, else None."""
        ...

    def create_issue(self, *, repo: str, title: str, body: str, labels: list[str]) -> str:
        """Create the issue and return its html_url."""
        ...


def _default_allowed_repos() -> frozenset[str]:
    """Read the allow-list from GITHUB_DEMO_REPO (comma-separated), lazily.

    Never called at import time. Returns an empty set (nothing allowed) if
    unset, so writes are refused by default rather than silently permitted.
    """
    raw = os.environ.get(GITHUB_DEMO_REPO_ENV, "")
    return frozenset(item.strip() for item in raw.split(",") if item.strip())


# ---------------------------------------------------------------------------
# Bounded, human-approved action
# ---------------------------------------------------------------------------


def create_opportunity_issue(
    result: OrchestrationResult,
    opportunity_id: str,
    repo: str,
    *,
    approved: bool = False,
    dry_run: bool = True,
    allowed_repos: frozenset[str] | set[str] | None = None,
    client: GitHubIssueClient | None = None,
) -> IssueActionResult:
    """Render, and only if approved, create one opportunity's GitHub issue.

    Refuses (returns a structured result, never raises for these cases) when:
    - `repo` is not in the allow-listed set of demo repositories.
    - `approved` is not True.
    - `dry_run` is True (payload is still rendered and returned).
    - an issue with the same deterministic idempotency marker already
      exists in `repo` (checked only once dry_run is False and approved).

    Makes at most one "find" and one "create" network request, both to
    `client` (or a lazily-constructed `GitHubRestIssueClient` if none is
    injected), and only when every guard above has passed.
    """
    payload = render_issue_payload(result, opportunity_id)
    allow_list = frozenset(allowed_repos) if allowed_repos is not None else _default_allowed_repos()

    if repo not in allow_list:
        return IssueActionResult(
            status=IssueCreationStatus.REFUSED_UNSUPPORTED_REPO,
            repo=repo,
            opportunity_id=opportunity_id,
            payload=payload,
            approved=approved,
            dry_run=dry_run,
            message=f"Repo '{repo}' is not in the allow-listed set of demo repositories.",
        )

    if not approved:
        return IssueActionResult(
            status=IssueCreationStatus.REFUSED_NOT_APPROVED,
            repo=repo,
            opportunity_id=opportunity_id,
            payload=payload,
            approved=False,
            dry_run=dry_run,
            message="approved=True is required to create a real GitHub issue. No mutation was made.",
        )

    if dry_run:
        return IssueActionResult(
            status=IssueCreationStatus.DRY_RUN,
            repo=repo,
            opportunity_id=opportunity_id,
            payload=payload,
            approved=True,
            dry_run=True,
            message="Dry run: rendered payload only. No GitHub request was made.",
        )

    if client is None:
        client = GitHubRestIssueClient()

    existing_url = client.find_issue_by_marker(repo=repo, marker=payload.idempotency_marker)
    if existing_url is not None:
        return IssueActionResult(
            status=IssueCreationStatus.REFUSED_DUPLICATE,
            repo=repo,
            opportunity_id=opportunity_id,
            payload=payload,
            approved=True,
            dry_run=False,
            issue_url=existing_url,
            message="An issue with this opportunity's idempotency marker already exists.",
        )

    issue_url = client.create_issue(
        repo=repo, title=payload.title, body=payload.body, labels=payload.labels
    )
    return IssueActionResult(
        status=IssueCreationStatus.CREATED,
        repo=repo,
        opportunity_id=opportunity_id,
        payload=payload,
        approved=True,
        dry_run=False,
        issue_url=issue_url,
        message="Issue created.",
    )


# ---------------------------------------------------------------------------
# Real GitHub REST adapter (stdlib urllib only -- no new dependency)
# ---------------------------------------------------------------------------


class GitHubRestIssueClient:
    """Real GitHubIssueClient backed by the GitHub REST API via urllib.

    Every attribute that could touch the network or secrets is resolved
    lazily inside _headers/find_issue_by_marker/create_issue, never in
    __init__ or at import time.
    """

    def __init__(self, log_path: str | Path | None = None) -> None:
        self._log_path = Path(log_path) if log_path is not None else _default_log_path()

    def _headers(self) -> dict[str, str]:
        token = os.environ.get(GITHUB_TOKEN_ENV)
        if not token:
            raise GitHubActionError(
                f"{GITHUB_TOKEN_ENV} is not set; cannot call the real GitHub API."
            )
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "product-feedback-agent",
        }

    def find_issue_by_marker(self, *, repo: str, marker: str) -> str | None:
        start = time.monotonic()
        try:
            headers = self._headers()
        except GitHubActionError:
            self._trace(action="find", repo=repo, success=False, duration=time.monotonic() - start,
                        error_type="MissingTokenError")
            raise

        query = urllib.parse.quote(f'repo:{repo} "{marker}" in:body')
        url = f"{GITHUB_API_BASE}/search/issues?q={query}"
        request = urllib.request.Request(url, headers=headers)

        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                data = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            # Never include str(exc): it can echo request details (headers,
            # URL query) and we must not risk leaking the token into logs.
            self._trace(action="find", repo=repo, success=False, duration=time.monotonic() - start,
                        error_type=type(exc).__name__)
            raise GitHubActionError(f"GitHub search request failed: {type(exc).__name__}") from exc

        items = data.get("items", [])
        found_url = items[0].get("html_url") if items else None
        self._trace(action="find", repo=repo, success=True, duration=time.monotonic() - start,
                    found=found_url is not None)
        return found_url

    def create_issue(self, *, repo: str, title: str, body: str, labels: list[str]) -> str:
        start = time.monotonic()
        try:
            headers = self._headers()
        except GitHubActionError:
            self._trace(action="create", repo=repo, success=False, duration=time.monotonic() - start,
                        error_type="MissingTokenError")
            raise

        headers = {**headers, "Content-Type": "application/json"}
        url = f"{GITHUB_API_BASE}/repos/{repo}/issues"
        body_bytes = json.dumps({"title": title, "body": body, "labels": labels}).encode("utf-8")
        request = urllib.request.Request(url, data=body_bytes, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                data = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            self._trace(action="create", repo=repo, success=False, duration=time.monotonic() - start,
                        error_type=type(exc).__name__)
            raise GitHubActionError(f"GitHub create-issue request failed: {type(exc).__name__}") from exc

        issue_url = data.get("html_url")
        self._trace(action="create", repo=repo, success=True, duration=time.monotonic() - start)
        return issue_url

    def _trace(
        self,
        *,
        action: str,
        repo: str,
        success: bool,
        duration: float,
        error_type: str | None = None,
        found: bool | None = None,
    ) -> None:
        record: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "stage": "github_action",
            "action": action,
            "repo": repo,
            "success": success,
            "duration_seconds": round(duration, 3),
        }
        if error_type is not None:
            record["error_type"] = error_type
        if found is not None:
            record["found"] = found

        _append_trace(self._log_path, record)


def _default_log_path() -> Path:
    project_root = Path(__file__).resolve().parents[2]
    return project_root / DEFAULT_ACTION_LOG_PATH


def _append_trace(log_path: Path, record: dict[str, Any]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


# ---------------------------------------------------------------------------
# CLI entry point (the "approved action" step in the n8n workflow)
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="product-feedback-agent-github-action",
        description=(
            "Render, and only if explicitly approved, create one opportunity's "
            "GitHub issue from a Stage 13 OrchestrationResult JSON file. "
            "approved defaults to False and dry-run defaults to True: pass "
            "--approved AND --no-dry-run together to actually mutate GitHub. "
            "This is the human-approved action step -- it takes no analysis "
            "decision of its own."
        ),
    )
    parser.add_argument(
        "--result-file",
        required=True,
        help="Path to an OrchestrationResult JSON file (e.g. orchestrator's CLI stdout).",
    )
    parser.add_argument("--opportunity-id", required=True)
    parser.add_argument("--repo", required=True, help="'owner/name' of the target repository.")
    parser.add_argument(
        "--approved",
        action="store_true",
        default=False,
        help="Must be passed explicitly to allow a real mutation. Defaults to False.",
    )
    parser.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        default=True,
        help="Disable dry-run mode. Without this flag, the payload is only rendered.",
    )
    parser.add_argument(
        "--allowed-repo",
        dest="allowed_repos",
        action="append",
        default=None,
        help="An allow-listed 'owner/name' repo. May be repeated. Defaults to GITHUB_DEMO_REPO.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point: render/create one opportunity's issue, print the JSON result.

    Writes the structured `IssueActionResult` JSON to stdout only, so it can
    be consumed by another stage (an n8n "Execute Command" node, a script,
    or a human). Never raises for the ordinary refusal cases (not approved,
    unsupported repo, dry run, duplicate) -- those are reported in the
    printed result's `status` field. Exits non-zero only for a genuine
    failure (bad input file, unknown opportunity_id, or a real GitHub
    request failure).
    """
    args = _build_arg_parser().parse_args(argv)

    try:
        raw = json.loads(Path(args.result_file).read_text(encoding="utf-8"))
        result = OrchestrationResult.model_validate(raw)
    except Exception as exc:
        print(f"Could not load OrchestrationResult from {args.result_file}: {exc}", file=sys.stderr)
        return 1

    allowed_repos = frozenset(args.allowed_repos) if args.allowed_repos else None

    try:
        action_result = create_opportunity_issue(
            result,
            args.opportunity_id,
            args.repo,
            approved=args.approved,
            dry_run=args.dry_run,
            allowed_repos=allowed_repos,
        )
    except GitHubActionError as exc:
        print(f"GitHub action could not be completed: {exc}", file=sys.stderr)
        return 1

    print(action_result.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
