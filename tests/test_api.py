"""Offline tests for the local HTTP boundary (n8n Docker -> Windows host).

No real network, Gemini, or GitHub call happens anywhere in this file:
`/analyze` tests monkeypatch `product_feedback_agent.api.run_analysis`
(mirroring the existing `test_orchestrator.py` CLI-test pattern), and
`/github/issues` tests either monkeypatch
`product_feedback_agent.api.create_opportunity_issue` directly or exercise
the real function with `dry_run=True` / a monkeypatched
`GitHubRestIssueClient` (mirroring `test_github_action.py`), so no HTTP
request ever leaves the process. Requests are made in-process via
Starlette's `TestClient` -- no socket is opened, no live n8n/API server is
started.
"""

from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

from product_feedback_agent.critic import CriticOutput, CriticStatus
from product_feedback_agent.orchestrator import HumanReviewStatus, OrchestrationResult
from product_feedback_agent.validation import (
    ScoreBreakdown,
    ValidatedOpportunity,
    ValidationResult,
)
from product_feedback_agent.analyst import EvidenceQuote, OpportunityDraft, ScoreComponentProposal

import product_feedback_agent.api as api_module

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
            EvidenceQuote(feedback_id="fb_billing", excerpt="The invoice payment process is confusing.")
        ],
        related_context=[],
        evidence_strength=_score(),
        severity=_score(),
        segment_breadth=_score(2),
        strategic_alignment=_score(3),
        confidence=_score(4),
        roadmap_status="Not explicitly addressed in supplied pricing strategy section.",
        contradicting_evidence=[],
        evidence_gaps=[],
        discovery_questions=["What specifically confuses users?"],
        recommended_next_step="Run 3-5 discovery interviews with enterprise billing contacts.",
    )


def _validated(opportunity_id: str) -> ValidatedOpportunity:
    return ValidatedOpportunity(
        opportunity_id=opportunity_id,
        evidence_count=1,
        score=ScoreBreakdown(
            evidence_strength=4, severity=4, strategic_alignment=3, segment_breadth=2, confidence=4, total=67.0
        ),
        issues=[],
    )


def _result(
    cache_key: str = "ck_abc123",
    opportunity_ids: tuple[str, str, str] = ("opp_1", "opp_2", "opp_3"),
) -> OrchestrationResult:
    validated = [_validated(oid) for oid in opportunity_ids]
    return OrchestrationResult(
        cache_key=cache_key,
        opportunities=[_draft(oid) for oid in opportunity_ids],
        validation_result=ValidationResult(opportunities=validated, issues=[]),
        critic_output=CriticOutput(status=CriticStatus.PASS),
        revised=False,
        model_request_count=2,
        unresolved_issue_count=0,
        human_review_status=HumanReviewStatus.PENDING_REVIEW,
    )


@pytest.fixture
def client() -> TestClient:
    return TestClient(api_module.create_app())


# ---------------------------------------------------------------------------
# 1. GET /health
# ---------------------------------------------------------------------------


def test_health_responds_ok(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# 2 & 3. POST /analyze delegates exactly once, returns exactly 3 opportunities
# ---------------------------------------------------------------------------


def test_analyze_delegates_exactly_once_to_run_analysis(client: TestClient, monkeypatch):
    calls: list[dict] = []
    expected = _result()

    def fake_run_analysis(**kwargs):
        calls.append(kwargs)
        return expected

    monkeypatch.setattr(api_module, "run_analysis", fake_run_analysis)

    response = client.post(
        "/analyze",
        json={
            "feedback_file": "data/samples/feedback_sample.csv",
            "context_files": ["data/samples/roadmap.md"],
            "analysis_query": "search filtering",
        },
    )

    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0]["analysis_query"] == "search filtering"


def test_analyze_returns_exactly_three_opportunities(client: TestClient, monkeypatch):
    monkeypatch.setattr(api_module, "run_analysis", lambda **kwargs: _result())

    response = client.post(
        "/analyze",
        json={"feedback_file": "feedback.csv", "analysis_query": "billing friction"},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["opportunities"]) == 3
    assert body["human_review_status"] == "pending_review"


def test_analyze_rejects_missing_required_fields(client: TestClient):
    response = client.post("/analyze", json={"feedback_file": "feedback.csv"})
    assert response.status_code == 422


def test_analyze_rejects_unknown_fields(client: TestClient, monkeypatch):
    monkeypatch.setattr(api_module, "run_analysis", lambda **kwargs: _result())

    response = client.post(
        "/analyze",
        json={"feedback_file": "feedback.csv", "analysis_query": "q", "approved": True},
    )
    assert response.status_code == 422


def test_analyze_surfaces_orchestration_error_as_400(client: TestClient, monkeypatch):
    from product_feedback_agent.orchestrator import OrchestrationError

    def _raise(**kwargs):
        raise OrchestrationError("zero valid feedback records")

    monkeypatch.setattr(api_module, "run_analysis", _raise)

    response = client.post("/analyze", json={"feedback_file": "empty.csv", "analysis_query": "q"})

    assert response.status_code == 400
    assert "zero valid feedback records" in response.json()["detail"]


# ---------------------------------------------------------------------------
# 4. /analyze never invokes the GitHub action
# ---------------------------------------------------------------------------


def test_analyze_does_not_invoke_github_action(client: TestClient, monkeypatch):
    monkeypatch.setattr(api_module, "run_analysis", lambda **kwargs: _result())

    github_calls: list[dict] = []

    def fake_create_opportunity_issue(*args, **kwargs):
        github_calls.append({"args": args, "kwargs": kwargs})
        raise AssertionError("create_opportunity_issue must never be called from /analyze")

    monkeypatch.setattr(api_module, "create_opportunity_issue", fake_create_opportunity_issue)

    response = client.post("/analyze", json={"feedback_file": "feedback.csv", "analysis_query": "q"})

    assert response.status_code == 200
    assert github_calls == []


# ---------------------------------------------------------------------------
# 5. POST /github/issues delegates to create_opportunity_issue
# ---------------------------------------------------------------------------


def test_github_issues_delegates_to_create_opportunity_issue(client: TestClient, monkeypatch):
    calls: list[dict] = []

    def fake_create_opportunity_issue(result, opportunity_id, repo, *, approved, dry_run, allowed_repos):
        calls.append(
            {
                "opportunity_id": opportunity_id,
                "repo": repo,
                "approved": approved,
                "dry_run": dry_run,
                "allowed_repos": allowed_repos,
            }
        )
        from product_feedback_agent.github_action import IssueActionResult, IssueCreationStatus, render_issue_payload

        return IssueActionResult(
            status=IssueCreationStatus.DRY_RUN,
            repo=repo,
            opportunity_id=opportunity_id,
            payload=render_issue_payload(result, opportunity_id),
            approved=approved,
            dry_run=dry_run,
            message="Dry run: rendered payload only. No GitHub request was made.",
        )

    monkeypatch.setattr(api_module, "create_opportunity_issue", fake_create_opportunity_issue)

    response = client.post(
        "/github/issues",
        json={
            "result": json.loads(_result().model_dump_json()),
            "opportunity_id": "opp_1",
            "repo": "org/repo",
            "approved": True,
            "dry_run": True,
        },
    )

    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0] == {
        "opportunity_id": "opp_1",
        "repo": "org/repo",
        "approved": True,
        "dry_run": True,
        "allowed_repos": None,
    }
    assert response.json()["status"] == "dry_run"


# ---------------------------------------------------------------------------
# 6. Unapproved GitHub request is refused
# ---------------------------------------------------------------------------


def test_github_issues_unapproved_request_is_refused(client: TestClient):
    response = client.post(
        "/github/issues",
        json={
            "result": json.loads(_result().model_dump_json()),
            "opportunity_id": "opp_1",
            "repo": "org/repo",
            "dry_run": False,
            "allowed_repos": ["org/repo"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "refused_not_approved"
    assert body["issue_url"] is None


def test_github_issues_approved_defaults_to_false(client: TestClient):
    response = client.post(
        "/github/issues",
        json={
            "result": json.loads(_result().model_dump_json()),
            "opportunity_id": "opp_1",
            "repo": "org/repo",
            "allowed_repos": ["org/repo"],
        },
    )

    body = response.json()
    assert body["approved"] is False
    assert body["status"] in {"refused_not_approved", "dry_run"}


# ---------------------------------------------------------------------------
# 7. Dry-run remains safe (default, and explicit)
# ---------------------------------------------------------------------------


def test_github_issues_dry_run_is_the_default_and_makes_no_mutation(client: TestClient, monkeypatch):
    # If this endpoint ever attempted a real network call, GITHUB_TOKEN being
    # unset would raise -- proving no such call happens for a dry run.
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    response = client.post(
        "/github/issues",
        json={
            "result": json.loads(_result().model_dump_json()),
            "opportunity_id": "opp_1",
            "repo": "org/repo",
            "approved": True,
            "allowed_repos": ["org/repo"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "dry_run"
    assert body["issue_url"] is None
    assert body["payload"]["title"]  # payload still rendered


# ---------------------------------------------------------------------------
# 8. The API layer does not weaken allow-list / idempotency behavior
# ---------------------------------------------------------------------------


def test_github_issues_default_allow_list_is_empty_when_env_unset(client: TestClient, monkeypatch):
    monkeypatch.delenv("GITHUB_DEMO_REPO", raising=False)

    response = client.post(
        "/github/issues",
        json={
            "result": json.loads(_result().model_dump_json()),
            "opportunity_id": "opp_1",
            "repo": "org/repo",
            "approved": True,
            "dry_run": False,
            # No allowed_repos supplied -- must not silently allow this repo.
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "refused_unsupported_repo"


def test_github_issues_duplicate_marker_is_still_refused_through_the_api(client: TestClient, monkeypatch):
    from product_feedback_agent.github_action import build_idempotency_marker

    marker = build_idempotency_marker("ck_abc123", "opp_1")

    class FakeGitHubClient:
        def find_issue_by_marker(self, *, repo: str, marker: str) -> str | None:
            return "https://github.com/org/repo/issues/99"

        def create_issue(self, *, repo: str, title: str, body: str, labels: list[str]) -> str:
            raise AssertionError("create_issue must not be called for a duplicate marker")

    monkeypatch.setattr(
        "product_feedback_agent.github_action.GitHubRestIssueClient", lambda: FakeGitHubClient()
    )

    response = client.post(
        "/github/issues",
        json={
            "result": json.loads(_result(cache_key="ck_abc123").model_dump_json()),
            "opportunity_id": "opp_1",
            "repo": "org/repo",
            "approved": True,
            "dry_run": False,
            "allowed_repos": ["org/repo"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "refused_duplicate"
    assert marker in body["payload"]["idempotency_marker"]


def test_github_issues_rejects_unknown_fields(client: TestClient):
    response = client.post(
        "/github/issues",
        json={
            "result": json.loads(_result().model_dump_json()),
            "opportunity_id": "opp_1",
            "repo": "org/repo",
            "extra_field": "should not be accepted",
        },
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# 16. Group 1 request budget remains unchanged through the HTTP boundary
# ---------------------------------------------------------------------------


def test_analyze_full_stack_respects_group1_request_budget(client: TestClient, monkeypatch, tmp_path):
    """Exercises the real run_analysis (not monkeypatched) with fake
    analyst/critic/revision clients, proving the HTTP layer adds no
    additional model requests on top of the existing 2/3-request budget."""
    from product_feedback_agent.context import load_context_file
    from product_feedback_agent.ingestion import load_feedback_file
    from product_feedback_agent.retrieval import ContextSearchResult, FeedbackSearchResult

    feedback_path = tmp_path / "feedback.csv"
    feedback_path.write_text(
        "text,source,segment,product_area\n"
        '"Search results are hard to filter.",survey,enterprise,search\n'
        '"Invoices are hard to download from the billing portal.",survey,enterprise,billing\n'
        '"The mobile app crashes when uploading a photo.",app_review,smb,mobile\n',
        encoding="utf-8",
    )
    roadmap_path = tmp_path / "roadmap.md"
    roadmap_path.write_text("# Roadmap\n\nShip search improvements next quarter.\n", encoding="utf-8")

    records = load_feedback_file(feedback_path).valid_records
    doc = load_context_file(roadmap_path)
    section = doc.sections[0]

    class FakeRetrieval:
        def index_feedback(self, records) -> None:
            pass

        def index_context(self, documents) -> None:
            pass

        def search_feedback(self, query: str, top_k: int) -> list[FeedbackSearchResult]:
            return [
                FeedbackSearchResult(
                    feedback_id=r.feedback_id,
                    text=r.text,
                    metadata={"source": r.source, "segment": r.segment, "product_area": r.product_area},
                    distance=0.0,
                )
                for r in records[:top_k]
            ]

        def search_context(self, query: str, top_k: int) -> list[ContextSearchResult]:
            return [
                ContextSearchResult(
                    section_id=section.section_id, document_id=doc.document_id, text=section.text,
                    metadata={}, distance=0.0,
                )
            ][:top_k]

    class FakeModelClient:
        def __init__(self, payload: dict) -> None:
            self.payload = payload
            self.calls: list[dict] = []

        def generate_structured(self, *, prompt: str, response_schema: dict, cache_key: str) -> str:
            self.calls.append({"cache_key": cache_key})
            return json.dumps(self.payload)

    def _opportunity(opportunity_id: str, record) -> dict:
        return {
            "opportunity_id": opportunity_id,
            "title": "Improve search filtering",
            "problem_statement": "Users find it hard to narrow down search results.",
            "underlying_need": "Users need a fast, precise way to filter results.",
            "affected_segments": [record.segment],
            "feedback_ids": [record.feedback_id],
            "representative_evidence": [{"feedback_id": record.feedback_id, "excerpt": record.text}],
            "related_context": [{"document_id": doc.document_id, "section_id": section.section_id}],
            "evidence_strength": {"score": 4, "rationale": "Supported."},
            "severity": {"score": 4, "rationale": "Supported."},
            "segment_breadth": {"score": 2, "rationale": "Supported."},
            "strategic_alignment": {"score": 3, "rationale": "Supported."},
            "confidence": {"score": 4, "rationale": "Supported."},
            "roadmap_status": "Referenced in the supplied roadmap section.",
            "contradicting_evidence": [],
            "evidence_gaps": [],
            "discovery_questions": ["What makes filtering difficult?"],
            "recommended_next_step": "Run discovery interviews.",
        }

    opportunities = {"opportunities": [_opportunity(f"opp_{i+1}", records[i]) for i in range(3)]}
    analyst_client = FakeModelClient(opportunities)
    critic_client = FakeModelClient(
        {"status": "pass", "issues": [], "unsupported_claims": [], "citation_errors": [],
         "duplicate_opportunities": [], "required_revisions": []}
    )
    revision_client = FakeModelClient(opportunities)

    from product_feedback_agent.orchestrator import run_analysis as real_run_analysis

    call_count = {"n": 0}

    def wrapped_run_analysis(**kwargs):
        call_count["n"] += 1
        return real_run_analysis(
            **kwargs,
            cache_dir=tmp_path / "cache",
            retrieval=FakeRetrieval(),
            analyst_client=analyst_client,
            critic_client=critic_client,
            revision_client=revision_client,
        )

    monkeypatch.setattr(api_module, "run_analysis", wrapped_run_analysis)

    response = client.post(
        "/analyze",
        json={"feedback_file": str(feedback_path), "context_files": [str(roadmap_path)], "analysis_query": "search filtering"},
    )

    assert response.status_code == 200
    assert call_count["n"] == 1
    total_requests = len(analyst_client.calls) + len(critic_client.calls) + len(revision_client.calls)
    assert total_requests == 2
    assert total_requests <= 3
    assert response.json()["model_request_count"] == 2
