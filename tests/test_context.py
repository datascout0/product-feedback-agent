from pathlib import Path

import pytest

from product_feedback_agent.context import ContextIngestionError, load_context_file

SAMPLES_DIR = Path(__file__).resolve().parents[1] / "data" / "samples"


def test_valid_markdown_sample_loads():
    document = load_context_file(SAMPLES_DIR / "product_strategy.md")

    assert document.filename == "product_strategy.md"
    assert document.document_type == "strategy"
    assert len(document.sections) > 1
    for index, section in enumerate(document.sections):
        assert section.document_id == document.document_id
        assert section.section_index == index


def test_valid_plain_text_document(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("Customers keep asking for bulk export.", encoding="utf-8")

    document = load_context_file(path)

    assert document.document_type == "context"
    assert len(document.sections) == 1
    section = document.sections[0]
    assert section.heading is None
    assert section.text == "Customers keep asking for bulk export."
    assert section.section_index == 0


def test_stable_document_id_across_repeated_imports(tmp_path):
    path_a = tmp_path / "a" / "roadmap.md"
    path_b = tmp_path / "b" / "roadmap.md"
    path_a.parent.mkdir()
    path_b.parent.mkdir()
    content = "# Now\n\nShip the thing.\n"
    path_a.write_text(content, encoding="utf-8")
    path_b.write_text(content, encoding="utf-8")

    document_a = load_context_file(path_a)
    document_b = load_context_file(path_b)

    assert document_a.document_id == document_b.document_id
    assert document_a.document_id.startswith("doc_")


def test_stable_section_ids_across_repeated_imports(tmp_path):
    content = "# Heading One\n\nBody one.\n\n# Heading Two\n\nBody two.\n"
    path_a = tmp_path / "features.md"
    path_a.write_text(content, encoding="utf-8")

    first_load = load_context_file(path_a)
    second_load = load_context_file(path_a)

    ids_first = [section.section_id for section in first_load.sections]
    ids_second = [section.section_id for section in second_load.sections]
    assert ids_first == ids_second
    assert len(set(ids_first)) == len(ids_first)


def test_markdown_sections_split_on_headings(tmp_path):
    content = "# First\n\nFirst body.\n\n## Second\n\nSecond body.\n\n# Third\n\nThird body.\n"
    path = tmp_path / "features.md"
    path.write_text(content, encoding="utf-8")

    document = load_context_file(path)

    headings = [section.heading for section in document.sections]
    assert headings == ["First", "Second", "Third"]


def test_markdown_heading_and_body_text_preserved(tmp_path):
    content = "# Onboarding\n\nUsers drop off during SSO setup.\nThis is a known gap.\n"
    path = tmp_path / "notes.md"
    path.write_text(content, encoding="utf-8")

    document = load_context_file(path)

    section = document.sections[0]
    assert section.heading == "Onboarding"
    assert "# Onboarding" in section.text
    assert "Users drop off during SSO setup." in section.text
    assert "This is a known gap." in section.text


def test_markdown_intro_text_before_first_heading_is_preserved(tmp_path):
    content = "This is context that appears before any heading.\n\n# Details\n\nMore text.\n"
    path = tmp_path / "research_notes.md"
    path.write_text(content, encoding="utf-8")

    document = load_context_file(path)

    assert document.sections[0].heading is None
    assert document.sections[0].text == "This is context that appears before any heading.\n\n"
    assert document.sections[1].heading == "Details"


def test_known_filename_infers_document_type(tmp_path):
    cases = {
        "product_strategy.md": "strategy",
        "roadmap.md": "roadmap",
        "personas.md": "personas",
        "features.md": "features",
        "research_notes.md": "research",
    }
    for filename, expected_type in cases.items():
        path = tmp_path / filename
        path.write_text("Some content.", encoding="utf-8")
        document = load_context_file(path)
        assert document.document_type == expected_type


def test_unknown_filename_becomes_context(tmp_path):
    path = tmp_path / "misc_notes.md"
    path.write_text("Some content.", encoding="utf-8")

    document = load_context_file(path)

    assert document.document_type == "context"


def test_empty_file_is_rejected(tmp_path):
    path = tmp_path / "empty.md"
    path.write_text("   \n\n  ", encoding="utf-8")

    with pytest.raises(ContextIngestionError):
        load_context_file(path)


def test_plain_text_preserves_exact_leading_and_trailing_whitespace(tmp_path):
    content = "\n\n  Customers keep asking for bulk export.  \n\n"
    path = tmp_path / "notes.txt"
    path.write_text(content, encoding="utf-8")

    document = load_context_file(path)

    assert document.sections[0].text == content


def test_markdown_section_preserves_exact_source_text(tmp_path):
    content = "# Onboarding\n\nUsers drop off during SSO setup.\n  \nThis is a known gap.\n"
    path = tmp_path / "notes.md"
    path.write_text(content, encoding="utf-8")

    document = load_context_file(path)

    assert document.sections[0].text == content


def test_unsupported_extension_fails_clearly(tmp_path):
    path = tmp_path / "notes.pdf"
    path.write_text("Some content.", encoding="utf-8")

    with pytest.raises(ContextIngestionError):
        load_context_file(path)
