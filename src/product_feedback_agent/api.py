"""Local HTTP boundary: n8n (Docker) -> this Windows-host Python service.

Design notes (Stage 15 repair -- see the 2026-09-07 live n8n finding in
docs/agent-progress.md and docs/decisions.md):
- The self-hosted n8n instance runs inside Docker
  (docker.n8n.io/n8nio/n8n), while this repository/.venv runs on the
  Windows host. n8n's Execute Command node cannot run this project's CLI
  in-place -- a command executed there runs *inside the n8n container*,
  not against the Windows host's .venv, and the node itself was not even
  installed/enabled on the live instance. Docker Desktop containers can
  reach the host at `host.docker.internal`, so this module exposes the
  existing Stage 13/14 Python functions over a small local HTTP service
  instead of shelling out.
- This module contains NO analysis or GitHub business logic of its own.
  Each endpoint only parses/validates an HTTP request into the same
  explicit arguments `run_analysis` / `create_opportunity_issue` already
  take, calls the existing function unmodified, and serializes the
  existing Pydantic result model back out as the response body. Every
  safety control already enforced by those functions -- approved=True
  required for mutation, dry-run default-safe, the repo allow-list,
  duplicate-marker prevention -- is preserved unchanged, because this
  layer never reimplements them, it only forwards to the real function.
- `/analyze` never imports or calls anything GitHub-related; `/github/issues`
  never re-derives or re-analyzes an opportunity -- it only acts on an
  `OrchestrationResult` the caller already has (typically from `/analyze`).
- Built on Starlette + uvicorn. Both are already transitive dependencies of
  this project (`mcp[cli]` requires starlette; `mcp[cli]`/`chromadb` require
  uvicorn) -- no new third-party dependency was introduced for this module.
- feedback_file/context_files are resolved relative to the project root
  (derived from this file's location), never the process's current working
  directory -- mirroring the same convention `mcp_server.py` already uses,
  so the service behaves the same regardless of the directory `uvicorn` was
  launched from.
- Binds to 0.0.0.0 by default (configurable via PFA_API_HOST/PFA_API_PORT)
  because Docker Desktop's `host.docker.internal` cannot reach a service
  bound only to 127.0.0.1. This service is intended for local development
  use only -- it has no authentication of its own.
- No secret (GITHUB_TOKEN, GEMINI_API_KEY) is ever read, logged, or returned
  by this module; those remain read lazily inside github_action.py /
  analyst.py / critic.py exactly as before.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from product_feedback_agent.github_action import (
    GitHubActionError,
    create_opportunity_issue,
)
from product_feedback_agent.orchestrator import (
    OrchestrationError,
    OrchestrationResult,
    run_analysis,
)

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000


def _project_root() -> Path:
    """The repository root, derived from this file's location (not cwd)."""
    return Path(__file__).resolve().parents[2]


def _resolve_path(project_root: Path, raw_path: str) -> str:
    """Resolve `raw_path` (absolute, or relative to `project_root`) to an
    absolute path string, so `run_analysis` behaves the same regardless of
    the directory the API process was launched from."""
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = project_root / candidate
    return str(candidate)


class ErrorResponse(BaseModel):
    """The body returned for every refused/invalid HTTP request."""

    model_config = ConfigDict(extra="forbid")

    detail: str


class AnalyzeRequest(BaseModel):
    """Explicit HTTP-layer mirror of `run_analysis`'s arguments.

    `extra="forbid"` and required `feedback_file`/`analysis_query` reject a
    malformed or incomplete request before `run_analysis` is ever called.
    """

    model_config = ConfigDict(extra="forbid")

    feedback_file: str
    context_files: list[str] = Field(default_factory=list)
    analysis_query: str
    feedback_top_k: int = 10
    context_top_k: int = 5
    diverse_sample_size: int = 5


class GitHubIssueRequest(BaseModel):
    """Explicit HTTP-layer mirror of `create_opportunity_issue`'s arguments.

    `result` is a full `OrchestrationResult` (e.g. `/analyze`'s response
    body) the caller already produced -- this endpoint never re-derives or
    re-analyzes anything, it only renders/creates one opportunity's issue
    from a result the caller supplies. `approved` defaults to False and
    `dry_run` defaults to True, matching `create_opportunity_issue` exactly.
    """

    model_config = ConfigDict(extra="forbid")

    result: OrchestrationResult
    opportunity_id: str
    repo: str
    approved: bool = False
    dry_run: bool = True
    allowed_repos: list[str] | None = None


async def _read_json_body(request: Request) -> tuple[dict | None, JSONResponse | None]:
    try:
        return await request.json(), None
    except json.JSONDecodeError:
        return None, JSONResponse(
            ErrorResponse(detail="Request body is not valid JSON.").model_dump(), status_code=400
        )


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


async def analyze(request: Request) -> JSONResponse:
    raw, error = await _read_json_body(request)
    if error is not None:
        return error

    try:
        payload = AnalyzeRequest.model_validate(raw)
    except ValidationError as exc:
        return JSONResponse(ErrorResponse(detail=str(exc)).model_dump(), status_code=422)

    project_root = _project_root()
    feedback_file = _resolve_path(project_root, payload.feedback_file)
    context_files = [_resolve_path(project_root, path) for path in payload.context_files]

    try:
        result = run_analysis(
            feedback_file=feedback_file,
            context_files=context_files,
            analysis_query=payload.analysis_query,
            feedback_top_k=payload.feedback_top_k,
            context_top_k=payload.context_top_k,
            diverse_sample_size=payload.diverse_sample_size,
        )
    except OrchestrationError as exc:
        return JSONResponse(ErrorResponse(detail=str(exc)).model_dump(), status_code=400)
    except OSError as exc:
        return JSONResponse(ErrorResponse(detail=f"Could not read input file: {exc}").model_dump(), status_code=400)

    return JSONResponse(json.loads(result.model_dump_json()))


async def github_issues(request: Request) -> JSONResponse:
    raw, error = await _read_json_body(request)
    if error is not None:
        return error

    try:
        payload = GitHubIssueRequest.model_validate(raw)
    except ValidationError as exc:
        return JSONResponse(ErrorResponse(detail=str(exc)).model_dump(), status_code=422)

    allowed_repos = frozenset(payload.allowed_repos) if payload.allowed_repos is not None else None

    try:
        result = create_opportunity_issue(
            payload.result,
            payload.opportunity_id,
            payload.repo,
            approved=payload.approved,
            dry_run=payload.dry_run,
            allowed_repos=allowed_repos,
        )
    except GitHubActionError as exc:
        return JSONResponse(ErrorResponse(detail=str(exc)).model_dump(), status_code=400)

    return JSONResponse(json.loads(result.model_dump_json()))


def create_app() -> Starlette:
    """Build the Starlette app. A fresh instance per call keeps this
    importable and testable with no module-level side effects."""
    return Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/analyze", analyze, methods=["POST"]),
            Route("/github/issues", github_issues, methods=["POST"]),
        ]
    )


app = create_app()


def main(argv: Sequence[str] | None = None) -> int:
    """Run the local HTTP service with uvicorn.

    Host/port are read from PFA_API_HOST/PFA_API_PORT -- never hard-coded to
    a machine-specific address. Defaults to 0.0.0.0:8000 so a Docker
    Desktop n8n container can reach it via host.docker.internal:8000.
    """
    import uvicorn

    host = os.environ.get("PFA_API_HOST", DEFAULT_HOST)
    port = int(os.environ.get("PFA_API_PORT", str(DEFAULT_PORT)))
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
