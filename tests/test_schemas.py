import pytest
from pydantic import ValidationError

from product_feedback_agent.schemas import FeedbackInput, FeedbackRecord


def test_feedback_input_accepts_minimum_fields():
    feedback = FeedbackInput(
        feedback_id="FB-001",
        text="It took me three attempts to connect the integration.",
        source="github",
    )

    assert feedback.feedback_id == "FB-001"
    assert feedback.source == "github"
    assert feedback.metadata == {}


def test_feedback_input_allows_missing_id():
    feedback = FeedbackInput(
        text="Search results are difficult to filter.",
        source="survey",
    )

    assert feedback.feedback_id is None


def test_feedback_record_requires_id():
    with pytest.raises(ValidationError):
        FeedbackRecord(
            text="Search results are difficult to filter.",
            source="survey",
        )


def test_original_feedback_text_is_preserved():
    original = "  The setup instructions are confusing.  "

    feedback = FeedbackInput(
        text=original,
        source="support",
    )

    assert feedback.text == original


def test_blank_feedback_text_is_rejected():
    with pytest.raises(ValidationError):
        FeedbackInput(
            text="   ",
            source="support",
        )


def test_blank_source_is_rejected():
    with pytest.raises(ValidationError):
        FeedbackInput(
            text="Useful feedback",
            source="   ",
        )


def test_optional_fields_are_supported():
    feedback = FeedbackRecord(
        feedback_id="FB-002",
        text="The reporting workflow is slow.",
        source="survey",
        product_area="reporting",
        segment="enterprise",
        date="2026-09-01",
        customer_id="CUST-123",
        rating=2,
        metadata={"region": "EU"},
    )

    assert feedback.product_area == "reporting"
    assert feedback.segment == "enterprise"
    assert feedback.date.isoformat() == "2026-09-01"
    assert feedback.rating == 2
    assert feedback.metadata["region"] == "EU"