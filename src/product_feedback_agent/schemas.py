from __future__ import annotations

from datetime import date as Date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FeedbackInput(BaseModel):
    """Raw feedback accepted from CSV, JSON, APIs, or other sources."""

    model_config = ConfigDict(extra="forbid")

    feedback_id: str | None = None
    text: str
    source: str
    product_area: str | None = None
    segment: str | None = None
    date: Date | None = None
    customer_id: str | None = None
    rating: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be empty")
        # Preserve the original feedback text exactly.
        return value

    @field_validator("source")
    @classmethod
    def validate_source(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("source must not be empty")
        return value

    @field_validator("feedback_id")
    @classmethod
    def normalize_optional_feedback_id(cls, value: str | None) -> str | None:
        if value is None:
            return None

        value = value.strip()
        return value or None


class FeedbackRecord(FeedbackInput):
    """Normalized feedback record with a guaranteed stable ID."""

    feedback_id: str

    @field_validator("feedback_id")
    @classmethod
    def validate_feedback_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("feedback_id must not be empty")
        return value


class ContextSection(BaseModel):
    """One coherent section of a product-context document, kept verbatim for citation."""

    section_id: str
    document_id: str
    heading: str | None = None
    text: str
    section_index: int


class ContextDocument(BaseModel):
    """A parsed product-context document (strategy, roadmap, personas, etc.)."""

    document_id: str
    filename: str
    document_type: str
    sections: list[ContextSection] = Field(default_factory=list)
