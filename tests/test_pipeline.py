import json

import pytest

from product_feedback_agent.analysis_context import (
    AnalysisContextPacket,
    ContextEvidence,
    DatasetStatistics,
    FeedbackEvidence,
    RetrievalParameters,
)
from product_feedback_agent.critic import CriticStatus
from product_feedback_agent.pipeline import (
    MAX_PASSING_RUN_REQUESTS,
    MAX_REVISION_RUN_REQUESTS,
    RevisionError,
    build_revision_prompt,
    run_pipeline,
    run_revision,
)
from product_feedback_agent.validation import ValidationIssueType, validate_analyst_output

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _packet() -> AnalysisContextPacket:
    return AnalysisContextPacket(
        analysis_query="billing invoice payment",
        dataset_statistics=DatasetStatistics(
            total_feedback_count=3,
            counts_by_source={"survey": 2, "app_store_review": 1},
            counts_by_product_area={"billing": 2, "mobile": 1},
            counts_by_segment={"enterprise": 2},
            count_with_rating=0,
            average_rating=None,
        ),
        retrieved_feedback=[
            FeedbackEvidence(
                feedback_id="fb_billing",
                text="The invoice payment process is confusing and billing support is slow.",
                metadata={"source": "survey", "segment": "enterprise", "product_area": "billing"},
                distance=0.1,
            ),
            FeedbackEvidence(
                feedback_id="fb_invoice2",
                text="Invoices are hard to download from the billing portal.",
                metadata={"source": "survey", "segment": "enterprise", "product_area": "billing"},
                distance=0.2,
            ),
            FeedbackEvidence(
                feedback_id="fb_mobile",
                text="The mobile app crashes when I upload a photo.",
                metadata={"source": "app_store_review", "product_area": "mobile"},
                distance=0.3,
            ),
        ],
        retrieved_context=[
            ContextEvidence(
                section_id="doc_pricing_sec000",
                document_id="doc_pricing",
                text="Enterprise pricing strategy focuses on annual contracts and volume discounts.",
                metadata={"heading": "Enterprise Pricing"},
                distance=0.15,
            )
        ],
        diverse_feedback_sample=[
            FeedbackEvidence(
                feedback_id="fb_billing",
                text="The invoice payment process is confusing and billing support is slow.",
                metadata={"source": "survey", "segment": "enterprise", "product_area": "billing"},
                distance=0.1,
            )
        ],
        retrieval_parameters=RetrievalParameters(
            feedback_top_k=10, context_top_k=5, diverse_sample_size=5
        ),
        cache_key="fake_cache_key_123",
    )


def _score(score: int = 4) -> dict:
    return {"score": score, "rationale": "Supported by consistent billing complaints."}


def _opportunity(opportunity_id: str = "opp_1", affected_segments: list[str] | None = None) -> dict:
    return {
        "opportunity_id": opportunity_id,
        "title": "Simplify invoice payment",
        "problem_statement": "Enterprise users find invoice payment confusing.",
        "underlying_need": "Users need a clear, fast way to understand and pay invoices.",
        "affected_segments": affected_segments if affected_segments is not None else ["enterprise"],
        "feedback_ids": ["fb_billing", "fb_invoice2"],
        "representative_evidence": [
            {
                "feedback_id": "fb_billing",
                "excerpt": "The invoice payment process is confusing and billing support is slow.",
            }
        ],
        "related_context": [{"document_id": "doc_pricing", "section_id": "doc_pricing_sec000"}],
        "evidence_strength": _score(),
        "severity": _score(),
        "segment_breadth": _score(2),
        "strategic_alignment": _score(3),
        "confidence": _score(4),
        "roadmap_status": "Not explicitly addressed in supplied pricing strategy section.",
        "contradicting_evidence": [],
        "evidence_gaps": ["No feedback from SMB segment on this theme."],
        "discovery_questions": ["What specifically confuses users about the invoice flow?"],
        "recommended_next_step": "Run 3-5 discovery interviews with enterprise billing contacts.",
    }


def _three_opportunities(**kwargs) -> list[dict]:
    return [_opportunity("opp_1", **kwargs), _opportunity("opp_2", **kwargs), _opportunity("opp_3", **kwargs)]


class FakeClient:
    """Generic structured-JSON fake client that records calls and returns a preset payload."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    def generate_structured(self, *, prompt: str, response_schema: dict, cache_key: str) -> str:
        self.calls.append({"prompt": prompt, "response_schema": response_schema, "cache_key": cache_key})
        return json.dumps(self.payload)


def _pass_critic_payload() -> dict:
    """Wire-shaped payload (as Gemini actually returns it): no status field."""
    return {
        "issues": [],
        "unsupported_claims": [],
        "citation_errors": [],
        "duplicate_opportunities": [],
        "required_revisions": [],
    }


def _revise_critic_payload() -> dict:
    """Wire-shaped payload (as Gemini actually returns it): no status field."""
    return {
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


# ---------------------------------------------------------------------------
# Passing run: exactly 2 requests, no revision
# ---------------------------------------------------------------------------


def test_critic_pass_makes_exactly_two_requests_total():
    packet = _packet()
    analyst_client = FakeClient({"opportunities": _three_opportunities()})
    critic_client = FakeClient(_pass_critic_payload())
    revision_client = FakeClient({"opportunities": _three_opportunities()})

    result = run_pipeline(
        packet,
        analyst_client=analyst_client,
        critic_client=critic_client,
        revision_client=revision_client,
    )

    assert len(analyst_client.calls) == 1
    assert len(critic_client.calls) == 1
    assert len(revision_client.calls) == 0
    assert result.model_request_count == 2
    assert result.model_request_count == MAX_PASSING_RUN_REQUESTS


def test_critic_pass_does_not_invoke_revision_client_at_all():
    packet = _packet()
    revision_client = FakeClient({"opportunities": _three_opportunities()})

    run_pipeline(
        packet,
        analyst_client=FakeClient({"opportunities": _three_opportunities()}),
        critic_client=FakeClient(_pass_critic_payload()),
        revision_client=revision_client,
    )

    assert revision_client.calls == []


def test_critic_pass_result_is_not_marked_revised():
    packet = _packet()
    result = run_pipeline(
        packet,
        analyst_client=FakeClient({"opportunities": _three_opportunities()}),
        critic_client=FakeClient(_pass_critic_payload()),
        revision_client=FakeClient({"opportunities": _three_opportunities()}),
    )
    assert result.revised is False
    assert result.critic_output.status == CriticStatus.PASS


# ---------------------------------------------------------------------------
# Revision run: exactly 3 requests, revision happens at most once
# ---------------------------------------------------------------------------


def test_critic_revise_makes_exactly_three_requests_total():
    packet = _packet()
    analyst_client = FakeClient({"opportunities": _three_opportunities(affected_segments=["mobile"])})
    critic_client = FakeClient(_revise_critic_payload())
    revision_client = FakeClient({"opportunities": _three_opportunities(affected_segments=["enterprise"])})

    result = run_pipeline(
        packet,
        analyst_client=analyst_client,
        critic_client=critic_client,
        revision_client=revision_client,
    )

    assert len(analyst_client.calls) == 1
    assert len(critic_client.calls) == 1
    assert len(revision_client.calls) == 1
    assert result.model_request_count == 3
    assert result.model_request_count == MAX_REVISION_RUN_REQUESTS


def test_critic_revise_result_is_marked_revised():
    packet = _packet()
    result = run_pipeline(
        packet,
        analyst_client=FakeClient({"opportunities": _three_opportunities(affected_segments=["mobile"])}),
        critic_client=FakeClient(_revise_critic_payload()),
        revision_client=FakeClient({"opportunities": _three_opportunities(affected_segments=["enterprise"])}),
    )
    assert result.revised is True
    assert result.critic_output.status == CriticStatus.REVISE


def test_revision_happens_at_most_once_even_when_critic_client_is_reused():
    # A single FakeClient instance used for both critic and revision must
    # still only be invoked once for each role: run_pipeline never loops
    # back to the critic after revision, and never revises twice.
    packet = _packet()
    critic_and_revision_client = FakeClient(_revise_critic_payload())
    revision_client = FakeClient({"opportunities": _three_opportunities(affected_segments=["enterprise"])})

    run_pipeline(
        packet,
        analyst_client=FakeClient({"opportunities": _three_opportunities(affected_segments=["mobile"])}),
        critic_client=critic_and_revision_client,
        revision_client=revision_client,
    )

    assert len(critic_and_revision_client.calls) == 1
    assert len(revision_client.calls) == 1


def test_no_execution_path_exceeds_three_requests():
    packet = _packet()
    for critic_payload in (_pass_critic_payload(), _revise_critic_payload()):
        analyst_client = FakeClient({"opportunities": _three_opportunities(affected_segments=["mobile"])})
        critic_client = FakeClient(critic_payload)
        revision_client = FakeClient({"opportunities": _three_opportunities(affected_segments=["enterprise"])})

        run_pipeline(
            packet,
            analyst_client=analyst_client,
            critic_client=critic_client,
            revision_client=revision_client,
        )

        total_requests = len(analyst_client.calls) + len(critic_client.calls) + len(revision_client.calls)
        assert total_requests <= MAX_REVISION_RUN_REQUESTS


# ---------------------------------------------------------------------------
# Revised output is revalidated locally; remaining issues are surfaced
# ---------------------------------------------------------------------------


def test_revised_output_is_revalidated_against_the_original_packet():
    packet = _packet()
    revised_payload = {"opportunities": _three_opportunities(affected_segments=["enterprise"])}

    result = run_pipeline(
        packet,
        analyst_client=FakeClient({"opportunities": _three_opportunities(affected_segments=["mobile"])}),
        critic_client=FakeClient(_revise_critic_payload()),
        revision_client=FakeClient(revised_payload),
    )

    # The final validation_result must reflect the REVISED opportunities
    # (segment now supported), not the original flagged draft.
    assert result.analyst_output.opportunities[0].affected_segments == ["enterprise"]
    for issue in result.validation_result.issues:
        assert issue.issue_type != ValidationIssueType.UNSUPPORTED_SEGMENT

    expected_validation = validate_analyst_output(result.analyst_output, packet)
    assert result.validation_result == expected_validation


def test_remaining_deterministic_issues_after_revision_are_surfaced_not_hidden():
    packet = _packet()
    # The "revised" output still carries an unsupported segment -- the
    # revision model did not fully fix it. This must be surfaced, not
    # silently repaired or dropped, and must not trigger another model call.
    still_broken_payload = {"opportunities": _three_opportunities(affected_segments=["mobile"])}

    analyst_client = FakeClient({"opportunities": _three_opportunities(affected_segments=["mobile"])})
    critic_client = FakeClient(_revise_critic_payload())
    revision_client = FakeClient(still_broken_payload)

    result = run_pipeline(
        packet,
        analyst_client=analyst_client,
        critic_client=critic_client,
        revision_client=revision_client,
    )

    assert result.revised is True
    assert result.model_request_count == 3
    assert len(critic_client.calls) == 1  # critic was NOT called again
    unsupported = [
        issue
        for issue in result.validation_result.issues
        if issue.issue_type == ValidationIssueType.UNSUPPORTED_SEGMENT
    ]
    assert len(unsupported) >= 1
    assert unsupported[0].segment == "mobile"


# ---------------------------------------------------------------------------
# run_revision in isolation
# ---------------------------------------------------------------------------


def test_run_revision_invokes_injected_client_exactly_once():
    packet = _packet()
    from product_feedback_agent.analyst import AnalystOutput
    from product_feedback_agent.critic import CriticOutput

    analyst_output = AnalystOutput.model_validate({"opportunities": _three_opportunities()})
    validation_result = validate_analyst_output(analyst_output, packet)
    critic_output = CriticOutput.model_validate({**_revise_critic_payload(), "status": "revise"})

    client = FakeClient({"opportunities": _three_opportunities(affected_segments=["enterprise"])})
    run_revision(packet, analyst_output, validation_result, critic_output, client=client)
    assert len(client.calls) == 1


def test_run_revision_prompt_contains_required_revisions_and_prior_findings():
    packet = _packet()
    from product_feedback_agent.analyst import AnalystOutput
    from product_feedback_agent.critic import CriticOutput

    analyst_output = AnalystOutput.model_validate({"opportunities": _three_opportunities()})
    validation_result = validate_analyst_output(analyst_output, packet)
    critic_output = CriticOutput.model_validate({**_revise_critic_payload(), "status": "revise"})

    prompt = build_revision_prompt(packet, analyst_output, validation_result, critic_output)
    assert "Remove 'mobile' from affected_segments for opp_1." in prompt
    assert "evidence_count" in prompt
    assert "ONLY revision request" in prompt


def test_run_revision_raises_on_invalid_output():
    packet = _packet()
    from product_feedback_agent.analyst import AnalystOutput
    from product_feedback_agent.critic import CriticOutput

    analyst_output = AnalystOutput.model_validate({"opportunities": _three_opportunities()})
    validation_result = validate_analyst_output(analyst_output, packet)
    critic_output = CriticOutput.model_validate({**_revise_critic_payload(), "status": "revise"})

    client = FakeClient({"opportunities": [_opportunity("opp_1")]})  # only one, invalid
    with pytest.raises(RevisionError):
        run_revision(packet, analyst_output, validation_result, critic_output, client=client)


def test_run_revision_raises_on_empty_output():
    packet = _packet()
    from product_feedback_agent.analyst import AnalystOutput
    from product_feedback_agent.critic import CriticOutput

    analyst_output = AnalystOutput.model_validate({"opportunities": _three_opportunities()})
    validation_result = validate_analyst_output(analyst_output, packet)
    critic_output = CriticOutput.model_validate({**_revise_critic_payload(), "status": "revise"})

    class EmptyClient:
        def generate_structured(self, *, prompt, response_schema, cache_key):
            return ""

    with pytest.raises(RevisionError):
        run_revision(packet, analyst_output, validation_result, critic_output, client=EmptyClient())
