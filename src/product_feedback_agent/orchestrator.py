"""Application-level orchestration above the bounded Group 1 pipeline.

Target flow (see docs/decisions.md D002/D004):

    feedback/context inputs
            -> ingestion
            -> prepare_analysis_context (ONE batched retrieval/context call)
            -> run_pipeline (analyst -> deterministic validator -> critic ->
               optional single revision)
            -> structured OrchestrationResult
            -> human review / approval boundary

Design notes:
- This module contains NO analyst/critic/revision/validation logic of its
  own. It only sequences the existing, already-tested building blocks:
  `load_feedback_file`, `load_context_file`, `prepare_analysis_context`, and
  `run_pipeline`. See docs/decisions.md D004.
- `prepare_analysis_context` is called at most once per `run_analysis` call.
  It has its own internal cache (keyed by content fingerprint), but this
  module never calls it twice for one analysis run.
- No GitHub call, and no other external write, happens anywhere in this
  module. `OrchestrationResult` has no field that could represent an
  automatic approval or a completed external action -- the AI proposes
  opportunities, the deterministic validator verifies evidence, and a human
  decides what happens next (docs/decisions.md D002). `human_review_status`
  is always `HumanReviewStatus.PENDING_REVIEW` for a freshly produced
  result: this module has no code path that can set it to anything else.
- Inputs are explicit function/CLI arguments (feedback file, context files,
  query, optional cache/persistence directories) -- never a hard-coded
  local filesystem path.
- Importing this module never touches the network, an LLM, or the local
  embedding model: `SemanticRetrieval` (and the Sentence Transformer it
  lazily loads) is only constructed inside `run_analysis` when the caller
  did not inject one, mirroring `mcp_server.py`'s `_LazyRetrieval` pattern.
"""

from __future__ import annotations

import argparse
import json
import sys
from enum import Enum
from pathlib import Path
from typing import Sequence

from pydantic import BaseModel, ConfigDict, Field

from product_feedback_agent.analysis_context import (
    DEFAULT_CACHE_DIR,
    prepare_analysis_context,
)
from product_feedback_agent.analyst import AnalystClient, OpportunityDraft
from product_feedback_agent.context import ContextIngestionError, load_context_file
from product_feedback_agent.critic import CriticClient, CriticOutput
from product_feedback_agent.ingestion import IngestionError, load_feedback_file
from product_feedback_agent.pipeline import RevisionClient, run_pipeline
from product_feedback_agent.retrieval import DEFAULT_CHROMA_PATH, SemanticRetrieval
from product_feedback_agent.validation import ValidationResult


class OrchestrationError(Exception):
    """Raised when a full analysis run cannot proceed (bad inputs, no valid feedback)."""


class HumanReviewStatus(str, Enum):
    """Where a produced result stands relative to the human approval boundary.

    This module never sets anything other than PENDING_REVIEW: approving,
    rejecting, or acting on a result is a decision made outside this module
    (by a human, via a separate, explicitly human-triggered step -- see
    Stage 14/15). Nothing here can mark a result as approved.
    """

    PENDING_REVIEW = "pending_review"


class OrchestrationResult(BaseModel):
    """The complete, human-reviewable outcome of one end-to-end analysis run.

    Deliberately has no field that could represent a completed or automatic
    external action (no issue URL, no "approved" flag) -- this is a proposal
    for a human to review, not an executed decision.
    """

    model_config = ConfigDict(extra="forbid")

    cache_key: str
    opportunities: list[OpportunityDraft] = Field(min_length=3, max_length=3)
    validation_result: ValidationResult
    critic_output: CriticOutput
    revised: bool
    model_request_count: int
    unresolved_issue_count: int
    human_review_status: HumanReviewStatus


def run_analysis(
    feedback_file: str | Path,
    context_files: Sequence[str | Path],
    analysis_query: str,
    *,
    feedback_top_k: int = 10,
    context_top_k: int = 5,
    diverse_sample_size: int = 5,
    cache_dir: str | Path = DEFAULT_CACHE_DIR,
    chroma_path: str | Path = DEFAULT_CHROMA_PATH,
    retrieval: SemanticRetrieval | None = None,
    analyst_client: AnalystClient | None = None,
    critic_client: CriticClient | None = None,
    revision_client: RevisionClient | None = None,
) -> OrchestrationResult:
    """Run one complete, bounded, human-reviewable analysis.

    Sequence: ingest feedback + context -> `prepare_analysis_context` (called
    exactly once) -> `run_pipeline` (analyst -> validator -> critic ->
    optional single revision, reused unmodified) -> `OrchestrationResult`.

    Every path argument is explicit; nothing here reads a hard-coded local
    path. `retrieval`/`analyst_client`/`critic_client`/`revision_client` let
    callers (tests, or an alternate host) inject fakes; production callers
    can omit all four and get the real, lazily-constructed implementations.

    Raises `OrchestrationError` if the feedback/context inputs cannot be
    loaded or contain zero valid feedback records. Propagates
    `AnalystError`/`CriticError`/`RevisionError` from `run_pipeline`
    unchanged -- this function adds no retry behavior of its own.
    """
    feedback_path = Path(feedback_file)
    context_paths = [Path(path) for path in context_files]

    try:
        ingestion_result = load_feedback_file(feedback_path)
    except IngestionError as exc:
        raise OrchestrationError(str(exc)) from exc

    if not ingestion_result.valid_records:
        raise OrchestrationError(
            f"{feedback_path} contains zero valid feedback records "
            f"({len(ingestion_result.invalid_records)} invalid, "
            f"{ingestion_result.duplicates_removed} duplicates removed)."
        )

    try:
        context_documents = [load_context_file(path) for path in context_paths]
    except ContextIngestionError as exc:
        raise OrchestrationError(str(exc)) from exc

    active_retrieval = retrieval if retrieval is not None else SemanticRetrieval(chroma_path)

    try:
        packet = prepare_analysis_context(
            feedback_records=ingestion_result.valid_records,
            context_documents=context_documents,
            retrieval=active_retrieval,
            analysis_query=analysis_query,
            feedback_top_k=feedback_top_k,
            context_top_k=context_top_k,
            diverse_sample_size=diverse_sample_size,
            cache_dir=cache_dir,
        )
    except ValueError as exc:
        raise OrchestrationError(str(exc)) from exc

    pipeline_result = run_pipeline(
        packet,
        analyst_client=analyst_client,
        critic_client=critic_client,
        revision_client=revision_client,
    )

    return OrchestrationResult(
        cache_key=packet.cache_key,
        opportunities=pipeline_result.analyst_output.opportunities,
        validation_result=pipeline_result.validation_result,
        critic_output=pipeline_result.critic_output,
        revised=pipeline_result.revised,
        model_request_count=pipeline_result.model_request_count,
        unresolved_issue_count=len(pipeline_result.validation_result.issues),
        human_review_status=HumanReviewStatus.PENDING_REVIEW,
    )


# ---------------------------------------------------------------------------
# CLI / demo entry point
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="product-feedback-agent",
        description=(
            "Run one bounded feedback-to-opportunity analysis and print the "
            "structured result for human review. This command NEVER creates "
            "a GitHub issue or takes any other external action -- it only "
            "produces a proposal for a human to review."
        ),
    )
    parser.add_argument("--feedback-file", required=True, help="Path to a feedback CSV/JSON file.")
    parser.add_argument(
        "--context-file",
        dest="context_files",
        action="append",
        default=[],
        help="Path to a product-context Markdown/text file. May be repeated.",
    )
    parser.add_argument("--query", dest="analysis_query", required=True, help="Analysis query.")
    parser.add_argument("--feedback-top-k", type=int, default=10)
    parser.add_argument("--context-top-k", type=int, default=5)
    parser.add_argument("--diverse-sample-size", type=int, default=5)
    parser.add_argument(
        "--cache-dir",
        default=DEFAULT_CACHE_DIR,
        help="Analysis-context cache directory (default: %(default)s).",
    )
    parser.add_argument(
        "--chroma-path",
        default=DEFAULT_CHROMA_PATH,
        help="Local Chroma persistence directory (default: %(default)s).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI/demo entry point: run one analysis, print JSON, exit non-zero on failure.

    This is a thin presentation layer over `run_analysis`. It makes no
    GitHub call and requests no approval decision on the caller's behalf --
    it prints the structured result and leaves review/approval to a human.

    The JSON result is written to stdout ONLY (nothing else on stdout), so
    the output can be piped directly into another stage (e.g. an n8n
    "Execute Command" node, or `github_action`'s CLI below) without extra
    parsing. Human-facing notices go to stderr instead.
    """
    args = _build_arg_parser().parse_args(argv)

    try:
        result = run_analysis(
            feedback_file=args.feedback_file,
            context_files=args.context_files,
            analysis_query=args.analysis_query,
            feedback_top_k=args.feedback_top_k,
            context_top_k=args.context_top_k,
            diverse_sample_size=args.diverse_sample_size,
            cache_dir=args.cache_dir,
            chroma_path=args.chroma_path,
        )
    except OrchestrationError as exc:
        print(f"Analysis could not be completed: {exc}", file=sys.stderr)
        return 1

    print(result.model_dump_json(indent=2))
    print(
        "HUMAN REVIEW REQUIRED: no GitHub issue or other external action "
        "has been taken. Review the opportunities above and use the "
        "separate, explicitly human-approved action step to act on any of "
        "them.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
