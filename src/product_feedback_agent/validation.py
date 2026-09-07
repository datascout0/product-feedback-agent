"""Deterministic validation and scoring for analyst opportunity drafts.

Design notes:
- This module makes NO network or model calls of any kind. It operates only
  on an already-produced AnalystOutput and the AnalysisContextPacket it was
  built from. There is no client parameter anywhere in this module.
- Every claim an OpportunityDraft makes (feedback IDs, quoted excerpts,
  context citations, affected segments) is checked against the packet that
  was actually supplied to the analyst. Unsupported claims are never
  silently repaired -- they are reported as structured ValidationIssue
  entries so a later critic stage (and a human) can see exactly what failed.
- evidence_count and the final opportunity score are always computed here,
  never trusted from the model. See docs/decisions.md D004/D005 and the
  real-analyst finding recorded in docs/results-log.md (2026-09-07): a real
  Gemini response used "mobile" and "reporting" -- product_area values -- as
  affected_segments. This module's segment check exists specifically to
  catch that class of error.
- opportunity_id uniqueness across the three opportunities in one
  AnalystOutput is also checked here (DUPLICATE_OPPORTUNITY_ID), added for
  the Stage 16 deterministic evaluation suite (see eval/cases.py). This is
  additive: it does not change scoring, the critic contract, or the request
  budget.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict

from product_feedback_agent.analysis_context import AnalysisContextPacket, FeedbackEvidence
from product_feedback_agent.analyst import AnalystOutput, OpportunityDraft

# Locked transparent scoring weights (see docs/decisions.md). Do not add,
# remove, or reweight components here without updating the decision log.
_EVIDENCE_STRENGTH_WEIGHT = 30
_SEVERITY_WEIGHT = 25
_STRATEGIC_ALIGNMENT_WEIGHT = 20
_SEGMENT_BREADTH_WEIGHT = 15
_CONFIDENCE_WEIGHT = 10
_SCORE_COMPONENT_MAX = 5


class ValidationIssueType(str, Enum):
    UNKNOWN_FEEDBACK_ID = "unknown_feedback_id"
    NON_EXACT_QUOTATION = "non_exact_quotation"
    UNKNOWN_CONTEXT_DOCUMENT = "unknown_context_document"
    UNKNOWN_CONTEXT_SECTION = "unknown_context_section"
    CONTEXT_PAIRING_MISMATCH = "context_pairing_mismatch"
    UNSUPPORTED_SEGMENT = "unsupported_segment"
    DUPLICATE_OPPORTUNITY_ID = "duplicate_opportunity_id"


class ValidationIssue(BaseModel):
    """One structured, human- and critic-readable validation finding."""

    model_config = ConfigDict(extra="forbid")

    opportunity_id: str
    issue_type: ValidationIssueType
    message: str
    feedback_id: str | None = None
    document_id: str | None = None
    section_id: str | None = None
    segment: str | None = None


class ScoreBreakdown(BaseModel):
    """The score components actually used, plus the locally computed total."""

    model_config = ConfigDict(extra="forbid")

    evidence_strength: int
    severity: int
    strategic_alignment: int
    segment_breadth: int
    confidence: int
    total: float


class ValidatedOpportunity(BaseModel):
    """One opportunity after deterministic validation and scoring."""

    model_config = ConfigDict(extra="forbid")

    opportunity_id: str
    evidence_count: int
    score: ScoreBreakdown
    issues: list[ValidationIssue]

    @property
    def is_valid(self) -> bool:
        return len(self.issues) == 0


class ValidationResult(BaseModel):
    """The complete deterministic validation outcome for one AnalystOutput."""

    model_config = ConfigDict(extra="forbid")

    opportunities: list[ValidatedOpportunity]
    issues: list[ValidationIssue]

    @property
    def is_valid(self) -> bool:
        return len(self.issues) == 0


def compute_score(
    *,
    evidence_strength: int,
    severity: int,
    strategic_alignment: int,
    segment_breadth: int,
    confidence: int,
) -> float:
    """The locked, transparent opportunity-score formula.

    Roadmap status is deliberately never an input here: roadmap coverage is
    kept separate from the score (docs/decisions.md D002/D004).
    """
    return (
        evidence_strength / _SCORE_COMPONENT_MAX * _EVIDENCE_STRENGTH_WEIGHT
        + severity / _SCORE_COMPONENT_MAX * _SEVERITY_WEIGHT
        + strategic_alignment / _SCORE_COMPONENT_MAX * _STRATEGIC_ALIGNMENT_WEIGHT
        + segment_breadth / _SCORE_COMPONENT_MAX * _SEGMENT_BREADTH_WEIGHT
        + confidence / _SCORE_COMPONENT_MAX * _CONFIDENCE_WEIGHT
    )


def validate_analyst_output(
    output: AnalystOutput, packet: AnalysisContextPacket
) -> ValidationResult:
    """Validate and score every opportunity in `output` against `packet`.

    Pure function: no I/O, no network, no model client. Never raises on an
    unsupported claim -- it reports the claim as a ValidationIssue instead.
    """
    feedback_by_id = _index_feedback(packet)
    context_document_ids = {evidence.document_id for evidence in packet.retrieved_context}
    context_section_ids = {evidence.section_id for evidence in packet.retrieved_context}
    context_pairs = {
        (evidence.document_id, evidence.section_id) for evidence in packet.retrieved_context
    }
    product_area_values = set(packet.dataset_statistics.counts_by_product_area)
    duplicate_opportunity_ids = _find_duplicate_ids(output)

    validated_opportunities: list[ValidatedOpportunity] = []
    all_issues: list[ValidationIssue] = []

    for opportunity in output.opportunities:
        issues: list[ValidationIssue] = []

        if opportunity.opportunity_id in duplicate_opportunity_ids:
            issues.append(
                ValidationIssue(
                    opportunity_id=opportunity.opportunity_id,
                    issue_type=ValidationIssueType.DUPLICATE_OPPORTUNITY_ID,
                    message=(
                        f"opportunity_id '{opportunity.opportunity_id}' is used by more than "
                        "one opportunity in this AnalystOutput."
                    ),
                )
            )

        valid_feedback_ids = _validate_feedback_ids(opportunity, feedback_by_id, issues)
        _validate_quotations(opportunity, feedback_by_id, issues)
        _validate_context_citations(
            opportunity, context_document_ids, context_section_ids, context_pairs, issues
        )
        _validate_segments(
            opportunity, feedback_by_id, valid_feedback_ids, product_area_values, issues
        )

        evidence_count = len(valid_feedback_ids)
        total = compute_score(
            evidence_strength=opportunity.evidence_strength.score,
            severity=opportunity.severity.score,
            strategic_alignment=opportunity.strategic_alignment.score,
            segment_breadth=opportunity.segment_breadth.score,
            confidence=opportunity.confidence.score,
        )

        validated_opportunities.append(
            ValidatedOpportunity(
                opportunity_id=opportunity.opportunity_id,
                evidence_count=evidence_count,
                score=ScoreBreakdown(
                    evidence_strength=opportunity.evidence_strength.score,
                    severity=opportunity.severity.score,
                    strategic_alignment=opportunity.strategic_alignment.score,
                    segment_breadth=opportunity.segment_breadth.score,
                    confidence=opportunity.confidence.score,
                    total=total,
                ),
                issues=issues,
            )
        )
        all_issues.extend(issues)

    return ValidationResult(opportunities=validated_opportunities, issues=all_issues)


def _find_duplicate_ids(output: AnalystOutput) -> set[str]:
    """Return the set of opportunity_id values used by more than one opportunity."""
    seen: set[str] = set()
    duplicates: set[str] = set()
    for opportunity in output.opportunities:
        if opportunity.opportunity_id in seen:
            duplicates.add(opportunity.opportunity_id)
        seen.add(opportunity.opportunity_id)
    return duplicates


def _index_feedback(packet: AnalysisContextPacket) -> dict[str, FeedbackEvidence]:
    feedback_by_id: dict[str, FeedbackEvidence] = {}
    for evidence in (*packet.retrieved_feedback, *packet.diverse_feedback_sample):
        feedback_by_id.setdefault(evidence.feedback_id, evidence)
    return feedback_by_id


def _validate_feedback_ids(
    opportunity: OpportunityDraft,
    feedback_by_id: dict[str, FeedbackEvidence],
    issues: list[ValidationIssue],
) -> list[str]:
    """Return the deduplicated, packet-valid feedback IDs cited by `opportunity`."""
    valid_ids: list[str] = []
    seen: set[str] = set()

    for feedback_id in opportunity.feedback_ids:
        if feedback_id not in feedback_by_id:
            issues.append(
                ValidationIssue(
                    opportunity_id=opportunity.opportunity_id,
                    issue_type=ValidationIssueType.UNKNOWN_FEEDBACK_ID,
                    message=(
                        f"feedback_id '{feedback_id}' does not exist in the supplied "
                        "AnalysisContextPacket."
                    ),
                    feedback_id=feedback_id,
                )
            )
            continue
        if feedback_id not in seen:
            seen.add(feedback_id)
            valid_ids.append(feedback_id)

    for quote in opportunity.representative_evidence:
        if quote.feedback_id not in feedback_by_id:
            issues.append(
                ValidationIssue(
                    opportunity_id=opportunity.opportunity_id,
                    issue_type=ValidationIssueType.UNKNOWN_FEEDBACK_ID,
                    message=(
                        f"representative_evidence cites feedback_id '{quote.feedback_id}', "
                        "which does not exist in the supplied AnalysisContextPacket."
                    ),
                    feedback_id=quote.feedback_id,
                )
            )

    return valid_ids


def _validate_quotations(
    opportunity: OpportunityDraft,
    feedback_by_id: dict[str, FeedbackEvidence],
    issues: list[ValidationIssue],
) -> None:
    for quote in opportunity.representative_evidence:
        evidence = feedback_by_id.get(quote.feedback_id)
        if evidence is None:
            continue  # already reported by _validate_feedback_ids
        if quote.excerpt not in evidence.text:
            issues.append(
                ValidationIssue(
                    opportunity_id=opportunity.opportunity_id,
                    issue_type=ValidationIssueType.NON_EXACT_QUOTATION,
                    message=(
                        f"Excerpt for feedback_id '{quote.feedback_id}' is not an exact "
                        "substring of the cited feedback text."
                    ),
                    feedback_id=quote.feedback_id,
                )
            )


def _validate_context_citations(
    opportunity: OpportunityDraft,
    context_document_ids: set[str],
    context_section_ids: set[str],
    context_pairs: set[tuple[str, str]],
    issues: list[ValidationIssue],
) -> None:
    for citation in opportunity.related_context:
        pair = (citation.document_id, citation.section_id)
        if pair in context_pairs:
            continue

        if citation.document_id not in context_document_ids:
            issue_type = ValidationIssueType.UNKNOWN_CONTEXT_DOCUMENT
            message = (
                f"document_id '{citation.document_id}' does not exist in the supplied "
                "retrieved_context."
            )
        elif citation.section_id not in context_section_ids:
            issue_type = ValidationIssueType.UNKNOWN_CONTEXT_SECTION
            message = (
                f"section_id '{citation.section_id}' does not exist in the supplied "
                "retrieved_context."
            )
        else:
            issue_type = ValidationIssueType.CONTEXT_PAIRING_MISMATCH
            message = (
                f"document_id '{citation.document_id}' and section_id '{citation.section_id}' "
                "each exist in retrieved_context, but not as a paired citation."
            )

        issues.append(
            ValidationIssue(
                opportunity_id=opportunity.opportunity_id,
                issue_type=issue_type,
                message=message,
                document_id=citation.document_id,
                section_id=citation.section_id,
            )
        )


def _validate_segments(
    opportunity: OpportunityDraft,
    feedback_by_id: dict[str, FeedbackEvidence],
    valid_feedback_ids: list[str],
    product_area_values: set[str],
    issues: list[ValidationIssue],
) -> None:
    supported_segments = {
        feedback_by_id[feedback_id].metadata.get("segment")
        for feedback_id in valid_feedback_ids
        if feedback_by_id[feedback_id].metadata.get("segment") is not None
    }

    for segment in dict.fromkeys(opportunity.affected_segments):
        if segment in supported_segments:
            continue

        if segment in product_area_values:
            message = (
                f"affected_segments value '{segment}' is a product_area in this dataset, "
                "not a customer segment, and is not supported by any cited feedback's "
                "segment metadata."
            )
        else:
            message = (
                f"affected_segments value '{segment}' is not supported by the segment "
                "metadata of any feedback cited by this opportunity."
            )

        issues.append(
            ValidationIssue(
                opportunity_id=opportunity.opportunity_id,
                issue_type=ValidationIssueType.UNSUPPORTED_SEGMENT,
                message=message,
                segment=segment,
            )
        )
