"""Gemini analyst: turns one AnalysisContextPacket into a structured opportunity draft.

Design notes:
- This module makes AT MOST ONE model request per run_analyst() call. There is
  no retry loop here; the caller (a future bounded workflow) controls whether
  and how additional requests happen, per the project's strict Gemini request
  budget (see docs/decisions.md D005).
- The real Gemini client (GeminiAnalystClient) is lazy in every sense that
  matters for tests: importing this module never touches `google.genai`,
  never reads GEMINI_API_KEY, and never talks to the network. The API key is
  read, and `google.genai.Client` constructed, only inside
  GeminiAnalystClient.generate_structured — i.e. only when a real request is
  actually about to happen. Ordinary pytest runs stay fully offline by always
  injecting a fake `client` into run_analyst().
- The analyst proposes; it does not decide. It must not invent
  evidence_count or opportunity_score — those are computed deterministically
  in a later validation/scoring milestone, not by the model.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from product_feedback_agent.analysis_context import AnalysisContextPacket

MODEL_NAME = "gemini-3.7-flash"
GEMINI_API_KEY_ENV = "GEMINI_API_KEY"
DEFAULT_LOG_PATH = "logs/model-calls.jsonl"


class AnalystError(Exception):
    """Raised when the analyst request cannot be made or its output is unusable."""


# ---------------------------------------------------------------------------
# Analyst output schema
# ---------------------------------------------------------------------------


class EvidenceQuote(BaseModel):
    """One exact excerpt from a supplied feedback record, cited by ID."""

    model_config = ConfigDict(extra="forbid")

    feedback_id: str
    excerpt: str


class ContextCitation(BaseModel):
    """A citation into one section of a supplied product-context document."""

    model_config = ConfigDict(extra="forbid")

    document_id: str
    section_id: str


class ScoreComponentProposal(BaseModel):
    """One recommended (not final) score component, with its rationale.

    Final scores are computed deterministically in a later milestone; this is
    only the analyst's input to that process.
    """

    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=1, le=5)
    rationale: str


class OpportunityDraft(BaseModel):
    """One analyst-proposed product opportunity, grounded in supplied evidence."""

    model_config = ConfigDict(extra="forbid")

    opportunity_id: str
    title: str
    problem_statement: str
    underlying_need: str
    affected_segments: list[str]
    feedback_ids: list[str]
    representative_evidence: list[EvidenceQuote]
    related_context: list[ContextCitation]

    evidence_strength: ScoreComponentProposal
    severity: ScoreComponentProposal
    segment_breadth: ScoreComponentProposal
    strategic_alignment: ScoreComponentProposal
    confidence: ScoreComponentProposal

    roadmap_status: str
    contradicting_evidence: list[str]
    evidence_gaps: list[str]
    discovery_questions: list[str]
    recommended_next_step: str


class AnalystOutput(BaseModel):
    """The analyst's complete draft: exactly three distinct opportunities.

    NOTE: deliberately has no evidence_count or opportunity_score fields.
    Those are computed locally, deterministically, from validated evidence
    IDs in a later milestone -- the model must not invent them.
    """

    model_config = ConfigDict(extra="forbid")

    opportunities: list[OpportunityDraft] = Field(min_length=3, max_length=3)


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_ANALYST_INSTRUCTIONS = """You are the analyst stage of a product feedback analysis pipeline.

You are given one evidence packet (JSON) below, built deterministically by a
prior retrieval step. It contains: retrieved feedback, retrieved product-context
sections, a diverse feedback sample, and dataset statistics.

Follow these rules exactly:

1. Use ONLY evidence contained in the supplied packet below. Do not use
   outside knowledge, assumptions about the product, or general industry
   knowledge that is not present in the packet.
2. Produce EXACTLY three distinct product opportunities. Merge duplicate or
   overlapping themes into one opportunity rather than repeating them.
3. Describe underlying problems and user needs, not predetermined features
   or solutions. Where feedback requests a specific solution, explicitly
   distinguish that requested solution from the underlying problem/need it is
   trying to solve.
4. Never invent feedback IDs. Every feedback_id you cite must be one of the
   feedback_id values that literally appears in the packet below.
5. Never invent document IDs or section IDs. Every document_id/section_id you
   cite must literally appear in the packet's retrieved_context below.
6. Representative excerpts (representative_evidence) must be exact,
   verbatim quotations copied from the supplied feedback text -- not
   paraphrases or summaries.
7. Affected segments must be supported by the segment/product_area/source
   metadata actually present in the supplied feedback evidence.
8. Any roadmap or strategy claim (roadmap_status) must be grounded in, and
   should reference, the supplied product-context sections. If the packet
   does not address roadmap/strategy for an opportunity, say so plainly
   instead of guessing.
9. Consider the diverse_feedback_sample as well as the top retrieved
   feedback -- it exists specifically to surface different signals that a
   single top-ranked search might miss.
10. Score component proposals (evidence_strength, severity, segment_breadth,
    strategic_alignment, confidence) are recommendations for a later
    deterministic validation/scoring step, not final decisions. Do not claim
    that the highest-scoring opportunity is the one that must be built --
    prioritization is a human decision.
11. Give exactly one concise rationale sentence for each score component,
    explaining why you chose that score given the supplied evidence.
12. When evidence for an opportunity is incomplete or thin, prefer
    discovery/research actions (e.g. user interviews, targeted surveys) as
    the recommended_next_step, rather than recommending a specific feature
    or solution be built.
13. Do NOT include an evidence_count or opportunity_score field -- those are
    computed separately, deterministically, from your feedback_ids.

Respond with JSON matching the supplied schema exactly. Do not include any
text outside the JSON object.
"""


def build_analyst_prompt(packet: AnalysisContextPacket) -> str:
    """Build the analyst prompt from a deterministic AnalysisContextPacket.

    The packet is serialized as JSON (preserving feedback_id, document_id,
    section_id, and verbatim text/excerpts) so the model has a single,
    unambiguous, machine-readable source of truth to ground its answer in.
    Retrieval has already happened; the model is not asked to retrieve
    anything itself.
    """
    packet_json = packet.model_dump_json(indent=2)
    return f"{_ANALYST_INSTRUCTIONS}\nEvidence packet (JSON):\n{packet_json}\n"


# ---------------------------------------------------------------------------
# Analyst client boundary
# ---------------------------------------------------------------------------


class AnalystClient(Protocol):
    """A model client capable of one structured-JSON analyst request.

    Tests inject a fake implementation; production uses GeminiAnalystClient.
    """

    def generate_structured(
        self, *, prompt: str, response_schema: dict[str, Any], cache_key: str
    ) -> str:
        """Return raw JSON text intended to validate against response_schema."""
        ...


def run_analyst(
    packet: AnalysisContextPacket,
    client: AnalystClient | None = None,
) -> AnalystOutput:
    """Run the analyst once against `packet`, returning a validated AnalystOutput.

    Makes at most one request to `client` (or a lazily-constructed
    GeminiAnalystClient if none is injected). There is no retry loop: a
    request failure or invalid output raises AnalystError immediately.
    """
    if client is None:
        client = GeminiAnalystClient()

    prompt = build_analyst_prompt(packet)
    response_schema = AnalystOutput.model_json_schema()

    raw_output = client.generate_structured(
        prompt=prompt, response_schema=response_schema, cache_key=packet.cache_key
    )

    if raw_output is None or not raw_output.strip():
        raise AnalystError("Analyst returned empty output.")

    try:
        data = json.loads(raw_output)
    except json.JSONDecodeError as exc:
        raise AnalystError(f"Analyst output is not valid JSON: {exc}") from exc

    try:
        return AnalystOutput.model_validate(data)
    except ValidationError as exc:
        raise AnalystError(f"Analyst output failed schema validation: {exc}") from exc


# ---------------------------------------------------------------------------
# Real Gemini adapter
# ---------------------------------------------------------------------------


class GeminiAnalystClient:
    """Real AnalystClient backed by the Google Gen AI SDK (`google-genai`).

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
            raise AnalystError(
                f"{GEMINI_API_KEY_ENV} is not set; cannot call the real Gemini analyst client."
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
            raise AnalystError(f"Gemini request failed: {type(exc).__name__}") from exc

        output_text = getattr(response, "output_text", None)
        if not output_text:
            self._trace(
                cache_key=cache_key,
                success=False,
                duration=time.monotonic() - start,
                error_type="EmptyOutputError",
            )
            raise AnalystError("Gemini returned empty output.")

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
            "stage": "analyst",
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
