import json
import sys
from pathlib import Path

import pytest

from product_feedback_agent.context import load_context_file
from product_feedback_agent.ingestion import load_feedback_file
from product_feedback_agent.retrieval import ContextSearchResult, FeedbackSearchResult

# ---------------------------------------------------------------------------
# Fixtures: a minimal on-disk project, and an in-memory fake retrieval that
# preserves real feedback IDs/metadata so analyst payloads below can cite
# them exactly (no chromadb, no embedding model, fully offline).
# ---------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "feedback.csv").write_text(
        "text,source,segment,product_area\n"
        '"Search results are hard to filter.",survey,enterprise,search\n'
        '"Invoices are hard to download from the billing portal.",survey,enterprise,billing\n'
        '"The mobile app crashes when uploading a photo.",app_review,smb,mobile\n',
        encoding="utf-8",
    )
    (tmp_path / "roadmap.md").write_text(
        "# Roadmap\n\nShip search improvements next quarter.\n", encoding="utf-8"
    )
    return tmp_path


class FakeRetrieval:
    """In-memory stand-in for SemanticRetrieval: no chromadb, no embedder."""

    def __init__(self) -> None:
        self._feedback: dict[str, object] = {}
        self._context_sections: list = []
        self.index_feedback_calls = 0
        self.index_context_calls = 0
        self.search_feedback_calls = 0
        self.search_context_calls = 0

    def index_feedback(self, records) -> None:
        self.index_feedback_calls += 1
        for record in records:
            self._feedback[record.feedback_id] = record

    def index_context(self, documents) -> None:
        self.index_context_calls += 1
        for document in documents:
            self._context_sections.extend(document.sections)

    def search_feedback(self, query: str, top_k: int) -> list[FeedbackSearchResult]:
        self.search_feedback_calls += 1
        records = list(self._feedback.values())[:top_k]
        return [
            FeedbackSearchResult(
                feedback_id=record.feedback_id,
                text=record.text,
                metadata={
                    "source": record.source,
                    "segment": record.segment,
                    "product_area": record.product_area,
                },
                distance=0.0,
            )
            for record in records
        ]

    def search_context(self, query: str, top_k: int) -> list[ContextSearchResult]:
        self.search_context_calls += 1
        sections = self._context_sections[:top_k]
        return [
            ContextSearchResult(
                section_id=section.section_id,
                document_id=section.document_id,
                text=section.text,
                metadata={},
                distance=0.0,
            )
            for section in sections
        ]


@pytest.fixture
def fake_retrieval() -> FakeRetrieval:
    return FakeRetrieval()


class FakeClient:
    """Generic structured-JSON fake client that records calls and returns a preset payload."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    def generate_structured(self, *, prompt: str, response_schema: dict, cache_key: str) -> str:
        self.calls.append(
            {"prompt": prompt, "response_schema": response_schema, "cache_key": cache_key}
        )
        return json.dumps(self.payload)


def _score(score: int = 4) -> dict:
    return {"score": score, "rationale": "Supported by the cited feedback."}


def _opportunity(opportunity_id: str, record, doc, *, affected_segments: list[str] | None = None) -> dict:
    section = doc.sections[0]
    return {
        "opportunity_id": opportunity_id,
        "title": "Improve search filtering",
        "problem_statement": "Users find it hard to narrow down search results.",
        "underlying_need": "Users need a fast, precise way to filter results.",
        "affected_segments": affected_segments if affected_segments is not None else [record.segment],
        "feedback_ids": [record.feedback_id],
        "representative_evidence": [
            {"feedback_id": record.feedback_id, "excerpt": record.text}
        ],
        "related_context": [{"document_id": doc.document_id, "section_id": section.section_id}],
        "evidence_strength": _score(),
        "severity": _score(),
        "segment_breadth": _score(2),
        "strategic_alignment": _score(3),
        "confidence": _score(4),
        "roadmap_status": "Referenced in the supplied roadmap section.",
        "contradicting_evidence": [],
        "evidence_gaps": ["No feedback from additional segments on this theme."],
        "discovery_questions": ["What specifically makes filtering difficult?"],
        "recommended_next_step": "Run discovery interviews with affected users.",
    }


def _three_opportunities(records, doc, **kwargs) -> list[dict]:
    return [
        _opportunity("opp_1", records[0], doc, **kwargs),
        _opportunity("opp_2", records[1], doc, **kwargs),
        _opportunity("opp_3", records[2], doc, **kwargs),
    ]


def _pass_critic_payload() -> dict:
    return {
        "status": "pass",
        "issues": [],
        "unsupported_claims": [],
        "citation_errors": [],
        "duplicate_opportunities": [],
        "required_revisions": [],
    }


def _revise_critic_payload() -> dict:
    return {
        "status": "revise",
        "issues": [
            {
                "opportunity_id": "opp_1",
                "issue_type": "unsupported_segment",
                "message": "'mobile' is a product_area, not a customer segment.",
            }
        ],
        "unsupported_claims": [],
        "citation_errors": [],
        "duplicate_opportunities": [],
        "required_revisions": ["Remove 'mobile' from affected_segments for opp_1."],
    }


def _real_records(project: Path):
    return load_feedback_file(project / "feedback.csv").valid_records


def _real_doc(project: Path):
    return load_context_file(project / "roadmap.md")


# ---------------------------------------------------------------------------
# Import safety
# ---------------------------------------------------------------------------


def test_importing_orchestrator_does_not_load_sentence_transformer():
    assert "sentence_transformers" not in sys.modules

    import product_feedback_agent.orchestrator  # noqa: F401

    assert "sentence_transformers" not in sys.modules


# ---------------------------------------------------------------------------
# run_analysis: prepare_analysis_context called exactly once
# ---------------------------------------------------------------------------


def test_run_analysis_prepares_context_exactly_once(project, fake_retrieval, tmp_path):
    from product_feedback_agent.orchestrator import run_analysis

    records = _real_records(project)
    doc = _real_doc(project)

    run_analysis(
        feedback_file=project / "feedback.csv",
        context_files=[project / "roadmap.md"],
        analysis_query="search filtering",
        cache_dir=tmp_path / "cache",
        retrieval=fake_retrieval,
        analyst_client=FakeClient({"opportunities": _three_opportunities(records, doc)}),
        critic_client=FakeClient(_pass_critic_payload()),
        revision_client=FakeClient({"opportunities": _three_opportunities(records, doc)}),
    )

    assert fake_retrieval.index_feedback_calls == 1
    assert fake_retrieval.index_context_calls == 1
    assert fake_retrieval.search_feedback_calls == 1
    assert fake_retrieval.search_context_calls == 1


# ---------------------------------------------------------------------------
# Structural guarantees
# ---------------------------------------------------------------------------


def test_run_analysis_returns_exactly_three_opportunities(project, fake_retrieval, tmp_path):
    from product_feedback_agent.orchestrator import run_analysis

    records = _real_records(project)
    doc = _real_doc(project)

    result = run_analysis(
        feedback_file=project / "feedback.csv",
        context_files=[project / "roadmap.md"],
        analysis_query="search filtering",
        cache_dir=tmp_path / "cache",
        retrieval=fake_retrieval,
        analyst_client=FakeClient({"opportunities": _three_opportunities(records, doc)}),
        critic_client=FakeClient(_pass_critic_payload()),
        revision_client=FakeClient({"opportunities": _three_opportunities(records, doc)}),
    )

    assert len(result.opportunities) == 3


def test_orchestration_result_locks_expected_field_set():
    from product_feedback_agent.orchestrator import OrchestrationResult

    assert set(OrchestrationResult.model_fields.keys()) == {
        "cache_key",
        "opportunities",
        "validation_result",
        "critic_output",
        "revised",
        "model_request_count",
        "unresolved_issue_count",
        "human_review_status",
    }


def test_orchestration_result_rejects_unknown_fields(project, fake_retrieval, tmp_path):
    from product_feedback_agent.orchestrator import OrchestrationResult, run_analysis

    records = _real_records(project)
    doc = _real_doc(project)

    result = run_analysis(
        feedback_file=project / "feedback.csv",
        context_files=[project / "roadmap.md"],
        analysis_query="search filtering",
        cache_dir=tmp_path / "cache",
        retrieval=fake_retrieval,
        analyst_client=FakeClient({"opportunities": _three_opportunities(records, doc)}),
        critic_client=FakeClient(_pass_critic_payload()),
        revision_client=FakeClient({"opportunities": _three_opportunities(records, doc)}),
    )

    payload = result.model_dump(mode="json")
    payload["github_issue_url"] = "https://github.com/example/repo/issues/1"
    payload["approved"] = True
    with pytest.raises(Exception):
        OrchestrationResult.model_validate(payload)


# ---------------------------------------------------------------------------
# Pass/revision metadata propagation
# ---------------------------------------------------------------------------


def test_pass_run_metadata_propagates(project, fake_retrieval, tmp_path):
    from product_feedback_agent.critic import CriticStatus
    from product_feedback_agent.orchestrator import HumanReviewStatus, run_analysis

    records = _real_records(project)
    doc = _real_doc(project)
    analyst_client = FakeClient({"opportunities": _three_opportunities(records, doc)})
    critic_client = FakeClient(_pass_critic_payload())
    revision_client = FakeClient({"opportunities": _three_opportunities(records, doc)})

    result = run_analysis(
        feedback_file=project / "feedback.csv",
        context_files=[project / "roadmap.md"],
        analysis_query="search filtering",
        cache_dir=tmp_path / "cache",
        retrieval=fake_retrieval,
        analyst_client=analyst_client,
        critic_client=critic_client,
        revision_client=revision_client,
    )

    assert result.revised is False
    assert result.model_request_count == 2
    assert result.critic_output.status == CriticStatus.PASS
    assert result.human_review_status == HumanReviewStatus.PENDING_REVIEW
    assert len(analyst_client.calls) == 1
    assert len(critic_client.calls) == 1
    assert len(revision_client.calls) == 0


def test_revision_run_metadata_propagates(project, fake_retrieval, tmp_path):
    from product_feedback_agent.critic import CriticStatus
    from product_feedback_agent.orchestrator import HumanReviewStatus, run_analysis

    records = _real_records(project)
    doc = _real_doc(project)
    analyst_client = FakeClient(
        {"opportunities": _three_opportunities(records, doc, affected_segments=["mobile"])}
    )
    critic_client = FakeClient(_revise_critic_payload())
    revision_client = FakeClient(
        {"opportunities": _three_opportunities(records, doc, affected_segments=["enterprise"])}
    )

    result = run_analysis(
        feedback_file=project / "feedback.csv",
        context_files=[project / "roadmap.md"],
        analysis_query="search filtering",
        cache_dir=tmp_path / "cache",
        retrieval=fake_retrieval,
        analyst_client=analyst_client,
        critic_client=critic_client,
        revision_client=revision_client,
    )

    assert result.revised is True
    assert result.model_request_count == 3
    assert result.critic_output.status == CriticStatus.REVISE
    assert result.human_review_status == HumanReviewStatus.PENDING_REVIEW
    assert len(analyst_client.calls) == 1
    assert len(critic_client.calls) == 1
    assert len(revision_client.calls) == 1


def test_no_execution_path_exceeds_three_model_requests(project, fake_retrieval, tmp_path):
    from product_feedback_agent.orchestrator import run_analysis

    records = _real_records(project)
    doc = _real_doc(project)

    for critic_payload in (_pass_critic_payload(), _revise_critic_payload()):
        analyst_client = FakeClient(
            {"opportunities": _three_opportunities(records, doc, affected_segments=["mobile"])}
        )
        critic_client = FakeClient(critic_payload)
        revision_client = FakeClient(
            {"opportunities": _three_opportunities(records, doc, affected_segments=["enterprise"])}
        )

        run_analysis(
            feedback_file=project / "feedback.csv",
            context_files=[project / "roadmap.md"],
            analysis_query="search filtering",
            cache_dir=tmp_path / f"cache-{critic_payload['status']}",
            retrieval=FakeRetrieval(),
            analyst_client=analyst_client,
            critic_client=critic_client,
            revision_client=revision_client,
        )

        total_requests = len(analyst_client.calls) + len(critic_client.calls) + len(revision_client.calls)
        assert total_requests <= 3


# ---------------------------------------------------------------------------
# Unresolved issues propagate
# ---------------------------------------------------------------------------


def test_unresolved_issues_after_revision_propagate(project, fake_retrieval, tmp_path):
    from product_feedback_agent.orchestrator import run_analysis

    records = _real_records(project)
    doc = _real_doc(project)
    still_broken = {
        "opportunities": _three_opportunities(records, doc, affected_segments=["mobile"])
    }

    result = run_analysis(
        feedback_file=project / "feedback.csv",
        context_files=[project / "roadmap.md"],
        analysis_query="search filtering",
        cache_dir=tmp_path / "cache",
        retrieval=fake_retrieval,
        analyst_client=FakeClient(
            {"opportunities": _three_opportunities(records, doc, affected_segments=["mobile"])}
        ),
        critic_client=FakeClient(_revise_critic_payload()),
        revision_client=FakeClient(still_broken),
    )

    assert result.unresolved_issue_count == len(result.validation_result.issues)
    assert result.unresolved_issue_count >= 1


# ---------------------------------------------------------------------------
# Human/AI boundary: never bypasses approval, never touches GitHub
# ---------------------------------------------------------------------------


def test_orchestration_never_marks_a_result_approved(project, fake_retrieval, tmp_path):
    from product_feedback_agent.orchestrator import HumanReviewStatus, run_analysis

    records = _real_records(project)
    doc = _real_doc(project)

    result = run_analysis(
        feedback_file=project / "feedback.csv",
        context_files=[project / "roadmap.md"],
        analysis_query="search filtering",
        cache_dir=tmp_path / "cache",
        retrieval=fake_retrieval,
        analyst_client=FakeClient({"opportunities": _three_opportunities(records, doc)}),
        critic_client=FakeClient(_pass_critic_payload()),
        revision_client=FakeClient({"opportunities": _three_opportunities(records, doc)}),
    )

    assert result.human_review_status is HumanReviewStatus.PENDING_REVIEW
    only_status = {status.value for status in HumanReviewStatus}
    assert only_status == {"pending_review"}


def test_orchestrator_module_imports_no_github_client():
    import product_feedback_agent.orchestrator as orchestrator_module

    assert "github" not in sys.modules
    for name in vars(orchestrator_module):
        assert "github" not in name.lower()


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_run_analysis_raises_on_zero_valid_feedback(project, fake_retrieval, tmp_path):
    from product_feedback_agent.orchestrator import OrchestrationError, run_analysis

    (project / "empty.csv").write_text("text,source\n", encoding="utf-8")

    with pytest.raises(OrchestrationError, match="zero valid feedback records"):
        run_analysis(
            feedback_file=project / "empty.csv",
            context_files=[],
            analysis_query="anything",
            cache_dir=tmp_path / "cache",
            retrieval=fake_retrieval,
        )


def test_run_analysis_raises_on_unsupported_context_extension(project, fake_retrieval, tmp_path):
    from product_feedback_agent.orchestrator import OrchestrationError, run_analysis

    (project / "notes.pdf").write_text("not really a pdf", encoding="utf-8")

    with pytest.raises(OrchestrationError):
        run_analysis(
            feedback_file=project / "feedback.csv",
            context_files=[project / "notes.pdf"],
            analysis_query="anything",
            cache_dir=tmp_path / "cache",
            retrieval=fake_retrieval,
        )


def test_run_analysis_raises_on_blank_query(project, fake_retrieval, tmp_path):
    from product_feedback_agent.orchestrator import OrchestrationError, run_analysis

    with pytest.raises(OrchestrationError):
        run_analysis(
            feedback_file=project / "feedback.csv",
            context_files=[project / "roadmap.md"],
            analysis_query="   ",
            cache_dir=tmp_path / "cache",
            retrieval=fake_retrieval,
        )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def test_main_prints_result_and_human_review_notice_on_success(project, fake_retrieval, tmp_path, monkeypatch, capsys):
    import product_feedback_agent.orchestrator as orchestrator_module

    records = _real_records(project)
    doc = _real_doc(project)
    expected = orchestrator_module.run_analysis(
        feedback_file=project / "feedback.csv",
        context_files=[project / "roadmap.md"],
        analysis_query="search filtering",
        cache_dir=tmp_path / "cache",
        retrieval=fake_retrieval,
        analyst_client=FakeClient({"opportunities": _three_opportunities(records, doc)}),
        critic_client=FakeClient(_pass_critic_payload()),
        revision_client=FakeClient({"opportunities": _three_opportunities(records, doc)}),
    )

    monkeypatch.setattr(orchestrator_module, "run_analysis", lambda **kwargs: expected)

    exit_code = orchestrator_module.main(
        [
            "--feedback-file",
            str(project / "feedback.csv"),
            "--context-file",
            str(project / "roadmap.md"),
            "--query",
            "search filtering",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "HUMAN REVIEW REQUIRED" in captured.err
    assert '"human_review_status": "pending_review"' in captured.out
    assert "HUMAN REVIEW REQUIRED" not in captured.out
    json.loads(captured.out)  # stdout must be pure, parseable JSON


def test_main_returns_nonzero_on_orchestration_error(monkeypatch, capsys):
    import product_feedback_agent.orchestrator as orchestrator_module

    def _raise(**kwargs):
        raise orchestrator_module.OrchestrationError("no valid feedback")

    monkeypatch.setattr(orchestrator_module, "run_analysis", _raise)

    exit_code = orchestrator_module.main(
        ["--feedback-file", "missing.csv", "--query", "anything"]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "could not be completed" in captured.err
