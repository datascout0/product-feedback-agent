"""Feedback ingestion: load raw CSV/JSON feedback into normalized FeedbackRecords.

Design notes (see docs/decisions.md for the broader architecture):
- Deterministic feedback IDs are a SHA-256 digest of the normalized record
  content (everything except feedback_id itself), so re-importing the same
  record always yields the same generated ID.
- "Exact duplicate" means identical normalized content, INCLUDING
  feedback_id (supplied or generated). Records that differ by even one
  field (e.g. customer_id, or a different supplied feedback_id) are
  distinct and both are kept, even if the feedback text matches.
- Explicit metadata (a metadata column/field) takes precedence over
  unrecognized columns swept into metadata on key collisions.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from product_feedback_agent.schemas import FeedbackInput, FeedbackRecord

MAX_RECORDS = 200

KNOWN_FIELDS = {
    "feedback_id",
    "text",
    "source",
    "product_area",
    "segment",
    "date",
    "customer_id",
    "rating",
    "metadata",
}

# Optional fields where a blank CSV/JSON string must become None rather than
# being passed through (feedback_id blanking is already handled by the
# FeedbackInput validator, and text/source are required).
BLANKABLE_OPTIONAL_FIELDS = ("product_area", "segment", "date", "customer_id", "rating")


class IngestionError(Exception):
    """Raised when an entire input file cannot be processed."""


@dataclass
class InvalidRecord:
    index: int
    raw: Any
    error: str


@dataclass
class IngestionResult:
    source_filename: str
    total_input_count: int
    valid_records: list[FeedbackRecord] = field(default_factory=list)
    invalid_records: list[InvalidRecord] = field(default_factory=list)
    duplicates_removed: int = 0


def load_feedback_file(path: str | Path) -> IngestionResult:
    """Load a UTF-8 CSV or JSON feedback file into an IngestionResult."""
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix == ".csv":
        raw_records = _read_csv(path)
    elif suffix == ".json":
        raw_records = _read_json(path)
    else:
        raise IngestionError(f"Unsupported file extension: {path.suffix!r}")

    total_input_count = len(raw_records)
    if total_input_count > MAX_RECORDS:
        raise IngestionError(
            f"Input contains {total_input_count} records, exceeding the "
            f"MVP maximum of {MAX_RECORDS} records per run."
        )

    result = IngestionResult(source_filename=path.name, total_input_count=total_input_count)

    seen_keys: set[str] = set()
    for index, raw in enumerate(raw_records):
        try:
            normalized = _normalize_raw_record(raw)
            feedback_input = FeedbackInput(**normalized)
            record = _to_record(feedback_input)
        except (ValidationError, ValueError) as exc:
            result.invalid_records.append(InvalidRecord(index=index, raw=raw, error=str(exc)))
            continue

        dedup_key = _duplicate_key(record)
        if dedup_key in seen_keys:
            result.duplicates_removed += 1
            continue
        seen_keys.add(dedup_key)
        result.valid_records.append(record)

    return result


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        return [dict(row) for row in reader]


def _read_json(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fh:
        try:
            data = json.load(fh)
        except json.JSONDecodeError as exc:
            raise IngestionError(f"Malformed JSON in {path.name}: {exc}") from exc

    if not isinstance(data, list):
        raise IngestionError(
            f"Top-level JSON in {path.name} must be an array of feedback objects."
        )

    return data


def _blank_to_none(value: Any) -> Any:
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _normalize_raw_record(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("record must be a JSON object")

    data = dict(raw)

    unknown_fields = {key: data.pop(key) for key in list(data) if key not in KNOWN_FIELDS}

    for key in BLANKABLE_OPTIONAL_FIELDS:
        if key in data:
            data[key] = _blank_to_none(data[key])

    metadata_value = _blank_to_none(data.get("metadata"))
    if metadata_value is None:
        base_metadata: dict[str, Any] = {}
    elif isinstance(metadata_value, str):
        try:
            parsed = json.loads(metadata_value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"metadata is not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("metadata JSON string must decode to an object")
        base_metadata = parsed
    elif isinstance(metadata_value, dict):
        base_metadata = metadata_value
    else:
        raise ValueError("metadata must be an object or a JSON object string")

    # Explicit metadata wins over unrecognized columns on key collisions.
    merged_metadata = {**unknown_fields, **base_metadata}
    data["metadata"] = merged_metadata

    return data


def _normalize_source(value: str) -> str:
    value = value.strip().lower()
    return re.sub(r"[\s-]+", "_", value)


def _to_record(feedback_input: FeedbackInput) -> FeedbackRecord:
    data = feedback_input.model_dump()
    data["source"] = _normalize_source(data["source"])

    if not data.get("feedback_id"):
        data["feedback_id"] = _generate_feedback_id(data)

    return FeedbackRecord(**data)


def _generate_feedback_id(normalized_data: dict[str, Any]) -> str:
    content = {key: value for key, value in normalized_data.items() if key != "feedback_id"}
    canonical = json.dumps(content, sort_keys=True, default=str)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"fb_{digest}"


def _duplicate_key(record: FeedbackRecord) -> str:
    data = record.model_dump()
    return json.dumps(data, sort_keys=True, default=str)
