import hashlib
import math
import re
from datetime import date

import pytest

from product_feedback_agent.analysis_context import (
    AnalysisContextPacket,
    _select_diverse_sample,
    prepare_analysis_context,
)
from product_feedback_agent.retrieval import FeedbackSearchResult, SemanticRetrieval
from product_feedback_agent.schemas import ContextDocument, ContextSection, FeedbackRecord

_WORD_RE = re.compile(r"[a-z0-9]+")


class FakeEmbedder:
    """Deterministic bag-of-words embedder, mirroring tests/test_retrieval.py.

    Also counts calls so tests can assert a cache hit never re-embeds.
    """

    def __init__(self, dim: int = 32) -> None:
        self.dim = dim
        self.embed_documents_calls = 0
        self.embed_query_calls = 0

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.embed_documents_calls += 1
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        self.embed_query_calls += 1
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
def embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def retrieval(tmp_path, embedder) -> SemanticRetrieval:
    return SemanticRetrieval(persist_directory=tmp_path / "chroma", embedder=embedder)


@pytest.fixture
def cache_dir(tmp_path):
    return tmp_path / "analysis-context-cache"


def _feedback(feedback_id: str, text: str, **kwargs) -> FeedbackRecord:
    kwargs.setdefault("source", "survey")
    return FeedbackRecord(feedback_id=feedback_id, text=text, **kwargs)


def _billing_record() -> FeedbackRecord:
    return _feedback(
        "fb_billing",
        "The invoice payment process is confusing and billing support is slow.",
        source="survey",
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
        source="app_review",
        segment="smb",
        product_area="ui",
        rating=4.0,
    )


def _support_record() -> FeedbackRecord:
    return _feedback(
        "fb_support",
        "Support response times for onboarding questions have been excellent.",
        source="survey",
        segment="enterprise",
        product_area="onboarding",
    )


def _no_rating_record() -> FeedbackRecord:
    return _feedback(
        "fb_no_rating",
        "The mobile app crashes when uploading large files.",
        source="app_review",
        segment="smb",
        product_area="mobile",
    )


def _pricing_document() -> ContextDocument:
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


def _onboarding_document() -> ContextDocument:
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


def _build_packet(retrieval, cache_dir, feedback=None, context=None, query="billing invoice payment", **kwargs):
    feedback = feedback if feedback is not None else [_billing_record(), _theme_record()]
    context = context if context is not None else [_pricing_document()]
    return prepare_analysis_context(
        feedback_records=feedback,
        context_documents=context,
        retrieval=retrieval,
        analysis_query=query,
        cache_dir=cache_dir,
        **kwargs,
    )


# --------------------------------------------------------------------------
# Dataset statistics
# --------------------------------------------------------------------------


def test_dataset_statistics_total_count(retrieval, cache_dir):
    packet = _build_packet(
        retrieval, cache_dir, feedback=[_billing_record(), _theme_record(), _support_record()]
    )
    assert packet.dataset_statistics.total_feedback_count == 3


def test_dataset_statistics_counts_by_source(retrieval, cache_dir):
    packet = _build_packet(
        retrieval, cache_dir, feedback=[_billing_record(), _theme_record(), _support_record()]
    )
    assert packet.dataset_statistics.counts_by_source == {"app_review": 1, "survey": 2}


def test_dataset_statistics_counts_by_product_area(retrieval, cache_dir):
    packet = _build_packet(
        retrieval, cache_dir, feedback=[_billing_record(), _theme_record(), _support_record()]
    )
    assert packet.dataset_statistics.counts_by_product_area == {
        "billing": 1,
        "onboarding": 1,
        "ui": 1,
    }


def test_dataset_statistics_counts_by_segment(retrieval, cache_dir):
    packet = _build_packet(
        retrieval, cache_dir, feedback=[_billing_record(), _theme_record(), _support_record()]
    )
    assert packet.dataset_statistics.counts_by_segment == {"enterprise": 2, "smb": 1}


def test_dataset_statistics_rating_count_and_average(retrieval, cache_dir):
    packet = _build_packet(
        retrieval, cache_dir, feedback=[_billing_record(), _theme_record(), _support_record()]
    )
    assert packet.dataset_statistics.count_with_rating == 2
    assert packet.dataset_statistics.average_rating == pytest.approx(3.0)


def test_dataset_statistics_no_rating_returns_none_average(retrieval, cache_dir):
    packet = _build_packet(retrieval, cache_dir, feedback=[_support_record(), _no_rating_record()])
    assert packet.dataset_statistics.count_with_rating == 0
    assert packet.dataset_statistics.average_rating is None


def test_dataset_statistics_are_deterministic_across_calls(tmp_path):
    feedback = [_billing_record(), _theme_record(), _support_record()]
    context = [_pricing_document()]

    packet_a = prepare_analysis_context(
        feedback_records=feedback,
        context_documents=context,
        retrieval=SemanticRetrieval(persist_directory=tmp_path / "chroma_a", embedder=FakeEmbedder()),
        analysis_query="billing invoice payment",
        cache_dir=tmp_path / "cache_a",
    )
    packet_b = prepare_analysis_context(
        feedback_records=feedback,
        context_documents=context,
        retrieval=SemanticRetrieval(persist_directory=tmp_path / "chroma_b", embedder=FakeEmbedder()),
        analysis_query="billing invoice payment",
        cache_dir=tmp_path / "cache_b",
    )

    assert packet_a.dataset_statistics == packet_b.dataset_statistics


# --------------------------------------------------------------------------
# Retrieved evidence
# --------------------------------------------------------------------------


def test_packet_contains_retrieved_feedback(retrieval, cache_dir):
    packet = _build_packet(retrieval, cache_dir)
    assert len(packet.retrieved_feedback) > 0


def test_packet_contains_retrieved_context(retrieval, cache_dir):
    packet = _build_packet(retrieval, cache_dir)
    assert len(packet.retrieved_context) > 0


def test_feedback_id_preserved(retrieval, cache_dir):
    packet = _build_packet(retrieval, cache_dir, query="invoice payment issue")
    assert "fb_billing" in {item.feedback_id for item in packet.retrieved_feedback}


def test_document_id_and_section_id_preserved(retrieval, cache_dir):
    packet = _build_packet(retrieval, cache_dir, query="enterprise pricing contracts")
    context_item = packet.retrieved_context[0]
    assert context_item.document_id == "doc_pricing"
    assert context_item.section_id == "doc_pricing_sec000"


def test_feedback_text_preserved_exactly(retrieval, cache_dir):
    record = _billing_record()
    packet = _build_packet(retrieval, cache_dir, feedback=[record], query="invoice payment issue")
    match = next(item for item in packet.retrieved_feedback if item.feedback_id == record.feedback_id)
    assert match.text == record.text


def test_context_text_preserved_exactly(retrieval, cache_dir):
    document = _pricing_document()
    packet = _build_packet(retrieval, cache_dir, context=[document], query="enterprise pricing contracts")
    assert packet.retrieved_context[0].text == document.sections[0].text


# --------------------------------------------------------------------------
# Diversity sample
# --------------------------------------------------------------------------


def test_diversity_sample_does_not_exceed_requested_size(retrieval, cache_dir):
    feedback = [_billing_record(), _theme_record(), _support_record(), _no_rating_record()]
    packet = _build_packet(
        retrieval, cache_dir, feedback=feedback, query="product feedback", diverse_sample_size=2
    )
    assert len(packet.diverse_feedback_sample) <= 2


def test_diversity_sample_never_exceeds_available_results(retrieval, cache_dir):
    packet = _build_packet(
        retrieval,
        cache_dir,
        feedback=[_billing_record()],
        query="invoice",
        feedback_top_k=5,
        diverse_sample_size=10,
    )
    assert len(packet.diverse_feedback_sample) == 1


def test_diversity_sample_prefers_multiple_sources_and_segments(retrieval, cache_dir):
    feedback = [_billing_record(), _theme_record(), _support_record(), _no_rating_record()]
    packet = _build_packet(
        retrieval, cache_dir, feedback=feedback, query="product feedback", feedback_top_k=4, diverse_sample_size=3
    )

    sample = packet.diverse_feedback_sample
    sources = {item.metadata.get("source") for item in sample}
    segments = {item.metadata.get("segment") for item in sample}

    assert len(sources) > 1 or len(segments) > 1


def test_diversity_sample_picks_higher_gain_over_next_ranked_candidate():
    # Rank 0 establishes the baseline source/product_area/segment.
    rank0 = FeedbackSearchResult(
        feedback_id="fb_rank0",
        text="baseline result",
        metadata={"source": "survey", "product_area": "billing", "segment": "enterprise"},
        distance=0.1,
    )
    # Rank 1 (next-ranked) adds only one previously unseen dimension (segment).
    rank1 = FeedbackSearchResult(
        feedback_id="fb_rank1",
        text="adds only segment",
        metadata={"source": "survey", "product_area": "billing", "segment": "smb"},
        distance=0.2,
    )
    # Rank 2 (slightly lower ranked) adds three previously unseen dimensions.
    rank2 = FeedbackSearchResult(
        feedback_id="fb_rank2",
        text="adds source, product_area, and segment",
        metadata={"source": "app_review", "product_area": "ui", "segment": "smb"},
        distance=0.3,
    )

    sample = _select_diverse_sample([rank0, rank1, rank2], sample_size=2)

    assert [item.feedback_id for item in sample] == ["fb_rank0", "fb_rank2"]


def test_diversity_sample_is_deterministic(retrieval, cache_dir):
    feedback = [_billing_record(), _theme_record(), _support_record(), _no_rating_record()]

    packet_a = _build_packet(
        retrieval, cache_dir, feedback=feedback, query="product feedback", diverse_sample_size=3
    )
    ids_a = [item.feedback_id for item in packet_a.diverse_feedback_sample]

    # Second call hits the cache; rebuild against a fresh retrieval/cache dir
    # instead to confirm the *algorithm* itself is deterministic, not just
    # that caching returns the same object.
    other_cache_dir = cache_dir.parent / "cache_repeat"
    other_retrieval = SemanticRetrieval(persist_directory=cache_dir.parent / "chroma_repeat", embedder=FakeEmbedder())
    packet_b = _build_packet(
        other_retrieval,
        other_cache_dir,
        feedback=feedback,
        query="product feedback",
        diverse_sample_size=3,
    )
    ids_b = [item.feedback_id for item in packet_b.diverse_feedback_sample]

    assert ids_a == ids_b


def test_diversity_sample_never_manufactures_or_alters_evidence(retrieval, cache_dir):
    feedback = [_billing_record(), _theme_record()]
    packet = _build_packet(retrieval, cache_dir, feedback=feedback, query="product feedback")

    retrieved_by_id = {item.feedback_id: item.text for item in packet.retrieved_feedback}
    for item in packet.diverse_feedback_sample:
        assert item.feedback_id in retrieved_by_id
        assert item.text == retrieved_by_id[item.feedback_id]


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def test_blank_query_is_rejected(retrieval, cache_dir):
    with pytest.raises(ValueError):
        _build_packet(retrieval, cache_dir, query="   ")


@pytest.mark.parametrize("param", ["feedback_top_k", "context_top_k", "diverse_sample_size"])
@pytest.mark.parametrize("value", [0, -1])
def test_zero_or_negative_limits_are_rejected(retrieval, cache_dir, param, value):
    with pytest.raises(ValueError):
        _build_packet(retrieval, cache_dir, **{param: value})


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------


def test_cache_file_is_created(retrieval, cache_dir):
    packet = _build_packet(retrieval, cache_dir)
    cache_file = cache_dir / f"{packet.cache_key}.json"
    assert cache_file.exists()


def test_identical_inputs_produce_the_same_cache_key(tmp_path):
    feedback = [_billing_record(), _theme_record()]
    context = [_pricing_document()]

    key_a = prepare_analysis_context(
        feedback_records=feedback,
        context_documents=context,
        retrieval=SemanticRetrieval(persist_directory=tmp_path / "chroma_a", embedder=FakeEmbedder()),
        analysis_query="billing invoice payment",
        cache_dir=tmp_path / "cache_a",
    ).cache_key
    key_b = prepare_analysis_context(
        feedback_records=feedback,
        context_documents=context,
        retrieval=SemanticRetrieval(persist_directory=tmp_path / "chroma_b", embedder=FakeEmbedder()),
        analysis_query="billing invoice payment",
        cache_dir=tmp_path / "cache_b",
    ).cache_key

    assert key_a == key_b


def test_cache_hit_returns_equivalent_packet(retrieval, cache_dir):
    packet_first = _build_packet(retrieval, cache_dir)
    packet_second = _build_packet(retrieval, cache_dir)

    assert isinstance(packet_second, AnalysisContextPacket)
    assert packet_first == packet_second


def test_cache_hit_does_not_invoke_embedding_or_retrieval_again(retrieval, embedder, cache_dir):
    _build_packet(retrieval, cache_dir)
    calls_after_first = (embedder.embed_documents_calls, embedder.embed_query_calls)

    _build_packet(retrieval, cache_dir)
    calls_after_second = (embedder.embed_documents_calls, embedder.embed_query_calls)

    assert calls_after_second == calls_after_first


def test_changed_query_invalidates_cache(retrieval, cache_dir):
    packet_a = _build_packet(retrieval, cache_dir, query="billing invoice payment")
    packet_b = _build_packet(retrieval, cache_dir, query="dark mode theme")
    assert packet_a.cache_key != packet_b.cache_key


def test_changed_feedback_content_invalidates_cache(retrieval, cache_dir):
    original = _billing_record()
    changed = _feedback(
        "fb_billing",
        "The invoice payment process is now fast and clear.",
        segment="enterprise",
        product_area="billing",
        date=date(2026, 1, 15),
        customer_id="cust_1",
        rating=2.0,
    )

    packet_a = _build_packet(retrieval, cache_dir, feedback=[original, _theme_record()])
    packet_b = _build_packet(retrieval, cache_dir, feedback=[changed, _theme_record()])

    assert packet_a.cache_key != packet_b.cache_key


def test_changed_context_content_invalidates_cache(retrieval, cache_dir):
    original = _pricing_document()
    changed = ContextDocument(
        document_id="doc_pricing",
        filename="pricing.md",
        document_type="strategy",
        sections=[
            ContextSection(
                section_id="doc_pricing_sec000",
                document_id="doc_pricing",
                heading="Enterprise Pricing",
                text="Enterprise pricing strategy now emphasizes monthly billing flexibility.",
                section_index=0,
            )
        ],
    )

    packet_a = _build_packet(retrieval, cache_dir, context=[original])
    packet_b = _build_packet(retrieval, cache_dir, context=[changed])

    assert packet_a.cache_key != packet_b.cache_key


def test_changed_retrieval_parameter_invalidates_cache(retrieval, cache_dir):
    packet_a = _build_packet(retrieval, cache_dir, feedback_top_k=5)
    packet_b = _build_packet(retrieval, cache_dir, feedback_top_k=6)
    assert packet_a.cache_key != packet_b.cache_key


def test_changed_diverse_sample_size_invalidates_cache(retrieval, cache_dir):
    packet_a = _build_packet(retrieval, cache_dir, diverse_sample_size=2)
    packet_b = _build_packet(retrieval, cache_dir, diverse_sample_size=3)
    assert packet_a.cache_key != packet_b.cache_key


def test_input_ordering_does_not_change_cache_key(tmp_path):
    feedback = [_billing_record(), _theme_record()]
    context = [_pricing_document(), _onboarding_document()]

    key_forward = prepare_analysis_context(
        feedback_records=feedback,
        context_documents=context,
        retrieval=SemanticRetrieval(persist_directory=tmp_path / "chroma_forward", embedder=FakeEmbedder()),
        analysis_query="billing invoice payment",
        cache_dir=tmp_path / "cache_forward",
    ).cache_key
    key_reversed = prepare_analysis_context(
        feedback_records=list(reversed(feedback)),
        context_documents=list(reversed(context)),
        retrieval=SemanticRetrieval(persist_directory=tmp_path / "chroma_reversed", embedder=FakeEmbedder()),
        analysis_query="billing invoice payment",
        cache_dir=tmp_path / "cache_reversed",
    ).cache_key

    assert key_forward == key_reversed
