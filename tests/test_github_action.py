import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

from product_feedback_agent.analyst import EvidenceQuote, OpportunityDraft, ScoreComponentProposal
from product_feedback_agent.critic import CriticOutput, CriticStatus
from product_feedback_agent.orchestrator import HumanReviewStatus, OrchestrationResult
from product_feedback_agent.validation import (
    ScoreBreakdown,
    ValidatedOpportunity,
    ValidationIssue,
    ValidationIssueType,
    ValidationResult,
)

# ---------------------------------------------------------------------------
# Fixtures: a hand-built OrchestrationResult, no orchestrator/pipeline call.
# ---------------------------------------------------------------------------


def _score(score: int = 4) -> ScoreComponentProposal:
    return ScoreComponentProposal(score=score, rationale="Supported by cited feedback.")


def _draft(opportunity_id: str) -> OpportunityDraft:
    return OpportunityDraft(
        opportunity_id=opportunity_id,
        title="Simplify invoice payment",
        problem_statement="Enterprise users find invoice payment confusing.",
        underlying_need="Users need a clear, fast way to understand and pay invoices.",
        affected_segments=["enterprise"],
        feedback_ids=["fb_billing"],
        representative_evidence=[
            EvidenceQuote(
                feedback_id="fb_billing",
                excerpt="The invoice payment process is confusing.",
            )
        ],
        related_context=[],
        evidence_strength=_score(),
        severity=_score(),
        segment_breadth=_score(2),
        strategic_alignment=_score(3),
        confidence=_score(4),
        roadmap_status="Not explicitly addressed in supplied pricing strategy section.",
        contradicting_evidence=["One user said billing was fine."],
        evidence_gaps=["No feedback from SMB segment on this theme."],
        discovery_questions=["What specifically confuses users?"],
        recommended_next_step="Run 3-5 discovery interviews with enterprise billing contacts.",
    )


def _validated(opportunity_id: str, *, issues: list[ValidationIssue] | None = None) -> ValidatedOpportunity:
    return ValidatedOpportunity(
        opportunity_id=opportunity_id,
        evidence_count=1,
        score=ScoreBreakdown(
            evidence_strength=4, severity=4, strategic_alignment=3, segment_breadth=2, confidence=4, total=67.0
        ),
        issues=issues or [],
    )


def _result(
    cache_key: str = "ck_abc123",
    *,
    opportunity_ids: tuple[str, str, str] = ("opp_1", "opp_2", "opp_3"),
    extra_issues: list[ValidationIssue] | None = None,
) -> OrchestrationResult:
    validated = [
        _validated(opportunity_ids[0], issues=extra_issues or []),
        _validated(opportunity_ids[1]),
        _validated(opportunity_ids[2]),
    ]
    return OrchestrationResult(
        cache_key=cache_key,
        opportunities=[_draft(oid) for oid in opportunity_ids],
        validation_result=ValidationResult(
            opportunities=validated,
            issues=[issue for v in validated for issue in v.issues],
        ),
        critic_output=CriticOutput(status=CriticStatus.PASS),
        revised=False,
        model_request_count=2,
        unresolved_issue_count=sum(len(v.issues) for v in validated),
        human_review_status=HumanReviewStatus.PENDING_REVIEW,
    )


class FakeGitHubClient:
    def __init__(self, *, existing_marker: str | None = None, issue_url: str = "https://github.com/org/repo/issues/1"):
        self.existing_marker = existing_marker
        self.issue_url = issue_url
        self.find_calls: list[dict] = []
        self.create_calls: list[dict] = []

    def find_issue_by_marker(self, *, repo: str, marker: str) -> str | None:
        self.find_calls.append({"repo": repo, "marker": marker})
        if self.existing_marker is not None and marker == self.existing_marker:
            return "https://github.com/org/repo/issues/99"
        return None

    def create_issue(self, *, repo: str, title: str, body: str, labels: list[str]) -> str:
        self.create_calls.append({"repo": repo, "title": title, "body": body, "labels": labels})
        return self.issue_url


# ---------------------------------------------------------------------------
# Payload rendering: evidence-backed, no fabrication
# ---------------------------------------------------------------------------


def test_render_issue_payload_includes_required_fields():
    from product_feedback_agent.github_action import render_issue_payload

    result = _result()
    payload = render_issue_payload(result, "opp_1")

    assert "Simplify invoice payment" in payload.title
    assert "Enterprise users find invoice payment confusing." in payload.body
    assert "enterprise" in payload.body
    assert "The invoice payment process is confusing." in payload.body
    assert "fb_billing" in payload.body
    assert "Evidence count (deterministically computed): 1" in payload.body
    assert "evidence_strength: 4/5" in payload.body
    assert "total: 67.00" in payload.body
    assert "Not explicitly addressed in supplied pricing strategy section." in payload.body
    assert "One user said billing was fine." in payload.body
    assert "No feedback from SMB segment on this theme." in payload.body
    assert "Run 3-5 discovery interviews with enterprise billing contacts." in payload.body
    assert "ck_abc123" in payload.body
    assert payload.idempotency_marker in payload.body


def test_render_issue_payload_surfaces_unresolved_validation_issues():
    from product_feedback_agent.github_action import render_issue_payload

    issue = ValidationIssue(
        opportunity_id="opp_1",
        issue_type=ValidationIssueType.UNSUPPORTED_SEGMENT,
        message="'mobile' is a product_area, not a customer segment.",
        segment="mobile",
    )
    result = _result(extra_issues=[issue])
    payload = render_issue_payload(result, "opp_1")

    assert "unsupported_segment" in payload.body
    assert "'mobile' is a product_area, not a customer segment." in payload.body


def test_idempotency_marker_is_deterministic_and_unique_per_opportunity():
    from product_feedback_agent.github_action import build_idempotency_marker

    marker_a1 = build_idempotency_marker("ck1", "opp_1")
    marker_a1_again = build_idempotency_marker("ck1", "opp_1")
    marker_a2 = build_idempotency_marker("ck1", "opp_2")
    marker_b1 = build_idempotency_marker("ck2", "opp_1")

    assert marker_a1 == marker_a1_again
    assert marker_a1 != marker_a2
    assert marker_a1 != marker_b1


def test_render_issue_payload_raises_on_unknown_opportunity_id():
    from product_feedback_agent.github_action import GitHubActionError, render_issue_payload

    with pytest.raises(GitHubActionError):
        render_issue_payload(_result(), "opp_does_not_exist")


# ---------------------------------------------------------------------------
# Approval boundary
# ---------------------------------------------------------------------------


def test_approved_false_blocks_write():
    from product_feedback_agent.github_action import IssueCreationStatus, create_opportunity_issue

    client = FakeGitHubClient()
    result = create_opportunity_issue(
        _result(), "opp_1", "org/repo",
        approved=False, dry_run=False,
        allowed_repos={"org/repo"}, client=client,
    )

    assert result.status == IssueCreationStatus.REFUSED_NOT_APPROVED
    assert result.issue_url is None
    assert client.find_calls == []
    assert client.create_calls == []


def test_missing_approval_defaults_to_false_and_blocks_write():
    from product_feedback_agent.github_action import IssueCreationStatus, create_opportunity_issue

    client = FakeGitHubClient()
    result = create_opportunity_issue(
        _result(), "opp_1", "org/repo",
        dry_run=False, allowed_repos={"org/repo"}, client=client,
    )

    assert result.status == IssueCreationStatus.REFUSED_NOT_APPROVED
    assert client.create_calls == []


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------


def test_dry_run_makes_no_client_call_even_when_approved():
    from product_feedback_agent.github_action import IssueCreationStatus, create_opportunity_issue

    client = FakeGitHubClient()
    result = create_opportunity_issue(
        _result(), "opp_1", "org/repo",
        approved=True, dry_run=True,
        allowed_repos={"org/repo"}, client=client,
    )

    assert result.status == IssueCreationStatus.DRY_RUN
    assert result.issue_url is None
    assert client.find_calls == []
    assert client.create_calls == []
    assert result.payload.title  # payload is still rendered


def test_dry_run_is_the_default():
    import inspect

    from product_feedback_agent.github_action import create_opportunity_issue

    sig = inspect.signature(create_opportunity_issue)
    assert sig.parameters["dry_run"].default is True
    assert sig.parameters["approved"].default is False


# ---------------------------------------------------------------------------
# Repo allow-list
# ---------------------------------------------------------------------------


def test_unsupported_repo_is_blocked():
    from product_feedback_agent.github_action import IssueCreationStatus, create_opportunity_issue

    client = FakeGitHubClient()
    result = create_opportunity_issue(
        _result(), "opp_1", "someone-else/unrelated-repo",
        approved=True, dry_run=False,
        allowed_repos={"org/repo"}, client=client,
    )

    assert result.status == IssueCreationStatus.REFUSED_UNSUPPORTED_REPO
    assert client.find_calls == []
    assert client.create_calls == []


def test_allowed_repo_is_accepted_and_creates_issue():
    from product_feedback_agent.github_action import IssueCreationStatus, create_opportunity_issue

    client = FakeGitHubClient(issue_url="https://github.com/org/repo/issues/42")
    result = create_opportunity_issue(
        _result(), "opp_1", "org/repo",
        approved=True, dry_run=False,
        allowed_repos={"org/repo"}, client=client,
    )

    assert result.status == IssueCreationStatus.CREATED
    assert result.issue_url == "https://github.com/org/repo/issues/42"
    assert len(client.create_calls) == 1
    assert client.create_calls[0]["repo"] == "org/repo"


def test_default_allow_list_reads_github_demo_repo_env(monkeypatch):
    from product_feedback_agent.github_action import IssueCreationStatus, create_opportunity_issue

    monkeypatch.setenv("GITHUB_DEMO_REPO", "org/repo, org/other-repo")
    client = FakeGitHubClient()

    blocked = create_opportunity_issue(_result(), "opp_1", "org/not-listed", approved=True, dry_run=False, client=client)
    allowed = create_opportunity_issue(_result(), "opp_1", "org/other-repo", approved=True, dry_run=False, client=client)

    assert blocked.status == IssueCreationStatus.REFUSED_UNSUPPORTED_REPO
    assert allowed.status == IssueCreationStatus.CREATED


def test_default_allow_list_is_empty_when_env_unset(monkeypatch):
    from product_feedback_agent.github_action import IssueCreationStatus, create_opportunity_issue

    monkeypatch.delenv("GITHUB_DEMO_REPO", raising=False)
    result = create_opportunity_issue(
        _result(), "opp_1", "org/repo", approved=True, dry_run=False, client=FakeGitHubClient()
    )

    assert result.status == IssueCreationStatus.REFUSED_UNSUPPORTED_REPO


# ---------------------------------------------------------------------------
# Duplicate prevention
# ---------------------------------------------------------------------------


def test_duplicate_marker_prevents_duplicate_creation():
    from product_feedback_agent.github_action import (
        IssueCreationStatus,
        build_idempotency_marker,
        create_opportunity_issue,
    )

    marker = build_idempotency_marker("ck_abc123", "opp_1")
    client = FakeGitHubClient(existing_marker=marker)

    result = create_opportunity_issue(
        _result(), "opp_1", "org/repo",
        approved=True, dry_run=False,
        allowed_repos={"org/repo"}, client=client,
    )

    assert result.status == IssueCreationStatus.REFUSED_DUPLICATE
    assert result.issue_url == "https://github.com/org/repo/issues/99"
    assert client.create_calls == []
    assert len(client.find_calls) == 1


# ---------------------------------------------------------------------------
# Result structure locking
# ---------------------------------------------------------------------------


def test_issue_action_result_field_set_is_locked():
    from product_feedback_agent.github_action import IssueActionResult

    assert set(IssueActionResult.model_fields.keys()) == {
        "status",
        "repo",
        "opportunity_id",
        "payload",
        "approved",
        "dry_run",
        "issue_url",
        "message",
    }


# ---------------------------------------------------------------------------
# Real client: lazy credentials, no network in tests, token never logged
# ---------------------------------------------------------------------------


def test_importing_github_action_module_does_not_require_a_token():
    repo_root = Path(__file__).resolve().parents[1]
    env = {key: value for key, value in __import__("os").environ.items() if key != "GITHUB_TOKEN"}
    env["PYTHONPATH"] = str(repo_root / "src")
    for var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        env[var] = "1"

    result = subprocess.run(
        [sys.executable, "-c", "import product_feedback_agent.github_action"],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_default_client_construction_does_not_touch_env_or_network(monkeypatch):
    from product_feedback_agent.github_action import GitHubRestIssueClient

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    client = GitHubRestIssueClient()
    assert client is not None


def test_missing_token_raises_clear_error(monkeypatch, tmp_path):
    from product_feedback_agent.github_action import GitHubActionError, GitHubRestIssueClient

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    client = GitHubRestIssueClient(log_path=tmp_path / "actions.jsonl")

    with pytest.raises(GitHubActionError, match="GITHUB_TOKEN"):
        client.create_issue(repo="org/repo", title="t", body="b", labels=[])


def test_missing_token_trace_does_not_reference_a_token_value(monkeypatch, tmp_path):
    from product_feedback_agent.github_action import GitHubActionError, GitHubRestIssueClient

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    log_path = tmp_path / "actions.jsonl"
    client = GitHubRestIssueClient(log_path=log_path)

    with pytest.raises(GitHubActionError):
        client.create_issue(repo="org/repo", title="t", body="b", labels=[])

    record = json.loads(log_path.read_text(encoding="utf-8").strip().splitlines()[0])
    assert record["success"] is False
    assert record["stage"] == "github_action"


class _FakeHTTPResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def test_mocked_success_path_creates_issue_and_never_logs_token(monkeypatch, tmp_path):
    from product_feedback_agent.github_action import GitHubRestIssueClient

    secret_token = "ghp_sekrit_do_not_leak"
    monkeypatch.setenv("GITHUB_TOKEN", secret_token)

    captured_requests: list = []

    def fake_urlopen(request, timeout=None):
        captured_requests.append(request)
        return _FakeHTTPResponse({"html_url": "https://github.com/org/repo/issues/7"})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    log_path = tmp_path / "actions.jsonl"
    client = GitHubRestIssueClient(log_path=log_path)
    issue_url = client.create_issue(repo="org/repo", title="Title", body="Body", labels=["x"])

    assert issue_url == "https://github.com/org/repo/issues/7"
    assert len(captured_requests) == 1
    assert captured_requests[0].get_header("Authorization") == f"Bearer {secret_token}"

    log_contents = log_path.read_text(encoding="utf-8")
    assert secret_token not in log_contents
    record = json.loads(log_contents.strip().splitlines()[0])
    assert record["success"] is True
    assert record["stage"] == "github_action"
    assert record["action"] == "create"
    assert record["repo"] == "org/repo"


def test_mocked_find_issue_by_marker_no_match(monkeypatch, tmp_path):
    from product_feedback_agent.github_action import GitHubRestIssueClient

    monkeypatch.setenv("GITHUB_TOKEN", "any-token")

    def fake_urlopen(request, timeout=None):
        return _FakeHTTPResponse({"items": []})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    client = GitHubRestIssueClient(log_path=tmp_path / "actions.jsonl")
    found = client.find_issue_by_marker(repo="org/repo", marker="<!-- marker -->")
    assert found is None


def test_mocked_network_failure_does_not_leak_token(monkeypatch, tmp_path):
    from product_feedback_agent.github_action import GitHubActionError, GitHubRestIssueClient

    secret_token = "ghp_another_secret"
    monkeypatch.setenv("GITHUB_TOKEN", secret_token)

    def fake_urlopen(request, timeout=None):
        raise ConnectionError("boom")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    log_path = tmp_path / "actions.jsonl"
    client = GitHubRestIssueClient(log_path=log_path)

    with pytest.raises(GitHubActionError) as excinfo:
        client.create_issue(repo="org/repo", title="t", body="b", labels=[])

    assert secret_token not in str(excinfo.value)
    log_contents = log_path.read_text(encoding="utf-8")
    assert secret_token not in log_contents


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _write_result_file(tmp_path: Path) -> Path:
    result_path = tmp_path / "result.json"
    result_path.write_text(_result().model_dump_json(indent=2), encoding="utf-8")
    return result_path


def test_main_dry_run_prints_json_result_and_returns_zero(tmp_path, capsys):
    import product_feedback_agent.github_action as github_action_module

    result_path = _write_result_file(tmp_path)

    exit_code = github_action_module.main(
        [
            "--result-file",
            str(result_path),
            "--opportunity-id",
            "opp_1",
            "--repo",
            "org/repo",
            "--allowed-repo",
            "org/repo",
            "--approved",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["status"] == "dry_run"
    assert payload["approved"] is True
    assert payload["dry_run"] is True


def test_main_approved_and_no_dry_run_without_allow_list_is_refused(tmp_path, capsys):
    import product_feedback_agent.github_action as github_action_module

    result_path = _write_result_file(tmp_path)

    exit_code = github_action_module.main(
        [
            "--result-file",
            str(result_path),
            "--opportunity-id",
            "opp_1",
            "--repo",
            "org/repo",
            "--approved",
            "--no-dry-run",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["status"] == "refused_unsupported_repo"


def test_main_approved_and_no_dry_run_with_allow_list_and_mocked_client_creates_issue(
    tmp_path, capsys, monkeypatch
):
    import product_feedback_agent.github_action as github_action_module

    monkeypatch.setattr(
        github_action_module,
        "GitHubRestIssueClient",
        lambda: FakeGitHubClient(issue_url="https://github.com/org/repo/issues/5"),
    )

    result_path = _write_result_file(tmp_path)

    exit_code = github_action_module.main(
        [
            "--result-file",
            str(result_path),
            "--opportunity-id",
            "opp_1",
            "--repo",
            "org/repo",
            "--approved",
            "--no-dry-run",
            "--allowed-repo",
            "org/repo",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["status"] == "created"
    assert payload["issue_url"] == "https://github.com/org/repo/issues/5"


def test_main_unknown_opportunity_id_returns_nonzero(tmp_path, capsys):
    import product_feedback_agent.github_action as github_action_module

    result_path = _write_result_file(tmp_path)

    exit_code = github_action_module.main(
        [
            "--result-file",
            str(result_path),
            "--opportunity-id",
            "does_not_exist",
            "--repo",
            "org/repo",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "could not be completed" in captured.err
    assert captured.out == ""
