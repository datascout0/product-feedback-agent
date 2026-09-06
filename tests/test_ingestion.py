import csv
import json
from pathlib import Path

import pytest

from product_feedback_agent.ingestion import (
    MAX_RECORDS,
    IngestionError,
    load_feedback_file,
)

SAMPLES_DIR = Path(__file__).resolve().parents[1] / "data" / "samples"


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _write_json(path: Path, data) -> Path:
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return path


def test_valid_csv_sample_loads():
    result = load_feedback_file(SAMPLES_DIR / "feedback_sample.csv")

    assert result.source_filename == "feedback_sample.csv"
    assert result.total_input_count == 7
    # one exact duplicate row, one blank-text invalid row
    assert result.duplicates_removed == 1
    assert len(result.invalid_records) == 1
    assert len(result.valid_records) == 5


def test_valid_json_sample_loads():
    result = load_feedback_file(SAMPLES_DIR / "feedback_sample.json")

    assert result.source_filename == "feedback_sample.json"
    assert result.total_input_count == 3
    assert result.invalid_records == []
    assert result.duplicates_removed == 0
    assert len(result.valid_records) == 3


def test_deterministic_generated_ids_match_across_runs(tmp_path):
    fieldnames = ["feedback_id", "text", "source", "customer_id"]
    row = {
        "feedback_id": "",
        "text": "Dashboard takes too long to load.",
        "source": "survey",
        "customer_id": "CUST-500",
    }

    path_a = _write_csv(tmp_path / "a.csv", fieldnames, [row])
    path_b = _write_csv(tmp_path / "b.csv", fieldnames, [row])

    result_a = load_feedback_file(path_a)
    result_b = load_feedback_file(path_b)

    id_a = result_a.valid_records[0].feedback_id
    id_b = result_b.valid_records[0].feedback_id

    assert id_a == id_b
    assert id_a.startswith("fb_")


def test_supplied_feedback_id_preserved_and_trimmed(tmp_path):
    fieldnames = ["feedback_id", "text", "source"]
    row = {"feedback_id": "  FB-777  ", "text": "Great support experience.", "source": "survey"}
    path = _write_csv(tmp_path / "supplied.csv", fieldnames, [row])

    result = load_feedback_file(path)

    assert len(result.valid_records) == 1
    assert result.valid_records[0].feedback_id == "FB-777"


def test_source_is_normalized(tmp_path):
    data = [
        {"text": "a", "source": "Support Ticket"},
        {"text": "b", "source": "  App-Store Review  "},
        {"text": "c", "source": "Zendesk - Chat"},
    ]
    path = _write_json(tmp_path / "test_source_norm.json", data)
    result = load_feedback_file(path)

    sources = {record.text: record.source for record in result.valid_records}
    assert sources["a"] == "support_ticket"
    assert sources["b"] == "app_store_review"
    assert sources["c"] == "zendesk_chat"


def test_original_text_preserved_exactly(tmp_path):
    original = "  Weird   spacing,  but exact.  "
    data = [{"text": original, "source": "survey"}]
    path = _write_json(tmp_path / "text.json", data)

    result = load_feedback_file(path)

    assert result.valid_records[0].text == original


def test_unknown_fields_are_preserved_in_metadata(tmp_path):
    data = [
        {
            "text": "Needs dark mode.",
            "source": "survey",
            "channel": "zoom",
            "metadata": {"priority": "low"},
        }
    ]
    path = _write_json(tmp_path / "unknown.json", data)

    result = load_feedback_file(path)

    metadata = result.valid_records[0].metadata
    assert metadata["channel"] == "zoom"
    assert metadata["priority"] == "low"


def test_exact_duplicate_records_are_removed(tmp_path):
    fieldnames = ["text", "source", "customer_id"]
    row = {"text": "Search results are hard to filter.", "source": "support", "customer_id": "CUST-1"}
    path = _write_csv(tmp_path / "dupes.csv", fieldnames, [row, dict(row), dict(row)])

    result = load_feedback_file(path)

    assert result.total_input_count == 3
    assert result.duplicates_removed == 2
    assert len(result.valid_records) == 1


def test_identical_text_different_customer_is_not_deduplicated(tmp_path):
    fieldnames = ["text", "source", "customer_id"]
    rows = [
        {"text": "Search results are hard to filter.", "source": "support", "customer_id": "CUST-1"},
        {"text": "Search results are hard to filter.", "source": "support", "customer_id": "CUST-2"},
    ]
    path = _write_csv(tmp_path / "distinct.csv", fieldnames, rows)

    result = load_feedback_file(path)

    assert result.duplicates_removed == 0
    assert len(result.valid_records) == 2
    customer_ids = {record.customer_id for record in result.valid_records}
    assert customer_ids == {"CUST-1", "CUST-2"}


def test_identical_content_different_supplied_ids_are_preserved(tmp_path):
    fieldnames = ["feedback_id", "text", "source", "customer_id"]
    rows = [
        {
            "feedback_id": "FB-AAA",
            "text": "Search results are hard to filter.",
            "source": "support",
            "customer_id": "CUST-1",
        },
        {
            "feedback_id": "FB-BBB",
            "text": "Search results are hard to filter.",
            "source": "support",
            "customer_id": "CUST-1",
        },
    ]
    path = _write_csv(tmp_path / "distinct_ids.csv", fieldnames, rows)

    result = load_feedback_file(path)

    assert result.duplicates_removed == 0
    assert len(result.valid_records) == 2
    feedback_ids = {record.feedback_id for record in result.valid_records}
    assert feedback_ids == {"FB-AAA", "FB-BBB"}


def test_identical_supplied_id_and_content_is_deduplicated(tmp_path):
    fieldnames = ["feedback_id", "text", "source", "customer_id"]
    row = {
        "feedback_id": "FB-SAME",
        "text": "Search results are hard to filter.",
        "source": "support",
        "customer_id": "CUST-1",
    }
    path = _write_csv(tmp_path / "same_id.csv", fieldnames, [row, dict(row)])

    result = load_feedback_file(path)

    assert result.duplicates_removed == 1
    assert len(result.valid_records) == 1


def test_invalid_records_are_collected_not_fatal(tmp_path):
    fieldnames = ["text", "source"]
    rows = [
        {"text": "Valid feedback text.", "source": "survey"},
        {"text": "   ", "source": "survey"},
    ]
    path = _write_csv(tmp_path / "invalid.csv", fieldnames, rows)

    result = load_feedback_file(path)

    assert len(result.valid_records) == 1
    assert len(result.invalid_records) == 1
    assert result.invalid_records[0].index == 1
    assert "text" in result.invalid_records[0].error.lower()


def test_dataset_over_max_records_is_rejected(tmp_path):
    fieldnames = ["text", "source"]
    rows = [{"text": f"Feedback number {i}.", "source": "survey"} for i in range(MAX_RECORDS + 1)]
    path = _write_csv(tmp_path / "too_many.csv", fieldnames, rows)

    with pytest.raises(IngestionError):
        load_feedback_file(path)


def test_unsupported_extension_fails_clearly(tmp_path):
    path = tmp_path / "feedback.txt"
    path.write_text("text,source\nhi,survey\n", encoding="utf-8")

    with pytest.raises(IngestionError):
        load_feedback_file(path)


def test_malformed_json_fails_clearly(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(IngestionError):
        load_feedback_file(path)


def test_non_array_json_top_level_fails_clearly(tmp_path):
    path = tmp_path / "object.json"
    path.write_text(json.dumps({"text": "hi", "source": "survey"}), encoding="utf-8")

    with pytest.raises(IngestionError):
        load_feedback_file(path)
