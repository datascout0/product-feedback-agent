"""Stage 17: secondary-domain reuse proof.

Uses a small synthetic customer onboarding/support/workflow-friction dataset
(`data/samples/secondary_domain/`) -- a different domain from the primary
search/reporting/mobile feedback used everywhere else in this project -- to
prove, with real (not domain-specific) production code, that:

- the same ingestion schema/ingestion function reads this dataset
- the same SemanticRetrieval/Chroma retrieval path indexes and returns it
- citations and exact quotes resolve against it exactly as before
- the same AnalystOutput/OrchestrationResult schemas hold exactly 3 opportunities
- the same deterministic scoring formula (compute_score) is used, unchanged
- the same critic wire contract (required_revisions-derived status) is used
- the same bounded 2/3-request budget applies
- no core module contains a domain-specific branch/conditional for this data

Only the analyst/critic model responses are faked (as everywhere else in
this offline suite) -- no live Gemini call, no live GitHub mutation, no
network dependency.

What changed to reuse this architecture for a new domain: DATA + CONTEXT
(the CSV + Markdown files under data/samples/secondary_domain/) only.
What did not change: the ingestion schema, retrieval, analyst contract,
deterministic validator, scoring formula, critic contract, and the human
approval model -- see docs/agent-progress.md Stage 17 for the full writeup.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

from product_feedback_agent.context import load_context_file
from product_feedback_agent.critic import CriticStatus
from product_feedback_agent.ingestion import load_feedback_file
from product_feedback_agent.orchestrator import HumanReviewStatus, run_analysis
from product_feedback_agent.pipeline import MAX_PASSING_RUN_REQUESTS
from product_feedback_agent.retrieval import SemanticRetrieval
from product_feedback_agent.validation import ValidationIssueType, compute_score

SECONDARY_DOMAIN_DIR = (
    Path(__file__).resolve().parents[1] / "data" / "samples" / "secondary_domain"
)
FEEDBACK_PATH = SECONDARY_DOMAIN_DIR / "onboarding_feedback.csv"
STRATEGY_PATH = SECONDARY_DOMAIN_DIR / "onboarding_strategy.md"

_CORE_MODULES = [
    "analyst.py",
    "critic.py",
    "pipeline.py",
    "validation.py",
    "orchestrator.py",
    "analysis_context.py",
    "retrieval.py",
    "github_action.py",
    "ingestion.py",
    "context.py",
]
_SECONDARY_DOMAIN_TERMS = ["onboarding", "sso", "startup"]


# ---------------------------------------------------------------------------
# Ingestion / retrieval building blocks (same pattern as test_retrieval.py)
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"[a-z0-9]+")


class FakeEmbedder:
    """Deterministic bag-of-words embedder, offline and reused unmodified
    from the pattern in tests/test_retrieval.py. This is the only
    domain-agnostic stand-in in this file: the actual SemanticRetrieval/
    Chroma indexing and query code is real and unmodified."""

    def __init__(self, dim: int = 32) -> None:
        self.dim = dim

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        for word in _WORD_RE.findall(text.lower()):
            index = int(hashlib.sha256(word.encode("utf-8")).hexdigest(), 16) % self.dim
            vector[index] += 1.0
        norm = math.sqrt(sum(component * component for component in vector))
        if norm == 0:
            return vector
        return [component / norm for component in vector]


class FakeModelClient:
    """Generic structured-JSON fake client (same shape used throughout the
    offline suite): records calls, returns a preset payload."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    def generate_structured(self, *, prompt: str, response_schema: dict, cache_key: str) -> str:
        self.calls.append({"prompt": prompt, "response_schema": response_schema, "cache_key": cache_key})
        return json.dumps(self.payload)


def _score(score: int) -> dict:
    return {"score": score, "rationale": "Grounded in the cited onboarding/support feedback."}


def _section_by_heading(doc, heading: str):
    return next(section for section in doc.sections if section.heading == heading)


def _pass_critic_payload() -> dict:
    return {
        "issues": [],
        "unsupported_claims": [],
        "citation_errors": [],
        "duplicate_opportunities": [],
        "required_revisions": [],
    }


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------


def test_secondary_domain_feedback_ingests_with_stable_ids():
    result = load_feedback_file(FEEDBACK_PATH)
    assert result.total_input_count == 8
    assert len(result.valid_records) == 8
    assert not result.invalid_records
    assert result.duplicates_removed == 0

    ids = {record.feedback_id for record in result.valid_records}
    assert ids == {f"ob_{i:03d}" for i in range(1, 9)}

    product_areas = {record.product_area for record in result.valid_records}
    assert product_areas == {"sso", "onboarding", "workflow", "notifications"}
    segments = {record.segment for record in result.valid_records}
    assert segments == {"enterprise", "smb", "startup"}


def test_secondary_domain_context_ingests_with_sections():
    document = load_context_file(STRATEGY_PATH)
    assert document.filename == "onboarding_strategy.md"
    headings = [section.heading for section in document.sections if section.heading]
    assert headings == [
        "Onboarding Strategy",
        "SSO Rollout Priorities",
        "Onboarding Checklist Redesign",
        "Support Ticket Quality",
    ]


# ---------------------------------------------------------------------------
# Retrieval (real SemanticRetrieval + Chroma, deterministic fake embedder)
# ---------------------------------------------------------------------------


def test_secondary_domain_retrieval_indexes_and_returns_real_records(tmp_path):
    ingestion_result = load_feedback_file(FEEDBACK_PATH)
    document = load_context_file(STRATEGY_PATH)

    retrieval = SemanticRetrieval(persist_directory=tmp_path / "chroma", embedder=FakeEmbedder())
    retrieval.index_feedback(ingestion_result.valid_records)
    retrieval.index_context(document)

    feedback_results = retrieval.search_feedback("SSO configuration friction", top_k=8)
    assert len(feedback_results) == 8
    assert {r.feedback_id for r in feedback_results} == {f"ob_{i:03d}" for i in range(1, 9)}

    context_results = retrieval.search_context("SSO rollout", top_k=5)
    assert len(context_results) == 5
    assert {r.document_id for r in context_results} == {document.document_id}


# ---------------------------------------------------------------------------
# Full pipeline through the unmodified Stage 13 orchestrator
# ---------------------------------------------------------------------------


def _build_analyst_payload(document) -> dict:
    sso_section = _section_by_heading(document, "SSO Rollout Priorities")
    checklist_section = _section_by_heading(document, "Onboarding Checklist Redesign")
    support_section = _section_by_heading(document, "Support Ticket Quality")

    return {
        "opportunities": [
            {
                "opportunity_id": "opp_sso_setup",
                "title": "Reduce manual SSO setup friction for enterprise accounts",
                "problem_statement": (
                    "Enterprise customers cannot self-serve SSO configuration and depend on "
                    "slow, repeated back-and-forth with support."
                ),
                "underlying_need": "Enterprise admins need to configure SSO themselves without a support loop.",
                "affected_segments": ["enterprise"],
                "feedback_ids": ["ob_001", "ob_002", "ob_007"],
                "representative_evidence": [
                    {
                        "feedback_id": "ob_001",
                        "excerpt": (
                            "New workspace setup requires our IT team to manually configure SSO "
                            "before any teammate can log in."
                        ),
                    },
                    {
                        "feedback_id": "ob_002",
                        "excerpt": "Configuring SSO for our new account took two weeks of back-and-forth with support.",
                    },
                ],
                "related_context": [
                    {"document_id": document.document_id, "section_id": sso_section.section_id}
                ],
                "evidence_strength": _score(4),
                "severity": _score(4),
                "segment_breadth": _score(2),
                "strategic_alignment": _score(5),
                "confidence": _score(4),
                "roadmap_status": "Self-serve SSO is called out as a Q3 priority in the supplied strategy section.",
                "contradicting_evidence": [],
                "evidence_gaps": ["No feedback yet from mid-market accounts on SSO setup."],
                "discovery_questions": ["Which SSO providers do affected accounts actually use?"],
                "recommended_next_step": "Run 3-5 discovery interviews with enterprise IT admins on SSO setup.",
            },
            {
                "opportunity_id": "opp_checklist_clarity",
                "title": "Make onboarding checklist completion state explicit",
                "problem_statement": (
                    "SMB accounts cannot tell when an onboarding checklist step is actually "
                    "complete, so they redo work they already finished."
                ),
                "underlying_need": "Users need clear, visible confirmation that a setup step succeeded.",
                "affected_segments": ["smb"],
                "feedback_ids": ["ob_003", "ob_004"],
                "representative_evidence": [
                    {
                        "feedback_id": "ob_003",
                        "excerpt": (
                            "The onboarding checklist never tells us when a step is actually "
                            "complete so we redo steps we already finished."
                        ),
                    }
                ],
                "related_context": [
                    {"document_id": document.document_id, "section_id": checklist_section.section_id}
                ],
                "evidence_strength": _score(3),
                "severity": _score(3),
                "segment_breadth": _score(2),
                "strategic_alignment": _score(4),
                "confidence": _score(3),
                "roadmap_status": "Checklist redesign is already called out in the supplied strategy section.",
                "contradicting_evidence": [],
                "evidence_gaps": ["No feedback yet from enterprise accounts on checklist clarity."],
                "discovery_questions": ["Which specific steps are most often redone unnecessarily?"],
                "recommended_next_step": "Run 3-5 discovery interviews with SMB accounts mid-onboarding.",
            },
            {
                "opportunity_id": "opp_workflow_visibility",
                "title": "Surface which onboarding step is blocking the rest of setup",
                "problem_statement": (
                    "Startup accounts cannot tell which onboarding step is blocking progress, and "
                    "resort to repeated support contact to get unblocked."
                ),
                "underlying_need": "Users need visibility into what is blocking their onboarding workflow.",
                "affected_segments": ["startup"],
                "feedback_ids": ["ob_005", "ob_006"],
                "representative_evidence": [
                    {
                        "feedback_id": "ob_006",
                        "excerpt": "Nobody on our team knew which onboarding step was blocking the rest of the workflow.",
                    }
                ],
                "related_context": [
                    {"document_id": document.document_id, "section_id": support_section.section_id}
                ],
                "evidence_strength": _score(3),
                "severity": _score(3),
                "segment_breadth": _score(1),
                "strategic_alignment": _score(3),
                "confidence": _score(3),
                "roadmap_status": "Not explicitly addressed in the supplied strategy section beyond ticket quality.",
                "contradicting_evidence": [],
                "evidence_gaps": ["No feedback yet from enterprise or SMB accounts on workflow blockers."],
                "discovery_questions": ["What information would let a team self-diagnose a blocked step?"],
                "recommended_next_step": "Run 3-5 discovery interviews with startup accounts mid-onboarding.",
            },
        ]
    }


def test_secondary_domain_produces_three_grounded_opportunities_on_the_shared_pipeline(tmp_path):
    document = load_context_file(STRATEGY_PATH)

    retrieval = SemanticRetrieval(persist_directory=tmp_path / "chroma", embedder=FakeEmbedder())
    analyst_client = FakeModelClient(_build_analyst_payload(document))
    critic_client = FakeModelClient(_pass_critic_payload())

    result = run_analysis(
        feedback_file=FEEDBACK_PATH,
        context_files=[STRATEGY_PATH],
        analysis_query="onboarding setup and support friction",
        feedback_top_k=8,
        context_top_k=5,
        diverse_sample_size=3,
        cache_dir=tmp_path / "cache",
        retrieval=retrieval,
        analyst_client=analyst_client,
        critic_client=critic_client,
    )

    # Requirement: exactly 3 opportunities, same OrchestrationResult/AnalystOutput schema.
    assert len(result.opportunities) == 3
    assert {o.opportunity_id for o in result.opportunities} == {
        "opp_sso_setup",
        "opp_checklist_clarity",
        "opp_workflow_visibility",
    }

    # Requirement: citations and exact quotes resolve; no fabricated claims.
    assert result.validation_result.is_valid, result.validation_result.issues
    assert result.unresolved_issue_count == 0
    for issue_type in ValidationIssueType:
        assert not any(issue.issue_type == issue_type for issue in result.validation_result.issues)

    # Requirement: same bounded request budget (analyst + critic, no revision).
    assert len(analyst_client.calls) == 1
    assert len(critic_client.calls) == 1
    assert result.model_request_count == MAX_PASSING_RUN_REQUESTS
    assert result.revised is False
    assert result.critic_output.status == CriticStatus.PASS

    # Requirement: same human-approval boundary -- nothing here is auto-approved.
    assert result.human_review_status == HumanReviewStatus.PENDING_REVIEW

    # Requirement: scoring is unchanged -- computed by the same shared
    # compute_score formula, not a domain-specific reimplementation.
    for opportunity, validated in zip(result.opportunities, result.validation_result.opportunities):
        expected_total = compute_score(
            evidence_strength=opportunity.evidence_strength.score,
            severity=opportunity.severity.score,
            strategic_alignment=opportunity.strategic_alignment.score,
            segment_breadth=opportunity.segment_breadth.score,
            confidence=opportunity.confidence.score,
        )
        assert validated.score.total == expected_total


def test_secondary_domain_evidence_count_reflects_real_citations_only(tmp_path):
    document = load_context_file(STRATEGY_PATH)
    retrieval = SemanticRetrieval(persist_directory=tmp_path / "chroma", embedder=FakeEmbedder())

    result = run_analysis(
        feedback_file=FEEDBACK_PATH,
        context_files=[STRATEGY_PATH],
        analysis_query="onboarding setup and support friction",
        feedback_top_k=8,
        context_top_k=5,
        diverse_sample_size=3,
        cache_dir=tmp_path / "cache",
        retrieval=retrieval,
        analyst_client=FakeModelClient(_build_analyst_payload(document)),
        critic_client=FakeModelClient(_pass_critic_payload()),
    )

    validated_by_id = {v.opportunity_id: v for v in result.validation_result.opportunities}
    assert validated_by_id["opp_sso_setup"].evidence_count == 3
    assert validated_by_id["opp_checklist_clarity"].evidence_count == 2
    assert validated_by_id["opp_workflow_visibility"].evidence_count == 2


# ---------------------------------------------------------------------------
# No domain-specific branch anywhere in the core pipeline
# ---------------------------------------------------------------------------


def test_core_pipeline_modules_contain_no_secondary_domain_specific_branch():
    src_dir = Path(__file__).resolve().parents[1] / "src" / "product_feedback_agent"
    for module_name in _CORE_MODULES:
        source = (src_dir / module_name).read_text(encoding="utf-8").lower()
        for term in _SECONDARY_DOMAIN_TERMS:
            assert term not in source, f"{module_name} contains domain-specific term {term!r}"
