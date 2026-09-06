import hashlib
import math
import re
from datetime import date

import pytest

from product_feedback_agent.retrieval import SemanticRetrieval
from product_feedback_agent.schemas import ContextDocument, ContextSection, FeedbackRecord

_WORD_RE = re.compile(r"[a-z0-9]+")


class FakeEmbedder:
    """Deterministic bag-of-words embedder for fast, offline tests.

    Texts that share words end up with closer (smaller-distance) vectors than
    texts that don't, which is enough to exercise semantic search behavior
    without loading a real model.
    """

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


@pytest.fixture
def retrieval(tmp_path) -> SemanticRetrieval:
    return SemanticRetrieval(persist_directory=tmp_path / "chroma", embedder=FakeEmbedder())


def _feedback(feedback_id: str, text: str, **kwargs) -> FeedbackRecord:
    return FeedbackRecord(feedback_id=feedback_id, text=text, source=kwargs.pop("source", "survey"), **kwargs)


def _billing_record() -> FeedbackRecord:
    return _feedback(
        "fb_billing",
        "The invoice payment process is confusing and billing support is slow.",
        segment="enterprise",
        product_area="billing",
        date=date(2026, 1, 15),
        customer_id="cust_1",
        rating=2.0,
    )


def _theme_record() -> FeedbackRecord:
    return _feedback(
        "fb_theme",
        "Please add a dark mode theme option to the UI.",
        segment="smb",
        product_area="ui",
    )


def _pricing_section() -> ContextDocument:
    return ContextDocument(
        document_id="doc_pricing",
        filename="pricing.md",
        document_type="strategy",
        sections=[
            ContextSection(
                section_id="doc_pricing_sec000",
                document_id="doc_pricing",
                heading="Enterprise Pricing",
                text="Enterprise pricing strategy focuses on annual contracts and volume discounts.",
                section_index=0,
            )
        ],
    )


def _onboarding_section() -> ContextDocument:
    return ContextDocument(
        document_id="doc_onboarding",
        filename="onboarding.md",
        document_type="context",
        sections=[
            ContextSection(
                section_id="doc_onboarding_sec000",
                document_id="doc_onboarding",
                heading="New User Onboarding",
                text="The onboarding flow guides new users through initial account setup.",
                section_index=0,
            )
        ],
    )


def test_feedback_records_can_be_indexed(retrieval):
    retrieval.index_feedback([_billing_record(), _theme_record()])

    results = retrieval.search_feedback("payment", top_k=5)

    assert {result.feedback_id for result in results} == {"fb_billing", "fb_theme"}


def test_context_sections_can_be_indexed(retrieval):
    retrieval.index_context([_pricing_section(), _onboarding_section()])

    results = retrieval.search_context("pricing", top_k=5)

    assert {result.section_id for result in results} == {
        "doc_pricing_sec000",
        "doc_onboarding_sec000",
    }


def test_repeated_indexing_is_idempotent(retrieval):
    retrieval.index_feedback([_billing_record()])
    retrieval.index_feedback([_billing_record()])
    retrieval.index_context([_pricing_section()])
    retrieval.index_context([_pricing_section()])

    assert retrieval._feedback_collection.count() == 1
    assert retrieval._context_collection.count() == 1


def test_semantic_query_returns_expected_feedback(retrieval):
    retrieval.index_feedback([_billing_record(), _theme_record()])

    results = retrieval.search_feedback("invoice payment issue", top_k=1)

    assert len(results) == 1
    assert results[0].feedback_id == "fb_billing"


def test_semantic_query_returns_expected_context_section(retrieval):
    retrieval.index_context([_pricing_section(), _onboarding_section()])

    results = retrieval.search_context("enterprise pricing contracts", top_k=1)

    assert len(results) == 1
    assert results[0].section_id == "doc_pricing_sec000"


def test_result_preserves_feedback_id(retrieval):
    retrieval.index_feedback([_billing_record()])

    results = retrieval.search_feedback("invoice", top_k=1)

    assert results[0].feedback_id == "fb_billing"


def test_result_preserves_document_and_section_ids(retrieval):
    retrieval.index_context([_pricing_section()])

    results = retrieval.search_context("pricing", top_k=1)

    assert results[0].document_id == "doc_pricing"
    assert results[0].section_id == "doc_pricing_sec000"


def test_original_feedback_text_is_unchanged(retrieval):
    record = _billing_record()
    retrieval.index_feedback([record])

    results = retrieval.search_feedback("invoice", top_k=1)

    assert results[0].text == record.text


def test_original_context_text_is_unchanged(retrieval):
    document = _pricing_section()
    retrieval.index_context([document])

    results = retrieval.search_context("pricing", top_k=1)

    assert results[0].text == document.sections[0].text


def test_feedback_metadata_is_retained(retrieval):
    retrieval.index_feedback([_billing_record()])

    results = retrieval.search_feedback("invoice", top_k=1)

    metadata = results[0].metadata
    assert metadata["source"] == "survey"
    assert metadata["segment"] == "enterprise"
    assert metadata["product_area"] == "billing"
    assert metadata["date"] == "2026-01-15"
    assert metadata["customer_id"] == "cust_1"
    assert metadata["rating"] == 2.0


def test_context_metadata_is_retained(retrieval):
    retrieval.index_context([_pricing_section()])

    results = retrieval.search_context("pricing", top_k=1)

    metadata = results[0].metadata
    assert metadata["document_id"] == "doc_pricing"
    assert metadata["section_id"] == "doc_pricing_sec000"
    assert metadata["filename"] == "pricing.md"
    assert metadata["document_type"] == "strategy"
    assert metadata["heading"] == "Enterprise Pricing"
    assert metadata["section_index"] == 0


def test_empty_feedback_collection_returns_empty_list(retrieval):
    assert retrieval.search_feedback("anything", top_k=5) == []


def test_empty_context_collection_returns_empty_list(retrieval):
    assert retrieval.search_context("anything", top_k=5) == []


@pytest.mark.parametrize("top_k", [0, -1])
def test_invalid_top_k_is_rejected_for_feedback(retrieval, top_k):
    retrieval.index_feedback([_billing_record()])
    with pytest.raises(ValueError):
        retrieval.search_feedback("invoice", top_k=top_k)


@pytest.mark.parametrize("top_k", [0, -1])
def test_invalid_top_k_is_rejected_for_context(retrieval, top_k):
    retrieval.index_context([_pricing_section()])
    with pytest.raises(ValueError):
        retrieval.search_context("pricing", top_k=top_k)


def test_feedback_and_context_collections_do_not_mix(retrieval):
    shared_id = "shared_001"
    feedback = FeedbackRecord(feedback_id=shared_id, text="Feedback text about invoices.", source="survey")
    context_document = ContextDocument(
        document_id="doc_shared",
        filename="shared.md",
        document_type="context",
        sections=[
            ContextSection(
                section_id=shared_id,
                document_id="doc_shared",
                heading=None,
                text="Context text about onboarding.",
                section_index=0,
            )
        ],
    )

    retrieval.index_feedback([feedback])
    retrieval.index_context([context_document])

    feedback_results = retrieval.search_feedback("invoices", top_k=5)
    context_results = retrieval.search_context("onboarding", top_k=5)

    assert [result.text for result in feedback_results] == ["Feedback text about invoices."]
    assert [result.text for result in context_results] == ["Context text about onboarding."]


def test_bare_context_sections_can_be_indexed_without_document(retrieval):
    section = ContextSection(
        section_id="bare_sec000",
        document_id="doc_bare",
        heading="Bare Section",
        text="A section indexed without its parent document.",
        section_index=0,
    )

    retrieval.index_context([section])
    results = retrieval.search_context("bare section", top_k=1)

    assert results[0].section_id == "bare_sec000"
    assert "filename" not in results[0].metadata
    assert "document_type" not in results[0].metadata
