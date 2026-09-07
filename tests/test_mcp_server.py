import asyncio
import sys
from pathlib import Path

import pytest

from product_feedback_agent.retrieval import ContextSearchResult, FeedbackSearchResult
from product_feedback_agent.schemas import ContextDocument, ContextSection


class FakeRetrieval:
    """In-memory stand-in for SemanticRetrieval: no chromadb, no embedder.

    Preserves insertion order and returns items verbatim, which is enough to
    exercise ID/text/metadata preservation without any real semantic ranking.
    """

    def __init__(self) -> None:
        self._feedback: dict[str, object] = {}
        self._context_sections: list[ContextSection] = []
        self.index_feedback_calls = 0
        self.index_context_calls = 0

    def index_feedback(self, records) -> None:
        self.index_feedback_calls += 1
        for record in records:
            self._feedback[record.feedback_id] = record

    def index_context(self, items) -> None:
        self.index_context_calls += 1
        if isinstance(items, (ContextDocument, ContextSection)):
            items = [items]
        for item in items:
            if isinstance(item, ContextDocument):
                self._context_sections.extend(item.sections)
            else:
                self._context_sections.append(item)

    def search_feedback(self, query: str, top_k: int) -> list[FeedbackSearchResult]:
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        records = list(self._feedback.values())[:top_k]
        return [
            FeedbackSearchResult(
                feedback_id=record.feedback_id,
                text=record.text,
                metadata={"source": record.source},
                distance=0.0,
            )
            for record in records
        ]

    def search_context(self, query: str, top_k: int) -> list[ContextSearchResult]:
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        sections = self._context_sections[:top_k]
        return [
            ContextSearchResult(
                section_id=section.section_id,
                document_id=section.document_id,
                text=section.text,
                metadata={},
                distance=0.0,
            )
            for section in sections
        ]


@pytest.fixture
def fake_retrieval() -> FakeRetrieval:
    return FakeRetrieval()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A minimal fake project root with a feedback CSV and a context file."""
    (tmp_path / "feedback.csv").write_text(
        "text,source\n"
        "App crashes on startup,app_review\n"
        "Please add a dark mode option,survey\n",
        encoding="utf-8",
    )
    (tmp_path / "roadmap.md").write_text(
        "# Roadmap\n\nShip search improvements next quarter.\n", encoding="utf-8"
    )
    return tmp_path


@pytest.fixture
def server(project: Path, fake_retrieval: FakeRetrieval):
    from product_feedback_agent.mcp_server import create_mcp_server

    return create_mcp_server(project_root=project, retrieval=fake_retrieval)


def _call(server, name: str, arguments: dict):
    return asyncio.run(server.call_tool(name, arguments))


# ---------------------------------------------------------------------------
# Import safety
# ---------------------------------------------------------------------------


def test_importing_module_does_not_load_sentence_transformer():
    assert "sentence_transformers" not in sys.modules

    import product_feedback_agent.mcp_server  # noqa: F401

    assert "sentence_transformers" not in sys.modules


# ---------------------------------------------------------------------------
# Server / tool registration
# ---------------------------------------------------------------------------


def test_server_can_be_created(server) -> None:
    from mcp.server import MCPServer

    assert isinstance(server, MCPServer)


def test_expected_tools_are_registered(server) -> None:
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    assert names == {"prepare_analysis_context", "search_feedback"}


def test_create_opportunity_issue_tool_not_present_yet(server) -> None:
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    assert "create_opportunity_issue" not in names


# ---------------------------------------------------------------------------
# prepare_analysis_context
# ---------------------------------------------------------------------------


def test_prepare_analysis_context_loads_feedback_csv(server) -> None:
    result = _call(
        server,
        "prepare_analysis_context",
        {
            "feedback_file": "feedback.csv",
            "context_files": ["roadmap.md"],
            "analysis_query": "dark mode",
        },
    )

    assert result.is_error is False
    assert result.structured_content["dataset_statistics"]["total_feedback_count"] == 2


def test_prepare_analysis_context_loads_context_markdown(server) -> None:
    result = _call(
        server,
        "prepare_analysis_context",
        {
            "feedback_file": "feedback.csv",
            "context_files": ["roadmap.md"],
            "analysis_query": "search improvements",
        },
    )

    section_ids = {item["section_id"] for item in result.structured_content["retrieved_context"]}
    assert len(section_ids) > 0


def test_relative_paths_resolve_from_project_root_not_cwd(
    server, project: Path, tmp_path_factory, monkeypatch
) -> None:
    other_cwd = tmp_path_factory.mktemp("elsewhere")
    monkeypatch.chdir(other_cwd)

    result = _call(
        server,
        "prepare_analysis_context",
        {
            "feedback_file": "feedback.csv",
            "context_files": ["roadmap.md"],
            "analysis_query": "dark mode",
        },
    )

    assert result.is_error is False
    assert result.structured_content["dataset_statistics"]["total_feedback_count"] == 2


def test_prepare_analysis_context_returns_structured_packet(server) -> None:
    result = _call(
        server,
        "prepare_analysis_context",
        {
            "feedback_file": "feedback.csv",
            "context_files": ["roadmap.md"],
            "analysis_query": "dark mode",
        },
    )

    packet = result.structured_content
    assert packet["analysis_query"] == "dark mode"
    assert "retrieval_parameters" in packet
    assert "cache_key" in packet


def test_prepare_analysis_context_preserves_feedback_ids(server, project: Path) -> None:
    from product_feedback_agent.ingestion import load_feedback_file

    expected_ids = {
        record.feedback_id for record in load_feedback_file(project / "feedback.csv").valid_records
    }

    result = _call(
        server,
        "prepare_analysis_context",
        {
            "feedback_file": "feedback.csv",
            "context_files": [],
            "analysis_query": "dark mode",
        },
    )

    returned_ids = {item["feedback_id"] for item in result.structured_content["retrieved_feedback"]}
    assert returned_ids <= expected_ids
    assert len(returned_ids) > 0


def test_prepare_analysis_context_preserves_document_and_section_ids(server, project: Path) -> None:
    from product_feedback_agent.context import load_context_file

    expected_document = load_context_file(project / "roadmap.md")

    result = _call(
        server,
        "prepare_analysis_context",
        {
            "feedback_file": "feedback.csv",
            "context_files": ["roadmap.md"],
            "analysis_query": "search improvements",
        },
    )

    context_item = result.structured_content["retrieved_context"][0]
    assert context_item["document_id"] == expected_document.document_id
    assert context_item["section_id"] == expected_document.sections[0].section_id


def test_prepare_analysis_context_missing_feedback_file_fails_clearly(server) -> None:
    with pytest.raises(Exception, match="Feedback file not found"):
        _call(
            server,
            "prepare_analysis_context",
            {"feedback_file": "missing.csv", "context_files": [], "analysis_query": "x"},
        )


def test_prepare_analysis_context_missing_context_file_fails_clearly(server) -> None:
    with pytest.raises(Exception, match="Context file not found"):
        _call(
            server,
            "prepare_analysis_context",
            {
                "feedback_file": "feedback.csv",
                "context_files": ["missing.md"],
                "analysis_query": "x",
            },
        )


def test_prepare_analysis_context_zero_valid_feedback_fails_clearly(server, project: Path) -> None:
    (project / "empty.csv").write_text("text,source\n", encoding="utf-8")

    with pytest.raises(Exception, match="zero valid feedback records"):
        _call(
            server,
            "prepare_analysis_context",
            {"feedback_file": "empty.csv", "context_files": [], "analysis_query": "x"},
        )


def test_prepare_analysis_context_blank_query_fails_clearly(server) -> None:
    with pytest.raises(Exception, match="analysis_query must not be blank"):
        _call(
            server,
            "prepare_analysis_context",
            {"feedback_file": "feedback.csv", "context_files": [], "analysis_query": "   "},
        )


# ---------------------------------------------------------------------------
# search_feedback
# ---------------------------------------------------------------------------


def test_search_feedback_returns_indexed_results(server) -> None:
    _call(
        server,
        "prepare_analysis_context",
        {
            "feedback_file": "feedback.csv",
            "context_files": [],
            "analysis_query": "dark mode",
        },
    )

    result = _call(server, "search_feedback", {"query": "dark mode", "top_k": 5})

    assert result.is_error is False
    assert len(result.structured_content["result"]) > 0


def test_search_feedback_preserves_text_and_ids(server, project: Path) -> None:
    from product_feedback_agent.ingestion import load_feedback_file

    valid_records = load_feedback_file(project / "feedback.csv").valid_records

    _call(
        server,
        "prepare_analysis_context",
        {"feedback_file": "feedback.csv", "context_files": [], "analysis_query": "dark mode"},
    )
    result = _call(server, "search_feedback", {"query": "dark mode", "top_k": 5})

    by_id = {record.feedback_id: record.text for record in valid_records}
    for item in result.structured_content["result"]:
        assert item["feedback_id"] in by_id
        assert item["text"] == by_id[item["feedback_id"]]


def test_search_feedback_blank_query_is_rejected(server) -> None:
    with pytest.raises(Exception, match="query must not be blank"):
        _call(server, "search_feedback", {"query": "   ", "top_k": 5})


@pytest.mark.parametrize("top_k", [0, -1])
def test_search_feedback_non_positive_top_k_is_rejected(server, top_k) -> None:
    with pytest.raises(Exception, match="top_k must be positive"):
        _call(server, "search_feedback", {"query": "dark mode", "top_k": top_k})


def test_search_feedback_empty_collection_returns_empty_list(server) -> None:
    result = _call(server, "search_feedback", {"query": "anything", "top_k": 5})

    assert result.is_error is False
    assert result.structured_content["result"] == []


def test_real_retrieval_is_anchored_to_project_root_not_cwd(
    project: Path, tmp_path_factory, monkeypatch
) -> None:
    """Regression test: the lazily-created real SemanticRetrieval must persist
    under project_root/data/local/chroma even when the MCP host launches the
    process from an unrelated cwd, not under whatever the cwd happens to be.
    """
    import product_feedback_agent.mcp_server as mcp_server_module
    from product_feedback_agent.retrieval import DEFAULT_CHROMA_PATH

    captured_paths: list[Path] = []

    class SpyRetrieval:
        """Records the persist_directory it was constructed with; no chromadb/model."""

        def __init__(self, persist_directory=None, embedder=None) -> None:
            captured_paths.append(Path(persist_directory))

        def search_feedback(self, query: str, top_k: int):
            return []

    monkeypatch.setattr(mcp_server_module, "SemanticRetrieval", SpyRetrieval)

    other_cwd = tmp_path_factory.mktemp("elsewhere")
    monkeypatch.chdir(other_cwd)

    server = mcp_server_module.create_mcp_server(project_root=project)
    result = _call(server, "search_feedback", {"query": "anything", "top_k": 5})

    assert result.is_error is False
    assert len(captured_paths) == 1
    assert captured_paths[0] == project.resolve() / DEFAULT_CHROMA_PATH
    assert str(other_cwd.resolve()) not in str(captured_paths[0])


def test_search_feedback_does_not_require_resending_feedback_file(server) -> None:
    _call(
        server,
        "prepare_analysis_context",
        {"feedback_file": "feedback.csv", "context_files": [], "analysis_query": "dark mode"},
    )

    result = _call(server, "search_feedback", {"query": "crashes", "top_k": 5})

    assert len(result.structured_content["result"]) > 0
