"""Bounded analyst -> validator -> critic -> optional single revision workflow.

Design notes (see docs/decisions.md D004/D005):
- This module makes AT MOST 3 model requests total, ever: one analyst
  request, one critic request, and -- only if the critic returns
  status="revise" -- exactly one revision request. There is no loop and no
  path that can reach a 4th request.
- The critic is called exactly once. Per the locked V1 architecture, a
  revised draft is re-validated deterministically but is NOT sent back to
  the critic again. Any deterministic issues that remain after the single
  revision are surfaced in the final ValidationResult for human review, not
  hidden and not chased with another model call.
- The revision step reuses AnalystOutput/OpportunityDraft as its output
  schema (still exactly three opportunities, same evidence-grounding
  rules) rather than inventing a parallel schema.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from product_feedback_agent.analysis_context import AnalysisContextPacket
from product_feedback_agent.analyst import (
    AnalystClient,
    AnalystError,
    AnalystOutput,
    run_analyst,
)
from product_feedback_agent.critic import (
    CriticClient,
    CriticOutput,
    CriticStatus,
    run_critic,
)
from product_feedback_agent.validation import ValidationResult, validate_analyst_output

MODEL_NAME = "gemini-3.7-flash"
GEMINI_API_KEY_ENV = "GEMINI_API_KEY"
DEFAULT_LOG_PATH = "logs/model-calls.jsonl"

MAX_PASSING_RUN_REQUESTS = 2
MAX_REVISION_RUN_REQUESTS = 3


class RevisionError(Exception):
    """Raised when the revision request cannot be made or its output is unusable."""


class PipelineResult(BaseModel):
    """The complete outcome of one bounded analyst/validator/critic run."""

    model_config = ConfigDict(extra="forbid")

    analyst_output: AnalystOutput
    validation_result: ValidationResult
    critic_output: CriticOutput
    revised: bool
    model_request_count: int


# ---------------------------------------------------------------------------
# Revision prompt construction
# ---------------------------------------------------------------------------

_REVISION_INSTRUCTIONS = """You are the revision stage of a product feedback analysis pipeline.

A prior analyst draft (AnalystOutput, JSON below) was reviewed by deterministic
code (ValidationResult, JSON below) and by a critic (CriticOutput, JSON
below), against the same evidence packet (JSON below) the original analyst
saw. The critic returned status="revise" with the required_revisions listed
in its output.

Produce a corrected, complete replacement set of EXACTLY three opportunities
that:

1. Fixes every problem named in required_revisions and in the deterministic
   validation issues -- merge duplicates, remove or correct unsupported
   affected_segments (including any product_area value miscast as a
   segment), correct or remove invalid citations, and fix non-exact
   quotations.
2. Still follows every original evidence rule: use ONLY evidence in the
   supplied packet, never invent feedback IDs or document/section IDs,
   representative excerpts must be exact verbatim substrings of the cited
   feedback text, affected_segments must be supported by the segment
   metadata of feedback you actually cite, and score component proposals
   are 1-5 recommendations for deterministic scoring, not final decisions.
3. Does NOT include an evidence_count or opportunity_score field -- those
   are computed separately, deterministically.

This is the ONLY revision request that will be made. Return your best
complete corrected answer now.

Respond with JSON matching the supplied schema exactly (the same schema as
the original analyst draft). Do not include any text outside the JSON
object.
"""


def build_revision_prompt(
    packet: AnalysisContextPacket,
    analyst_output: AnalystOutput,
    validation_result: ValidationResult,
    critic_output: CriticOutput,
) -> str:
    """Build the one-shot revision prompt from the packet and every prior stage's output."""
    return (
        f"{_REVISION_INSTRUCTIONS}\n"
        f"Evidence packet (JSON):\n{packet.model_dump_json(indent=2)}\n\n"
        f"Original analyst draft (JSON):\n{analyst_output.model_dump_json(indent=2)}\n\n"
        f"Deterministic validation findings (JSON):\n{validation_result.model_dump_json(indent=2)}\n\n"
        f"Critic findings (JSON):\n{critic_output.model_dump_json(indent=2)}\n"
    )


# ---------------------------------------------------------------------------
# Revision client boundary
# ---------------------------------------------------------------------------

# Structurally identical to AnalystClient/CriticClient: one structured-JSON
# request, returning raw JSON text. Reused as the type for run_revision's
# `client` parameter rather than declaring a duplicate Protocol.
RevisionClient = AnalystClient


def run_revision(
    packet: AnalysisContextPacket,
    analyst_output: AnalystOutput,
    validation_result: ValidationResult,
    critic_output: CriticOutput,
    client: RevisionClient | None = None,
) -> AnalystOutput:
    """Make the single, bounded revision request, returning a validated AnalystOutput.

    Makes at most one request to `client` (or a lazily-constructed
    GeminiRevisionClient if none is injected). There is no retry loop.
    """
    if client is None:
        client = GeminiRevisionClient()

    prompt = build_revision_prompt(packet, analyst_output, validation_result, critic_output)
    response_schema = AnalystOutput.model_json_schema()

    raw_output = client.generate_structured(
        prompt=prompt, response_schema=response_schema, cache_key=packet.cache_key
    )

    if raw_output is None or not raw_output.strip():
        raise RevisionError("Revision returned empty output.")

    try:
        data = json.loads(raw_output)
    except json.JSONDecodeError as exc:
        raise RevisionError(f"Revision output is not valid JSON: {exc}") from exc

    try:
        return AnalystOutput.model_validate(data)
    except ValidationError as exc:
        raise RevisionError(f"Revision output failed schema validation: {exc}") from exc


# ---------------------------------------------------------------------------
# Real Gemini adapter
# ---------------------------------------------------------------------------


class GeminiRevisionClient:
    """Real RevisionClient backed by the Google Gen AI SDK (`google-genai`).

    Every attribute that could touch the network or secrets is resolved
    lazily inside generate_structured, never in __init__ or at import time.
    """

    def __init__(self, log_path: str | Path | None = None) -> None:
        self._log_path = Path(log_path) if log_path is not None else _default_log_path()

    def generate_structured(
        self, *, prompt: str, response_schema: dict[str, Any], cache_key: str
    ) -> str:
        start = time.monotonic()

        api_key = os.environ.get(GEMINI_API_KEY_ENV)
        if not api_key:
            self._trace(
                cache_key=cache_key,
                success=False,
                duration=time.monotonic() - start,
                error_type="MissingAPIKeyError",
            )
            raise RevisionError(
                f"{GEMINI_API_KEY_ENV} is not set; cannot call the real Gemini revision client."
            )

        # Imported here (not at module scope) so importing this module, and
        # running the offline test suite, never requires google-genai to be
        # installed or reachable.
        from google import genai

        try:
            gemini_client = genai.Client(api_key=api_key)
            response = gemini_client.interactions.create(
                model=MODEL_NAME,
                input=prompt,
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": response_schema,
                },
            )
        except Exception as exc:
            # Never include str(exc) here: SDK exceptions can echo request
            # details, and we must not risk leaking the API key into logs.
            self._trace(
                cache_key=cache_key,
                success=False,
                duration=time.monotonic() - start,
                error_type=type(exc).__name__,
            )
            raise RevisionError(f"Gemini request failed: {type(exc).__name__}") from exc

        output_text = getattr(response, "output_text", None)
        if not output_text:
            self._trace(
                cache_key=cache_key,
                success=False,
                duration=time.monotonic() - start,
                error_type="EmptyOutputError",
            )
            raise RevisionError("Gemini returned empty output.")

        self._trace(
            cache_key=cache_key,
            success=True,
            duration=time.monotonic() - start,
            usage=getattr(response, "usage", None),
        )
        return output_text

    def _trace(
        self,
        *,
        cache_key: str,
        success: bool,
        duration: float,
        error_type: str | None = None,
        usage: Any = None,
    ) -> None:
        record: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "stage": "revision",
            "model": MODEL_NAME,
            "cache_key": cache_key,
            "success": success,
            "duration_seconds": round(duration, 3),
        }
        if error_type is not None:
            record["error_type"] = error_type

        usage_payload = _extract_usage(usage)
        if usage_payload is not None:
            record["usage"] = usage_payload

        _append_trace(self._log_path, record)


def _default_log_path() -> Path:
    project_root = Path(__file__).resolve().parents[2]
    return project_root / DEFAULT_LOG_PATH


def _extract_usage(usage: Any) -> dict[str, Any] | None:
    """Best-effort extraction of token/usage info. Omit rather than guess."""
    if usage is None:
        return None
    if isinstance(usage, dict):
        return usage
    for attr in ("model_dump", "to_dict"):
        method = getattr(usage, attr, None)
        if callable(method):
            try:
                return method()
            except Exception:
                return None
    if hasattr(usage, "__dict__"):
        return dict(vars(usage))
    return None


def _append_trace(log_path: Path, record: dict[str, Any]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


# ---------------------------------------------------------------------------
# Bounded orchestration
# ---------------------------------------------------------------------------


def run_pipeline(
    packet: AnalysisContextPacket,
    *,
    analyst_client: AnalystClient | None = None,
    critic_client: CriticClient | None = None,
    revision_client: RevisionClient | None = None,
) -> PipelineResult:
    """Run the bounded Group 1 workflow: analyst -> validator -> critic -> optional revision.

    Request budget (see docs/decisions.md D005):
    - Passing run (critic status == "pass"): exactly 2 model requests
      (analyst + critic). No revision call is made.
    - Revision run (critic status == "revise"): exactly 3 model requests
      (analyst + critic + one revision). The critic is never called a
      second time; the revised output is re-validated deterministically and
      returned as-is, even if deterministic issues remain.

    There is no code path in this function capable of making a 4th request.
    """
    analyst_output = run_analyst(packet, client=analyst_client)
    validation_result = validate_analyst_output(analyst_output, packet)
    critic_output = run_critic(packet, analyst_output, validation_result, client=critic_client)

    if critic_output.status != CriticStatus.REVISE:
        return PipelineResult(
            analyst_output=analyst_output,
            validation_result=validation_result,
            critic_output=critic_output,
            revised=False,
            model_request_count=MAX_PASSING_RUN_REQUESTS,
        )

    revised_output = run_revision(
        packet, analyst_output, validation_result, critic_output, client=revision_client
    )
    revised_validation_result = validate_analyst_output(revised_output, packet)

    return PipelineResult(
        analyst_output=revised_output,
        validation_result=revised_validation_result,
        critic_output=critic_output,
        revised=True,
        model_request_count=MAX_REVISION_RUN_REQUESTS,
    )
