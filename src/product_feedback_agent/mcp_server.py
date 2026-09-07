"""Thin MCP server exposing the existing, already-tested Python capabilities.

Design notes:
- This module contains no analysis logic of its own. Every tool is a thin
  wrapper that resolves paths, calls the existing ingestion/retrieval/
  analysis_context functions, and returns their (already-typed) results.
- Path arguments are resolved relative to the project root (derived from this
  file's location), never the process's current working directory, so the
  server behaves the same regardless of where the MCP host launches it from.
- The real SemanticRetrieval (and the Sentence Transformer model it lazily
  loads) is only constructed the first time a tool actually needs it, via
  `_LazyRetrieval`. Importing this module, or calling `create_mcp_server()`
  with an injected fake retrieval, never touches the model or the on-disk
  Chroma store. This keeps `import product_feedback_agent.mcp_server` and
  ordinary pytest runs fully offline.
- Anticipated failures (missing files, blank queries, non-positive limits,
  zero valid feedback records) are raised as `ToolError` so MCP reports a
  clear message back to the caller instead of a generic crash.
"""

from __future__ import annotations

from pathlib import Path

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from product_feedback_agent.analysis_context import (
    DEFAULT_CACHE_DIR,
    AnalysisContextPacket,
    FeedbackEvidence,
)
from product_feedback_agent.analysis_context import (
    prepare_analysis_context as run_prepare_analysis_context,
)
from product_feedback_agent.context import ContextIngestionError, load_context_file
from product_feedback_agent.github_action import GitHubActionError, IssueActionResult
from product_feedback_agent.github_action import create_opportunity_issue as run_create_opportunity_issue
from product_feedback_agent.ingestion import IngestionError, load_feedback_file
from product_feedback_agent.orchestrator import OrchestrationResult
from product_feedback_agent.retrieval import DEFAULT_CHROMA_PATH, SemanticRetrieval

SERVER_NAME = "Product Feedback Agent"


def _project_root() -> Path:
    """The repository root, derived from this file's location (not cwd)."""
    return Path(__file__).resolve().parents[2]


def _resolve_existing_file(project_root: Path, raw_path: str, *, kind: str) -> Path:
    """Resolve `raw_path` (absolute, or relative to `project_root`) to a file.

    Raises ToolError with a clear message if the path does not exist.
    """
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = project_root / candidate

    if not candidate.is_file():
        raise ToolError(f"{kind} file not found: {raw_path!r} (resolved to {candidate})")
    return candidate


class _LazyRetrieval:
    """Defers constructing the real SemanticRetrieval until first use."""

    def __init__(self, project_root: Path, retrieval: SemanticRetrieval | None = None) -> None:
        self._project_root = project_root
        self._retrieval = retrieval

    def get(self) -> SemanticRetrieval:
        if self._retrieval is None:
            # Anchor the persistence directory to project_root rather than
            # the process's cwd, matching how other paths in this module
            # are resolved.
            self._retrieval = SemanticRetrieval(self._project_root / DEFAULT_CHROMA_PATH)
        return self._retrieval


def create_mcp_server(
    *,
    project_root: Path | str | None = None,
    retrieval: SemanticRetrieval | None = None,
) -> MCPServer:
    """Build the MCP server and register its tools.

    `project_root` overrides where relative file arguments are resolved from
    (defaults to the repository root). `retrieval` lets callers (tests, or a
    future in-process caller) inject a fake/pre-built SemanticRetrieval
    instead of the real persistent one.
    """
    root = Path(project_root).resolve() if project_root is not None else _project_root()
    retrieval_holder = _LazyRetrieval(root, retrieval)

    server = MCPServer(SERVER_NAME)

    @server.tool()
    def prepare_analysis_context(
        feedback_file: str,
        context_files: list[str],
        analysis_query: str,
        feedback_top_k: int = 10,
        context_top_k: int = 5,
        diverse_sample_size: int = 5,
    ) -> AnalysisContextPacket:
        """Load feedback + context files and build a deterministic evidence packet."""
        feedback_path = _resolve_existing_file(root, feedback_file, kind="Feedback")
        context_paths = [
            _resolve_existing_file(root, path, kind="Context") for path in context_files
        ]

        try:
            ingestion_result = load_feedback_file(feedback_path)
        except IngestionError as exc:
            raise ToolError(str(exc)) from exc

        if not ingestion_result.valid_records:
            raise ToolError(
                f"{feedback_file!r} contains zero valid feedback records "
                f"({len(ingestion_result.invalid_records)} invalid, "
                f"{ingestion_result.duplicates_removed} duplicates removed)."
            )

        try:
            context_documents = [load_context_file(path) for path in context_paths]
        except ContextIngestionError as exc:
            raise ToolError(str(exc)) from exc

        try:
            return run_prepare_analysis_context(
                feedback_records=ingestion_result.valid_records,
                context_documents=context_documents,
                retrieval=retrieval_holder.get(),
                analysis_query=analysis_query,
                feedback_top_k=feedback_top_k,
                context_top_k=context_top_k,
                diverse_sample_size=diverse_sample_size,
                # Anchor the cache to project_root rather than the process's
                # cwd, matching how feedback/context file arguments resolve.
                cache_dir=root / DEFAULT_CACHE_DIR,
            )
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    @server.tool()
    def search_feedback(query: str, top_k: int = 10) -> list[FeedbackEvidence]:
        """Semantically search feedback already indexed by prepare_analysis_context."""
        if not query.strip():
            raise ToolError("query must not be blank")
        if top_k <= 0:
            raise ToolError("top_k must be positive")

        try:
            results = retrieval_holder.get().search_feedback(query, top_k=top_k)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

        return [
            FeedbackEvidence(
                feedback_id=result.feedback_id,
                text=result.text,
                metadata=result.metadata,
                distance=result.distance,
            )
            for result in results
        ]

    @server.tool()
    def create_opportunity_issue(
        orchestration_result: OrchestrationResult,
        opportunity_id: str,
        repo: str,
        approved: bool = False,
        dry_run: bool = True,
        allowed_repos: list[str] | None = None,
    ) -> IssueActionResult:
        """Render, and only if human-approved, create one opportunity's GitHub issue.

        `approved=False` (the default) and `dry_run=True` (the default) each
        independently guarantee no GitHub mutation happens: both must be
        explicitly overridden by a human-approved caller. When `allowed_repos`
        is omitted, the server falls back to the GITHUB_DEMO_REPO environment
        variable (read lazily, never at import time).
        """
        allow_set = frozenset(allowed_repos) if allowed_repos is not None else None
        try:
            return run_create_opportunity_issue(
                orchestration_result,
                opportunity_id,
                repo,
                approved=approved,
                dry_run=dry_run,
                allowed_repos=allow_set,
            )
        except GitHubActionError as exc:
            raise ToolError(str(exc)) from exc

    return server


PROJECT_ROOT = _project_root()
mcp = create_mcp_server(project_root=PROJECT_ROOT)


if __name__ == "__main__":
    mcp.run()
