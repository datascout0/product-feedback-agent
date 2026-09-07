import inspect
import json

from product_feedback_agent.analysis_context import (
    AnalysisContextPacket,
    ContextEvidence,
    DatasetStatistics,
    FeedbackEvidence,
    RetrievalParameters,
)
from product_feedback_agent.analyst import AnalystOutput, OpportunityDraft
from product_feedback_agent.validation import (
    ValidationIssueType,
    ValidationResult,
    compute_score,
    validate_analyst_output,
)

# ---------------------------------------------------------------------------
# Fixtures: a deterministic packet with feedback that has segment metadata
# AND feedback that only has product_area metadata (no segment) -- this is
# what makes the real "mobile"/"reporting" affected_segments bug reproducible
# offline (docs/results-log.md, 2026-09-07).
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
            ),
            ContextEvidence(
                section_id="doc_other_sec000",
                document_id="doc_other",
                text="Unrelated context section from a different document.",
                metadata={"heading": "Other"},
                distance=0.25,
            ),
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


def _opportunity(**overrides) -> dict:
    base = {
        "opportunity_id": "opp_1",
        "title": "Simplify invoice payment",
        "problem_statement": "Enterprise users find invoice payment confusing.",
        "underlying_need": "Users need a clear, fast way to understand and pay invoices.",
        "affected_segments": ["enterprise"],
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
    base.update(overrides)
    return base


def _draft(**overrides) -> OpportunityDraft:
    return OpportunityDraft.model_validate(_opportunity(**overrides))


def _output(*drafts: OpportunityDraft) -> AnalystOutput:
    # AnalystOutput requires exactly 3 opportunities; tests that only care
    # about one opportunity's behavior pad with two neutral valid drafts.
    opportunities = list(drafts)
    while len(opportunities) < 3:
        opportunities.append(
            _draft(opportunity_id=f"opp_pad_{len(opportunities)}")
        )
    return AnalystOutput(opportunities=opportunities[:3] if len(drafts) >= 3 else opportunities)


def _issue_types(result: ValidationResult, opportunity_id: str = "opp_1") -> set[ValidationIssueType]:
    return {
        issue.issue_type
        for issue in result.issues
        if issue.opportunity_id == opportunity_id
    }


# ---------------------------------------------------------------------------
# Feedback ID checks
# ---------------------------------------------------------------------------


def test_valid_feedback_id_passes():
    result = validate_analyst_output(_output(_draft()), _packet())
    assert ValidationIssueType.UNKNOWN_FEEDBACK_ID not in _issue_types(result)


def test_invented_feedback_id_is_rejected():
    output = _output(_draft(feedback_ids=["fb_billing", "fb_does_not_exist"]))
    result = validate_analyst_output(output, _packet())
    assert ValidationIssueType.UNKNOWN_FEEDBACK_ID in _issue_types(result)

    issue = next(
        i
        for i in result.issues
        if i.opportunity_id == "opp_1" and i.issue_type == ValidationIssueType.UNKNOWN_FEEDBACK_ID
    )
    assert issue.feedback_id == "fb_does_not_exist"


def test_invented_representative_evidence_feedback_id_is_rejected():
    output = _output(
        _draft(
            representative_evidence=[
                {"feedback_id": "fb_nope", "excerpt": "anything"},
            ]
        )
    )
    result = validate_analyst_output(output, _packet())
    assert ValidationIssueType.UNKNOWN_FEEDBACK_ID in _issue_types(result)


def test_duplicate_feedback_ids_do_not_inflate_evidence_count():
    output = _output(_draft(feedback_ids=["fb_billing", "fb_billing", "fb_invoice2"]))
    result = validate_analyst_output(output, _packet())
    validated = next(o for o in result.opportunities if o.opportunity_id == "opp_1")
    assert validated.evidence_count == 2


def test_evidence_count_excludes_invented_ids():
    output = _output(_draft(feedback_ids=["fb_billing", "fb_does_not_exist"]))
    result = validate_analyst_output(output, _packet())
    validated = next(o for o in result.opportunities if o.opportunity_id == "opp_1")
    assert validated.evidence_count == 1


def test_evidence_count_is_deterministic_across_runs():
    output = _output(_draft(feedback_ids=["fb_invoice2", "fb_billing", "fb_invoice2"]))
    packet = _packet()
    first = validate_analyst_output(output, packet)
    second = validate_analyst_output(output, packet)
    first_count = next(o for o in first.opportunities if o.opportunity_id == "opp_1").evidence_count
    second_count = next(o for o in second.opportunities if o.opportunity_id == "opp_1").evidence_count
    assert first_count == second_count == 2


# ---------------------------------------------------------------------------
# Exact quotation checks
# ---------------------------------------------------------------------------


def test_valid_exact_quote_passes():
    result = validate_analyst_output(_output(_draft()), _packet())
    assert ValidationIssueType.NON_EXACT_QUOTATION not in _issue_types(result)


def test_paraphrased_quote_is_rejected():
    output = _output(
        _draft(
            representative_evidence=[
                {"feedback_id": "fb_billing", "excerpt": "Billing support is really slow."}
            ]
        )
    )
    result = validate_analyst_output(output, _packet())
    assert ValidationIssueType.NON_EXACT_QUOTATION in _issue_types(result)


def test_non_exact_quote_does_not_raise_and_does_not_get_repaired():
    output = _output(
        _draft(
            representative_evidence=[
                {"feedback_id": "fb_billing", "excerpt": "not even close to the source text"}
            ]
        )
    )
    result = validate_analyst_output(output, _packet())
    issue = next(
        i for i in result.issues if i.issue_type == ValidationIssueType.NON_EXACT_QUOTATION
    )
    assert issue.feedback_id == "fb_billing"


# ---------------------------------------------------------------------------
# Context citation checks
# ---------------------------------------------------------------------------


def test_valid_context_citation_passes():
    result = validate_analyst_output(_output(_draft()), _packet())
    assert not (
        _issue_types(result)
        & {
            ValidationIssueType.UNKNOWN_CONTEXT_DOCUMENT,
            ValidationIssueType.UNKNOWN_CONTEXT_SECTION,
            ValidationIssueType.CONTEXT_PAIRING_MISMATCH,
        }
    )


def test_invented_section_id_is_rejected():
    output = _output(
        _draft(
            related_context=[
                {"document_id": "doc_pricing", "section_id": "doc_pricing_sec999"}
            ]
        )
    )
    result = validate_analyst_output(output, _packet())
    assert ValidationIssueType.UNKNOWN_CONTEXT_SECTION in _issue_types(result)


def test_unknown_document_id_is_rejected():
    output = _output(
        _draft(
            related_context=[
                {"document_id": "doc_missing", "section_id": "doc_pricing_sec000"}
            ]
        )
    )
    result = validate_analyst_output(output, _packet())
    assert ValidationIssueType.UNKNOWN_CONTEXT_DOCUMENT in _issue_types(result)


def test_incorrect_document_section_pairing_is_rejected():
    # Both IDs individually exist in the packet, but never as this pairing.
    output = _output(
        _draft(
            related_context=[
                {"document_id": "doc_pricing", "section_id": "doc_other_sec000"}
            ]
        )
    )
    result = validate_analyst_output(output, _packet())
    assert ValidationIssueType.CONTEXT_PAIRING_MISMATCH in _issue_types(result)


# ---------------------------------------------------------------------------
# Affected-segment checks
# ---------------------------------------------------------------------------


def test_supported_segment_passes():
    result = validate_analyst_output(_output(_draft(affected_segments=["enterprise"])), _packet())
    assert ValidationIssueType.UNSUPPORTED_SEGMENT not in _issue_types(result)


def test_unsupported_segment_is_flagged():
    output = _output(_draft(affected_segments=["smb"]))
    result = validate_analyst_output(output, _packet())
    assert ValidationIssueType.UNSUPPORTED_SEGMENT in _issue_types(result)


def test_product_area_used_as_segment_is_flagged():
    # This reproduces the real analyst finding: "mobile" is a product_area,
    # not a customer segment, for the feedback actually cited.
    output = _output(
        _draft(
            feedback_ids=["fb_mobile"],
            representative_evidence=[
                {
                    "feedback_id": "fb_mobile",
                    "excerpt": "The mobile app crashes when I upload a photo.",
                }
            ],
            affected_segments=["mobile"],
        )
    )
    result = validate_analyst_output(output, _packet())
    assert ValidationIssueType.UNSUPPORTED_SEGMENT in _issue_types(result)

    issue = next(
        i
        for i in result.issues
        if i.opportunity_id == "opp_1" and i.issue_type == ValidationIssueType.UNSUPPORTED_SEGMENT
    )
    assert issue.segment == "mobile"
    assert "product_area" in issue.message


def test_segment_supported_only_by_an_invalid_feedback_id_is_still_flagged():
    # fb_billing has segment "enterprise", but it is NOT among this
    # opportunity's cited feedback_ids, so "enterprise" must not be treated
    # as supported here.
    output = _output(_draft(feedback_ids=["fb_mobile"], affected_segments=["enterprise"]))
    result = validate_analyst_output(output, _packet())
    assert ValidationIssueType.UNSUPPORTED_SEGMENT in _issue_types(result)


# ---------------------------------------------------------------------------
# Scoring formula
# ---------------------------------------------------------------------------


def test_score_formula_is_correct():
    total = compute_score(
        evidence_strength=4,
        severity=4,
        strategic_alignment=3,
        segment_breadth=2,
        confidence=4,
    )
    expected = (4 / 5 * 30) + (4 / 5 * 25) + (3 / 5 * 20) + (2 / 5 * 15) + (4 / 5 * 10)
    assert total == expected


def test_score_boundary_all_ones():
    total = compute_score(
        evidence_strength=1, severity=1, strategic_alignment=1, segment_breadth=1, confidence=1
    )
    expected = (1 / 5) * (30 + 25 + 20 + 15 + 10)
    assert total == expected
    assert round(total, 4) == 20.0


def test_score_boundary_all_fives():
    total = compute_score(
        evidence_strength=5, severity=5, strategic_alignment=5, segment_breadth=5, confidence=5
    )
    assert total == 100.0


def test_validated_opportunity_score_matches_compute_score():
    output = _output(
        _draft(
            evidence_strength=_score(5),
            severity=_score(1),
            strategic_alignment=_score(3),
            segment_breadth=_score(2),
            confidence=_score(4),
        )
    )
    result = validate_analyst_output(output, _packet())
    validated = next(o for o in result.opportunities if o.opportunity_id == "opp_1")
    expected = compute_score(
        evidence_strength=5, severity=1, strategic_alignment=3, segment_breadth=2, confidence=4
    )
    assert validated.score.total == expected
    assert validated.score.evidence_strength == 5
    assert validated.score.severity == 1


def test_roadmap_status_does_not_affect_score():
    output_a = _output(_draft(roadmap_status="Fully addressed by the current roadmap."))
    output_b = _output(_draft(roadmap_status="Not addressed anywhere in supplied context."))
    packet = _packet()

    score_a = next(
        o for o in validate_analyst_output(output_a, packet).opportunities if o.opportunity_id == "opp_1"
    ).score.total
    score_b = next(
        o for o in validate_analyst_output(output_b, packet).opportunities if o.opportunity_id == "opp_1"
    ).score.total

    assert score_a == score_b


# ---------------------------------------------------------------------------
# Offline guarantee
# ---------------------------------------------------------------------------


def test_validator_signature_takes_no_model_client():
    # The validator must be architecturally incapable of making a model
    # call: it accepts only already-produced data, never a client.
    signature = inspect.signature(validate_analyst_output)
    assert "client" not in signature.parameters
    assert set(signature.parameters) == {"output", "packet"}


def test_validator_result_is_json_serializable_without_extra_state():
    result = validate_analyst_output(_output(_draft()), _packet())
    # Round-trips cleanly; nothing about the result depends on hidden,
    # non-deterministic, or network-derived state.
    json.loads(result.model_dump_json())


# ---------------------------------------------------------------------------
# Optional local regression input (ignored file, never required by CI)
# ---------------------------------------------------------------------------


def test_real_analyst_smoke_output_is_caught_by_segment_validation():
    """If the local smoke fixtures exist, replay the real finding offline.

    Both files are gitignored local artifacts (see docs/results-log.md,
    2026-09-07). This test is skipped, not failed, when they are absent, so
    the normal offline suite never depends on ignored local files.
    """
    import pytest
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    smoke_path = repo_root / "data" / "local" / "analyst-smoke.json"
    packet_path = (
        repo_root
        / "data"
        / "local"
        / "analysis-context-cache"
        / "e4cfdd21879143b76031823715f6c104f4bd9a8ccfd24bb070050c8ebea44d0c.json"
    )
    if not smoke_path.exists() or not packet_path.exists():
        pytest.skip("optional local smoke fixtures not present")

    output = AnalystOutput.model_validate(json.loads(smoke_path.read_text(encoding="utf-8")))
    packet = AnalysisContextPacket.model_validate(
        json.loads(packet_path.read_text(encoding="utf-8"))
    )

    result = validate_analyst_output(output, packet)

    unsupported_segments = {
        issue.segment
        for issue in result.issues
        if issue.issue_type == ValidationIssueType.UNSUPPORTED_SEGMENT
    }
    assert "mobile" in unsupported_segments
    assert "reporting" in unsupported_segments
