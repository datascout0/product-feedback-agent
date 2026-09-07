"""Gemini critic: reviews one AnalystOutput against deterministic validation findings.

Design notes:
- Mirrors analyst.py's engineering principles exactly: this module makes AT
  MOST ONE model request per run_critic() call, has no retry loop, and the
  real Gemini client (GeminiCriticClient) is lazy in every sense that
  matters for tests -- importing this module never touches `google.genai`,
  never reads GEMINI_API_KEY, and never talks to the network. Ordinary
  pytest runs stay fully offline by always injecting a fake `client` into
  run_critic().
- The critic reviews; it does not decide. It cannot change the roadmap,
  create GitHub issues, change permissions, or take any autonomous action --
  it can only report structured findings for a human (and, per D004, at most
  one bounded revision pass) to act on.
- The critic receives the packet, the analyst's draft, AND the Stage 10
  deterministic ValidationResult, so it is reviewing on top of (not
  instead of) code-verified evidence checks -- it is asked to catch things
  the deterministic checks structurally cannot, such as rationale quality,
  duplicate opportunities, ignored contradictions, and overreach.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from product_feedback_agent.analysis_context import AnalysisContextPacket
from product_feedback_agent.analyst import AnalystOutput
from product_feedback_agent.gemini_model import resolve_gemini_model
from product_feedback_agent.validation import ValidationResult

GEMINI_API_KEY_ENV = "GEMINI_API_KEY"
DEFAULT_LOG_PATH = "logs/model-calls.jsonl"


class CriticError(Exception):
    """Raised when the critic request cannot be made or its output is unusable."""


# ---------------------------------------------------------------------------
# Critic output schema
# ---------------------------------------------------------------------------


class CriticStatus(str, Enum):
    PASS = "pass"
    REVISE = "revise"


class CriticIssueType(str, Enum):
    UNSUPPORTED_CLAIM = "unsupported_claim"
    INVALID_CITATION = "invalid_citation"
    EXACT_QUOTATION_PROBLEM = "exact_quotation_problem"
    UNSUPPORTED_SEGMENT = "unsupported_segment"
    DUPLICATE_OPPORTUNITY = "duplicate_opportunity"
    IGNORED_CONTRADICTION = "ignored_contradiction"
    ROADMAP_OVERREACH = "roadmap_overreach"
    WEAK_SCORE_RATIONALE = "weak_score_rationale"
    FEATURE_VS_NEED_CONFUSION = "feature_vs_need_confusion"
    CONFIDENCE_OVERSTATEMENT = "confidence_overstatement"
    NEXT_STEP_MISMATCH = "next_step_mismatch"


class CriticIssue(BaseModel):
    """One structured critic finding about a specific opportunity."""

    model_config = ConfigDict(extra="forbid")

    opportunity_id: str
    issue_type: CriticIssueType
    message: str


class CriticOutput(BaseModel):
    """The critic's complete structured review of one AnalystOutput.

    NOTE: the critic can only report findings. Nothing in this schema lets
    it choose a roadmap, create a GitHub issue, change permissions, or take
    any other autonomous action -- see docs/decisions.md D002.
    """

    model_config = ConfigDict(extra="forbid")

    status: CriticStatus
    issues: list[CriticIssue] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    citation_errors: list[str] = Field(default_factory=list)
    duplicate_opportunities: list[str] = Field(default_factory=list)
    required_revisions: list[str] = Field(default_factory=list)


def derive_critic_status(required_revisions: list[str]) -> CriticStatus:
    """Deterministically derive CriticStatus from required_revisions.

    non-empty required_revisions => REVISE, empty => PASS. This is the only
    place CriticStatus is decided -- it is never trusted from the model (see
    the wire schema section below).
    """
    return CriticStatus.REVISE if required_revisions else CriticStatus.PASS


# ---------------------------------------------------------------------------
# Wire (structured-output) schema.
#
# Background (see docs/agent-progress.md Stage 12b/12c and the third live
# failure this fix addresses, in docs/results-log.md 2026-09-07 Stage 15):
# the real critic THREE times returned status="revise" with
# required_revisions=[] -- first rejected by a prompt-only rule, then by a
# pass/revise anyOf wire union (CriticPassResponse/CriticReviseResponse)
# whose per-branch minItems/maxItems tied required_revisions' length to a
# model-produced status field. All three attempts still let Gemini produce
# a `status` value that could disagree with `required_revisions`, and the
# union's own cross-branch invariant is itself invisible in a flat JSON
# Schema for any client that flattens anyOf (as the live n8n/Gemini failure
# proved again).
#
# The fix removes `status` from the wire contract entirely rather than
# constraining it further: CriticWireResponse below asks Gemini for its
# review findings ONLY, with `required_revisions` as an always-present
# (possibly empty) array. Python derives CriticStatus deterministically
# from required_revisions (see derive_critic_status above) -- there is no
# model-produced field left to disagree with it.
# ---------------------------------------------------------------------------


class CriticWireResponse(BaseModel):
    """The structured-output schema actually sent to (and parsed from) Gemini.

    Deliberately contains NO status field: status is derived locally by
    derive_critic_status(required_revisions), never trusted from the model.
    required_revisions has no default, so it is always present in
    model_json_schema()'s "required" list -- it may still be an empty list.
    """

    model_config = ConfigDict(extra="forbid")

    issues: list[CriticIssue] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    citation_errors: list[str] = Field(default_factory=list)
    duplicate_opportunities: list[str] = Field(default_factory=list)
    required_revisions: list[str]


def build_critic_response_schema() -> dict[str, Any]:
    """Return the structured-output JSON schema actually sent to the critic model.

    One flat object schema (no anyOf/oneOf, no status field). required_revisions
    is a required array[string] that may be empty.
    """
    return CriticWireResponse.model_json_schema()


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_CRITIC_INSTRUCTIONS = """You are the critic stage of a product feedback analysis pipeline.

You are given three JSON objects below:
1. The original evidence packet supplied to the analyst.
2. The analyst's draft opportunities (AnalystOutput).
3. The deterministic validation findings and scores already computed by code
   (ValidationResult) -- these checks are authoritative and have already
   caught unsupported feedback IDs, non-exact quotations, invalid context
   citations, and unsupported affected segments where detectable by exact
   comparison against the evidence packet.

Your job is to catch what deterministic string/ID comparison cannot, by
reviewing the analyst's reasoning quality against the actual evidence:

1. Whether the cited evidence actually SUPPORTS each opportunity's claimed
   problem, underlying need, and severity -- not just whether citations are
   structurally valid.
2. Any invalid or invented citations the deterministic checks may have
   missed.
3. Exact-quotation problems (paraphrase presented as a verbatim quote).
4. Unsupported affected_segments claims, including any product_area value
   used as if it were a customer segment.
5. Duplicate or overlapping opportunities that should have been merged into
   one.
6. Contradicting evidence that the analyst listed but then ignored in its
   problem statement, severity, or confidence.
7. Roadmap or strategy overreach: any claim about roadmap/strategy that goes
   beyond what the supplied product-context sections actually say, or that
   effectively decides prioritization rather than describing evidence.
8. Whether each score-component rationale is a real, evidence-grounded
   justification rather than a generic or circular restatement of the score.
9. Feature-request vs underlying-need confusion: whether the opportunity
   actually distinguishes the user's underlying need from a specific
   requested solution, where feedback requested one.
10. Confidence overstatement relative to the amount and quality of evidence
    actually supplied.
11. Whether recommended_next_step matches the evidence_gaps -- thin evidence
    should lead to a discovery/research next step, not a build decision.

You do not decide the roadmap, create any external record (e.g. a GitHub issue),
change any permission, or take any action beyond returning this structured
review. You only report findings.

required_revisions rules (follow these exactly). There is no separate
status field in your response -- required_revisions IS your verdict:

- required_revisions MUST always be present in your response, as an array
  of strings. It is valid, and expected, for it to be an empty array `[]`.
- Return an empty required_revisions array ONLY when the analyst output
  requires no changes at all -- none of the 11 checks above found anything
  that would materially mislead someone deciding whether to act on this
  opportunity.
- Return one or more concrete, actionable strings in required_revisions
  whenever the analyst output requires ANY change, including a
  deterministic validation finding above that requires an analyst-side
  correction -- for example an unsupported affected_segments value
  (including a product_area value used as if it were a customer segment),
  an invalid feedback/context ID, or a non-exact quotation. Those
  deterministic findings are evidence you must act on, not skip because
  code already flagged them.
- Each entry must be a concrete, actionable revision instruction (e.g.
  "merge opp_2 into opp_1", "remove 'mobile' from affected_segments for
  opp_2 since it is a product_area, not a customer segment").
- Every issue you list in `issues` that requires changing the analyst output
  must have a corresponding actionable entry in required_revisions -- do not
  report a problem without also giving the instruction to fix it.
- If you cannot name at least one concrete, actionable revision instruction,
  that means nothing actually requires changing the analyst output, so
  return an empty required_revisions array instead of inventing one.

Respond with JSON matching the supplied schema exactly. Do not include any
text outside the JSON object.
"""


def build_critic_prompt(
    packet: AnalysisContextPacket,
    analyst_output: AnalystOutput,
    validation_result: ValidationResult,
) -> str:
    """Build the critic prompt from the packet, analyst draft, and deterministic findings."""
    packet_json = packet.model_dump_json(indent=2)
    analyst_json = analyst_output.model_dump_json(indent=2)
    validation_json = validation_result.model_dump_json(indent=2)
    return (
        f"{_CRITIC_INSTRUCTIONS}\n"
        f"Evidence packet (JSON):\n{packet_json}\n\n"
        f"Analyst draft (JSON):\n{analyst_json}\n\n"
        f"Deterministic validation findings (JSON):\n{validation_json}\n"
    )


# ---------------------------------------------------------------------------
# Critic client boundary
# ---------------------------------------------------------------------------


class CriticClient(Protocol):
    """A model client capable of one structured-JSON critic request.

    Tests inject a fake implementation; production uses GeminiCriticClient.
    """

    def generate_structured(
        self, *, prompt: str, response_schema: dict[str, Any], cache_key: str
    ) -> str:
        """Return raw JSON text intended to validate against response_schema."""
        ...


def run_critic(
    packet: AnalysisContextPacket,
    analyst_output: AnalystOutput,
    validation_result: ValidationResult,
    client: CriticClient | None = None,
) -> CriticOutput:
    """Run the critic once, returning a validated CriticOutput.

    Makes at most one request to `client` (or a lazily-constructed
    GeminiCriticClient if none is injected). There is no retry loop: a
    request failure or invalid output raises CriticError immediately.
    """
    if client is None:
        client = GeminiCriticClient()

    prompt = build_critic_prompt(packet, analyst_output, validation_result)
    response_schema = build_critic_response_schema()

    raw_output = client.generate_structured(
        prompt=prompt, response_schema=response_schema, cache_key=packet.cache_key
    )

    if raw_output is None or not raw_output.strip():
        raise CriticError("Critic returned empty output.")

    try:
        data = json.loads(raw_output)
    except json.JSONDecodeError as exc:
        raise CriticError(f"Critic output is not valid JSON: {exc}") from exc

    try:
        wire_output = CriticWireResponse.model_validate(data)
    except ValidationError as exc:
        raise CriticError(f"Critic output failed schema validation: {exc}") from exc

    # status is never trusted from the model: it is derived deterministically
    # from required_revisions, then combined with the wire findings to build
    # the domain CriticOutput the rest of the pipeline uses.
    status = derive_critic_status(wire_output.required_revisions)
    try:
        return CriticOutput(
            status=status,
            issues=wire_output.issues,
            unsupported_claims=wire_output.unsupported_claims,
            citation_errors=wire_output.citation_errors,
            duplicate_opportunities=wire_output.duplicate_opportunities,
            required_revisions=wire_output.required_revisions,
        )
    except ValidationError as exc:
        raise CriticError(f"Critic output failed schema validation: {exc}") from exc


# ---------------------------------------------------------------------------
# Real Gemini adapter
# ---------------------------------------------------------------------------


class GeminiCriticClient:
    """Real CriticClient backed by the Google Gen AI SDK (`google-genai`).

    Every attribute that could touch the network or secrets is resolved
    lazily inside generate_structured, never in __init__ or at import time.
    """

    def __init__(self, log_path: str | Path | None = None) -> None:
        self._log_path = Path(log_path) if log_path is not None else _default_log_path()

    def generate_structured(
        self, *, prompt: str, response_schema: dict[str, Any], cache_key: str
    ) -> str:
        start = time.monotonic()
        model = resolve_gemini_model()

        api_key = os.environ.get(GEMINI_API_KEY_ENV)
        if not api_key:
            self._trace(
                cache_key=cache_key,
                model=model,
                success=False,
                duration=time.monotonic() - start,
                error_type="MissingAPIKeyError",
            )
            raise CriticError(
                f"{GEMINI_API_KEY_ENV} is not set; cannot call the real Gemini critic client."
            )

        # Imported here (not at module scope) so importing this module, and
        # running the offline test suite, never requires google-genai to be
        # installed or reachable.
        from google import genai

        try:
            gemini_client = genai.Client(api_key=api_key)
            response = gemini_client.interactions.create(
                model=model,
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
                model=model,
                success=False,
                duration=time.monotonic() - start,
                error_type=type(exc).__name__,
            )
            raise CriticError(f"Gemini request failed: {type(exc).__name__}") from exc

        output_text = getattr(response, "output_text", None)
        if not output_text:
            self._trace(
                cache_key=cache_key,
                model=model,
                success=False,
                duration=time.monotonic() - start,
                error_type="EmptyOutputError",
            )
            raise CriticError("Gemini returned empty output.")

        self._trace(
            cache_key=cache_key,
            model=model,
            success=True,
            duration=time.monotonic() - start,
            usage=getattr(response, "usage", None),
        )
        return output_text

    def _trace(
        self,
        *,
        cache_key: str,
        model: str,
        success: bool,
        duration: float,
        error_type: str | None = None,
        usage: Any = None,
    ) -> None:
        record: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "stage": "critic",
            "model": model,
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
