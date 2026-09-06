"""Deterministic analysis-context packet preparation.

Design notes:
- prepare_analysis_context is the pure-Python operation the future MCP tool
  of the same name will wrap. It never calls an LLM.
- Dataset statistics are computed over the *complete* supplied feedback
  dataset, not over search results, so they are independent of top_k.
- Retrieval happens at most once per feedback/context collection per call:
  one semantic search each, plus a diversity pass over the feedback search
  results. Individual themes are never searched separately.
- The packet is cached to a local JSON file keyed by a SHA-256 fingerprint
  over canonicalized (sorted) inputs, so incidental list reordering does not
  change the cache key but any content/metadata/parameter change does. On a
  cache hit, indexing and retrieval are skipped entirely.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from pydantic import BaseModel, ConfigDict

from product_feedback_agent.retrieval import (
    ContextSearchResult,
    FeedbackSearchResult,
    SemanticRetrieval,
)
from product_feedback_agent.schemas import ContextDocument, FeedbackRecord

DEFAULT_CACHE_DIR = "data/local/analysis-context-cache"


class DatasetStatistics(BaseModel):
    """Deterministic statistics over the complete valid feedback dataset."""

    model_config = ConfigDict(extra="forbid")

    total_feedback_count: int
    counts_by_source: dict[str, int]
    counts_by_product_area: dict[str, int]
    counts_by_segment: dict[str, int]
    count_with_rating: int
    average_rating: float | None


class FeedbackEvidence(BaseModel):
    """One feedback search result, preserved verbatim for citation."""

    model_config = ConfigDict(extra="forbid")

    feedback_id: str
    text: str
    metadata: dict[str, Any]
    distance: float


class ContextEvidence(BaseModel):
    """One product-context search result, preserved verbatim for citation."""

    model_config = ConfigDict(extra="forbid")

    section_id: str
    document_id: str
    text: str
    metadata: dict[str, Any]
    distance: float


class RetrievalParameters(BaseModel):
    """The retrieval limits used to build a given packet."""

    model_config = ConfigDict(extra="forbid")

    feedback_top_k: int
    context_top_k: int
    diverse_sample_size: int


class AnalysisContextPacket(BaseModel):
    """One deterministic evidence packet for the future analyst step."""

    model_config = ConfigDict(extra="forbid")

    analysis_query: str
    dataset_statistics: DatasetStatistics
    retrieved_feedback: list[FeedbackEvidence]
    retrieved_context: list[ContextEvidence]
    diverse_feedback_sample: list[FeedbackEvidence]
    retrieval_parameters: RetrievalParameters
    cache_key: str


def prepare_analysis_context(
    feedback_records: Sequence[FeedbackRecord],
    context_documents: Sequence[ContextDocument],
    retrieval: SemanticRetrieval,
    analysis_query: str,
    *,
    feedback_top_k: int = 10,
    context_top_k: int = 5,
    diverse_sample_size: int = 5,
    cache_dir: str | Path = DEFAULT_CACHE_DIR,
) -> AnalysisContextPacket:
    """Build (or load from cache) one deterministic analysis-context packet."""
    feedback_records = list(feedback_records)
    context_documents = list(context_documents)

    query = analysis_query.strip()
    if not query:
        raise ValueError("analysis_query must not be blank")

    _validate_positive("feedback_top_k", feedback_top_k)
    _validate_positive("context_top_k", context_top_k)
    _validate_positive("diverse_sample_size", diverse_sample_size)

    cache_key = _compute_cache_key(
        feedback_records=feedback_records,
        context_documents=context_documents,
        analysis_query=query,
        feedback_top_k=feedback_top_k,
        context_top_k=context_top_k,
        diverse_sample_size=diverse_sample_size,
    )

    cache_path = Path(cache_dir) / f"{cache_key}.json"
    cached_packet = _load_cached_packet(cache_path)
    if cached_packet is not None:
        return cached_packet

    retrieval.index_feedback(feedback_records)
    retrieval.index_context(context_documents)

    feedback_results = retrieval.search_feedback(query, top_k=feedback_top_k)
    context_results = retrieval.search_context(query, top_k=context_top_k)
    diverse_sample = _select_diverse_sample(feedback_results, diverse_sample_size)

    packet = AnalysisContextPacket(
        analysis_query=query,
        dataset_statistics=_compute_dataset_statistics(feedback_records),
        retrieved_feedback=[_to_feedback_evidence(result) for result in feedback_results],
        retrieved_context=[_to_context_evidence(result) for result in context_results],
        diverse_feedback_sample=[_to_feedback_evidence(result) for result in diverse_sample],
        retrieval_parameters=RetrievalParameters(
            feedback_top_k=feedback_top_k,
            context_top_k=context_top_k,
            diverse_sample_size=diverse_sample_size,
        ),
        cache_key=cache_key,
    )

    _save_cached_packet(cache_path, packet)
    return packet


def _validate_positive(name: str, value: int) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be positive")


def _compute_dataset_statistics(records: list[FeedbackRecord]) -> DatasetStatistics:
    counts_by_source: dict[str, int] = {}
    counts_by_product_area: dict[str, int] = {}
    counts_by_segment: dict[str, int] = {}
    count_with_rating = 0
    rating_sum = 0.0

    for record in records:
        counts_by_source[record.source] = counts_by_source.get(record.source, 0) + 1

        if record.product_area is not None:
            counts_by_product_area[record.product_area] = (
                counts_by_product_area.get(record.product_area, 0) + 1
            )

        if record.segment is not None:
            counts_by_segment[record.segment] = counts_by_segment.get(record.segment, 0) + 1

        if record.rating is not None:
            count_with_rating += 1
            rating_sum += record.rating

    average_rating = rating_sum / count_with_rating if count_with_rating > 0 else None

    return DatasetStatistics(
        total_feedback_count=len(records),
        counts_by_source=dict(sorted(counts_by_source.items())),
        counts_by_product_area=dict(sorted(counts_by_product_area.items())),
        counts_by_segment=dict(sorted(counts_by_segment.items())),
        count_with_rating=count_with_rating,
        average_rating=average_rating,
    )


def _select_diverse_sample(
    results: list[FeedbackSearchResult], sample_size: int
) -> list[FeedbackSearchResult]:
    """Greedily pick a diverse, deterministic sample from ranked search results.

    The first semantic result is always kept as the starting point. Each
    subsequent pick is the remaining candidate with the highest diversity
    gain (unseen source/product_area/segment count against everything
    selected so far), breaking ties by better original semantic rank. Once
    no remaining candidate adds any diversity, the rest of the slots are
    filled by original semantic rank. The output is returned in original
    rank order.
    """
    if not results:
        return []

    sample_size = min(sample_size, len(results))

    seen_sources: set[Any] = set()
    seen_product_areas: set[Any] = set()
    seen_segments: set[Any] = set()

    remaining_indices = list(range(len(results)))
    selected_indices: list[int] = []

    first_idx = remaining_indices.pop(0)
    selected_indices.append(first_idx)
    _record_metadata(results[first_idx], seen_sources, seen_product_areas, seen_segments)

    while len(selected_indices) < sample_size and remaining_indices:
        best_idx = max(
            remaining_indices,
            key=lambda idx: (
                _diversity_gain(results[idx], seen_sources, seen_product_areas, seen_segments),
                -idx,
            ),
        )

        remaining_indices.remove(best_idx)
        selected_indices.append(best_idx)
        _record_metadata(results[best_idx], seen_sources, seen_product_areas, seen_segments)

    selected_indices.sort()
    return [results[idx] for idx in selected_indices]


def _diversity_gain(
    result: FeedbackSearchResult,
    seen_sources: set[Any],
    seen_product_areas: set[Any],
    seen_segments: set[Any],
) -> int:
    source = result.metadata.get("source")
    product_area = result.metadata.get("product_area")
    segment = result.metadata.get("segment")
    return (
        int(source is not None and source not in seen_sources)
        + int(product_area is not None and product_area not in seen_product_areas)
        + int(segment is not None and segment not in seen_segments)
    )


def _record_metadata(
    result: FeedbackSearchResult,
    seen_sources: set[Any],
    seen_product_areas: set[Any],
    seen_segments: set[Any],
) -> None:
    source = result.metadata.get("source")
    product_area = result.metadata.get("product_area")
    segment = result.metadata.get("segment")
    if source is not None:
        seen_sources.add(source)
    if product_area is not None:
        seen_product_areas.add(product_area)
    if segment is not None:
        seen_segments.add(segment)


def _to_feedback_evidence(result: FeedbackSearchResult) -> FeedbackEvidence:
    return FeedbackEvidence(
        feedback_id=result.feedback_id,
        text=result.text,
        metadata=result.metadata,
        distance=result.distance,
    )


def _to_context_evidence(result: ContextSearchResult) -> ContextEvidence:
    return ContextEvidence(
        section_id=result.section_id,
        document_id=result.document_id,
        text=result.text,
        metadata=result.metadata,
        distance=result.distance,
    )


def _compute_cache_key(
    feedback_records: list[FeedbackRecord],
    context_documents: list[ContextDocument],
    analysis_query: str,
    feedback_top_k: int,
    context_top_k: int,
    diverse_sample_size: int,
) -> str:
    """SHA-256 fingerprint over canonicalized (sorted) inputs.

    Sorting by stable ID before hashing means incidental list reordering of
    the caller's records/documents/sections never changes the key, while any
    change to content, IDs, metadata, the query, or the retrieval parameters
    does.
    """
    feedback_payload = sorted(
        (record.model_dump(mode="json") for record in feedback_records),
        key=lambda data: data["feedback_id"],
    )
    context_payload = sorted(
        (_context_document_payload(document) for document in context_documents),
        key=lambda data: data["document_id"],
    )

    fingerprint_input = {
        "analysis_query": analysis_query,
        "feedback_records": feedback_payload,
        "context_documents": context_payload,
        "feedback_top_k": feedback_top_k,
        "context_top_k": context_top_k,
        "diverse_sample_size": diverse_sample_size,
    }

    canonical = json.dumps(fingerprint_input, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _context_document_payload(document: ContextDocument) -> dict[str, Any]:
    payload = document.model_dump(mode="json")
    payload["sections"] = sorted(payload["sections"], key=lambda section: section["section_id"])
    return payload


def _load_cached_packet(cache_path: Path) -> AnalysisContextPacket | None:
    if not cache_path.exists():
        return None
    data = json.loads(cache_path.read_text(encoding="utf-8"))
    return AnalysisContextPacket(**data)


def _save_cached_packet(cache_path: Path, packet: AnalysisContextPacket) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(packet.model_dump_json(indent=2), encoding="utf-8")
