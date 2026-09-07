import json
import os
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from product_feedback_agent.analysis_context import (
    AnalysisContextPacket,
    ContextEvidence,
    DatasetStatistics,
    FeedbackEvidence,
    RetrievalParameters,
)
from product_feedback_agent.analyst import AnalystOutput
from product_feedback_agent.critic import (
    CriticClient,
    CriticError,
    CriticIssue,
    CriticIssueType,
    CriticOutput,
    CriticPassResponse,
    CriticReviseResponse,
    CriticStatus,
    CriticWireResponse,
    GeminiCriticClient,
    build_critic_prompt,
    build_critic_response_schema,
    run_critic,
)
from product_feedback_agent.validation import validate_analyst_output

# ---------------------------------------------------------------------------
# Fixtures: reuse the same shape of packet/opportunity as test_analyst.py,
# plus the real ValidationResult computed by Stage 10 so the critic prompt
# reflects the actual bounded pipeline (packet -> analyst -> validator -> critic).
# ---------------------------------------------------------------------------


@pytest.fixture
def packet() -> AnalysisContextPacket:
    return AnalysisContextPacket(
        analysis_query="billing invoice payment",
        dataset_statistics=DatasetStatistics(
            total_feedback_count=2,
            counts_by_source={"survey": 2},
            counts_by_product_area={"billing": 2},
            counts_by_segment={"enterprise": 2},
            count_with_rating=2,
            average_rating=2.5,
        ),
        retrieved_feedback=[
            FeedbackEvidence(
                feedback_id="fb_billing",
                text="The invoice payment process is confusing and billing support is slow.",
                metadata={"source": "survey", "segment": "enterprise", "product_area": "billing"},
                distance=0.1,
            ),
            FeedbackEvidence(
                feedback_id="fb_invoice2",
                text="Invoices are hard to download from the billing portal.",
                metadata={"source": "survey", "segment": "enterprise", "product_area": "billing"},
                distance=0.2,
            ),
        ],
        retrieved_context=[
            ContextEvidence(
                section_id="doc_pricing_sec000",
                document_id="doc_pricing",
                text="Enterprise pricing strategy focuses on annual contracts and volume discounts.",
                metadata={"heading": "Enterprise Pricing"},
                distance=0.15,
            )
        ],
        diverse_feedback_sample=[
            FeedbackEvidence(
                feedback_id="fb_billing",
                text="The invoice payment process is confusing and billing support is slow.",
                metadata={"source": "survey", "segment": "enterprise", "product_area": "billing"},
                distance=0.1,
            )
        ],
        retrieval_parameters=RetrievalParameters(
            feedback_top_k=10, context_top_k=5, diverse_sample_size=5
        ),
        cache_key="fake_cache_key_123",
    )


def _score(score: int = 4) -> dict:
    return {"score": score, "rationale": "Supported by two consistent billing complaints."}


def _opportunity(opportunity_id: str = "opp_1") -> dict:
    return {
        "opportunity_id": opportunity_id,
        "title": "Simplify invoice payment",
        "problem_statement": "Enterprise users find invoice payment confusing.",
        "underlying_need": "Users need a clear, fast way to understand and pay invoices.",
        "affected_segments": ["enterprise"],
        "feedback_ids": ["fb_billing", "fb_invoice2"],
        "representative_evidence": [
            {
                "feedback_id": "fb_billing",
                "excerpt": "The invoice payment process is confusing and billing support is slow.",
            }
        ],
        "related_context": [{"document_id": "doc_pricing", "section_id": "doc_pricing_sec000"}],
        "evidence_strength": _score(),
        "severity": _score(),
        "segment_breadth": _score(2),
        "strategic_alignment": _score(3),
        "confidence": _score(4),
        "roadmap_status": "Not explicitly addressed in supplied pricing strategy section.",
        "contradicting_evidence": [],
        "evidence_gaps": ["No feedback from SMB segment on this theme."],
        "discovery_questions": ["What specifically confuses users about the invoice flow?"],
        "recommended_next_step": "Run 3-5 discovery interviews with enterprise billing contacts.",
    }


@pytest.fixture
def analyst_output() -> AnalystOutput:
    return AnalystOutput.model_validate(
        {"opportunities": [_opportunity("opp_1"), _opportunity("opp_2"), _opportunity("opp_3")]}
    )


@pytest.fixture
def validation_result(packet, analyst_output):
    return validate_analyst_output(analyst_output, packet)


def _pass_payload() -> dict:
    return {
        "status": "pass",
        "issues": [],
        "unsupported_claims": [],
        "citation_errors": [],
        "duplicate_opportunities": [],
        "required_revisions": [],
    }


def _revise_payload() -> dict:
    return {
        "status": "revise",
        "issues": [
            {
                "opportunity_id": "opp_2",
                "issue_type": "duplicate_opportunity",
                "message": "opp_2 restates the same theme as opp_1.",
            }
        ],
        "unsupported_claims": ["opp_2 claims high confidence with only one feedback item."],
        "citation_errors": [],
        "duplicate_opportunities": ["opp_1", "opp_2"],
        "required_revisions": ["Merge opp_2 into opp_1 and reduce its confidence score."],
    }


class FakeCriticClient:
    """Records calls and returns a preset raw JSON string."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    def generate_structured(self, *, prompt: str, response_schema: dict, cache_key: str) -> str:
        self.calls.append({"prompt": prompt, "response_schema": response_schema, "cache_key": cache_key})
        return json.dumps(self.payload)


# ---------------------------------------------------------------------------
# Schema: status/required_revisions constraint, enums
# ---------------------------------------------------------------------------


def test_pass_status_with_no_required_revisions_is_valid():
    output = CriticOutput.model_validate(_pass_payload())
    assert output.status == CriticStatus.PASS
    assert output.required_revisions == []


def test_revise_status_with_required_revisions_is_valid():
    output = CriticOutput.model_validate(_revise_payload())
    assert output.status == CriticStatus.REVISE
    assert len(output.required_revisions) >= 1


def test_revise_status_without_required_revisions_is_rejected():
    payload = _revise_payload()
    payload["required_revisions"] = []
    with pytest.raises(ValidationError):
        CriticOutput.model_validate(payload)


def test_invalid_status_value_is_rejected():
    payload = _pass_payload()
    payload["status"] = "approve"
    with pytest.raises(ValidationError):
        CriticOutput.model_validate(payload)


def test_invalid_issue_type_is_rejected():
    payload = _revise_payload()
    payload["issues"][0]["issue_type"] = "not_a_real_issue_type"
    with pytest.raises(ValidationError):
        CriticOutput.model_validate(payload)


def test_critic_issue_type_enum_covers_required_checks():
    values = {member.value for member in CriticIssueType}
    assert values == {
        "unsupported_claim",
        "invalid_citation",
        "exact_quotation_problem",
        "unsupported_segment",
        "duplicate_opportunity",
        "ignored_contradiction",
        "roadmap_overreach",
        "weak_score_rationale",
        "feature_vs_need_confusion",
        "confidence_overstatement",
        "next_step_mismatch",
    }


def test_critic_output_has_no_roadmap_or_action_fields():
    # The critic must be structurally incapable of deciding the roadmap,
    # creating GitHub issues, or changing permissions.
    field_names = set(CriticOutput.model_fields)
    assert field_names == {
        "status",
        "issues",
        "unsupported_claims",
        "citation_errors",
        "duplicate_opportunities",
        "required_revisions",
    }


# ---------------------------------------------------------------------------
# Wire (structured-output) schema: regression for the second real Group 1
# failure. The real critic twice returned status="revise" with
# required_revisions=[] -- Pydantic's CriticOutput.model_validator correctly
# rejected it, but that cross-field rule was invisible in the flat
# CriticOutput.model_json_schema() sent to Gemini, so prompt wording alone
# was relied on and failed twice. build_critic_response_schema() now sends
# an explicit pass/revise anyOf union whose per-branch minItems/maxItems
# encode the same rule structurally.
# ---------------------------------------------------------------------------


def test_critic_response_schema_offers_exactly_two_pass_revise_alternatives():
    schema = build_critic_response_schema()
    alternatives = schema.get("anyOf") or schema.get("oneOf")
    assert alternatives is not None, "response schema must use anyOf/oneOf"
    assert len(alternatives) == 2

    ref_names = {alt["$ref"].rsplit("/", 1)[-1] for alt in alternatives}
    assert ref_names == {"CriticPassResponse", "CriticReviseResponse"}


def test_critic_response_schema_revise_branch_requires_at_least_one_revision():
    schema = build_critic_response_schema()
    revise_schema = schema["$defs"]["CriticReviseResponse"]
    required_revisions_schema = revise_schema["properties"]["required_revisions"]
    assert required_revisions_schema["minItems"] == 1
    assert "required_revisions" in revise_schema["required"]


def test_critic_response_schema_pass_branch_forbids_revision_instructions():
    schema = build_critic_response_schema()
    pass_schema = schema["$defs"]["CriticPassResponse"]
    required_revisions_schema = pass_schema["properties"]["required_revisions"]
    assert required_revisions_schema["maxItems"] == 0


def _wire_findings(**overrides: Any) -> dict:
    payload = {
        "issues": [],
        "unsupported_claims": [],
        "citation_errors": [],
        "duplicate_opportunities": [],
    }
    payload.update(overrides)
    return payload


def test_wire_schema_parses_valid_pass_output():
    adapter = TypeAdapter(CriticWireResponse)
    parsed = adapter.validate_python(
        _wire_findings(status="pass", required_revisions=[])
    )
    assert isinstance(parsed, CriticPassResponse)
    assert parsed.required_revisions == []


def test_wire_schema_parses_valid_revise_output_with_a_required_revision():
    adapter = TypeAdapter(CriticWireResponse)
    parsed = adapter.validate_python(
        _wire_findings(status="revise", required_revisions=["Merge opp_2 into opp_1."])
    )
    assert isinstance(parsed, CriticReviseResponse)
    assert parsed.required_revisions == ["Merge opp_2 into opp_1."]


def test_wire_schema_rejects_revise_with_empty_required_revisions():
    adapter = TypeAdapter(CriticWireResponse)
    with pytest.raises(ValidationError):
        adapter.validate_python(_wire_findings(status="revise", required_revisions=[]))


def test_wire_schema_rejects_pass_with_nonempty_required_revisions():
    adapter = TypeAdapter(CriticWireResponse)
    with pytest.raises(ValidationError):
        adapter.validate_python(
            _wire_findings(status="pass", required_revisions=["should not be allowed"])
        )


def test_run_critic_rejects_real_failure_shape_revise_with_no_required_revisions(
    packet, analyst_output, validation_result
):
    # This is the literal shape the real Gemini critic returned twice live:
    # status="revise", required_revisions=[]. It must still be rejected --
    # now caught by the wire schema/adapter, and CriticOutput's own
    # model_validator remains as a second, unremoved guardrail.
    payload = _revise_payload()
    payload["required_revisions"] = []
    client = FakeCriticClient(payload)
    with pytest.raises(CriticError):
        run_critic(packet, analyst_output, validation_result, client=client)


def test_critic_output_model_validator_still_rejects_revise_with_empty_required_revisions():
    # Unremoved guardrail: even bypassing run_critic/the wire adapter
    # entirely, CriticOutput itself still refuses this shape directly.
    with pytest.raises(ValidationError):
        CriticOutput.model_validate({**_revise_payload(), "required_revisions": []})


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def test_prompt_contains_packet_evidence(packet, analyst_output, validation_result):
    prompt = build_critic_prompt(packet, analyst_output, validation_result)
    assert "The invoice payment process is confusing and billing support is slow." in prompt


def test_prompt_contains_analyst_draft(packet, analyst_output, validation_result):
    prompt = build_critic_prompt(packet, analyst_output, validation_result)
    assert "Simplify invoice payment" in prompt


def test_prompt_contains_deterministic_validation_findings(packet, analyst_output, validation_result):
    prompt = build_critic_prompt(packet, analyst_output, validation_result)
    assert "evidence_count" in prompt
    assert "opp_1" in prompt


def test_prompt_states_critic_cannot_take_autonomous_action(packet, analyst_output, validation_result):
    prompt = build_critic_prompt(packet, analyst_output, validation_result)
    assert "do not decide the roadmap" in prompt
    assert "GitHub issue" in prompt


def test_prompt_states_revise_requires_non_empty_required_revisions(
    packet, analyst_output, validation_result
):
    # Regression for the live Group 1 smoke failure: Gemini returned
    # status="revise" with required_revisions=[], which Pydantic correctly
    # rejected. The prompt must explicitly instruct the model never to do
    # this rather than relying on schema validation alone.
    prompt = build_critic_prompt(packet, analyst_output, validation_result)
    assert 'If status="revise", required_revisions MUST contain at least one concrete,' in prompt
    assert "actionable revision instruction" in prompt


def test_prompt_explicitly_prohibits_revise_with_empty_required_revisions(
    packet, analyst_output, validation_result
):
    prompt = build_critic_prompt(packet, analyst_output, validation_result)
    assert 'NEVER return status="revise" with an empty required_revisions list' in prompt


def test_prompt_requires_deterministic_findings_to_produce_revise_and_revisions(
    packet, analyst_output, validation_result
):
    prompt = build_critic_prompt(packet, analyst_output, validation_result)
    assert "for example an unsupported affected_segments" in prompt
    assert "an invalid feedback/context ID, or a non-exact quotation" in prompt


def test_prompt_requires_every_issue_to_have_a_matching_required_revision(
    packet, analyst_output, validation_result
):
    prompt = build_critic_prompt(packet, analyst_output, validation_result)
    assert (
        "Every issue you list in `issues` that requires changing the analyst output"
        in prompt
    )


# ---------------------------------------------------------------------------
# run_critic request boundary
# ---------------------------------------------------------------------------


def test_run_critic_invokes_injected_client_exactly_once(packet, analyst_output, validation_result):
    client = FakeCriticClient(_pass_payload())
    run_critic(packet, analyst_output, validation_result, client=client)
    assert len(client.calls) == 1


def test_run_critic_passes_cache_key_to_client(packet, analyst_output, validation_result):
    client = FakeCriticClient(_pass_payload())
    run_critic(packet, analyst_output, validation_result, client=client)
    assert client.calls[0]["cache_key"] == "fake_cache_key_123"


def test_run_critic_passes_response_schema_to_client(packet, analyst_output, validation_result):
    client = FakeCriticClient(_pass_payload())
    run_critic(packet, analyst_output, validation_result, client=client)
    assert client.calls[0]["response_schema"] == build_critic_response_schema()


def test_run_critic_returns_pass_output(packet, analyst_output, validation_result):
    client = FakeCriticClient(_pass_payload())
    result = run_critic(packet, analyst_output, validation_result, client=client)
    assert result.status == CriticStatus.PASS


def test_run_critic_returns_revise_output(packet, analyst_output, validation_result):
    client = FakeCriticClient(_revise_payload())
    result = run_critic(packet, analyst_output, validation_result, client=client)
    assert result.status == CriticStatus.REVISE
    assert result.required_revisions == ["Merge opp_2 into opp_1 and reduce its confidence score."]


def test_run_critic_raises_on_invalid_client_output(packet, analyst_output, validation_result):
    payload = _revise_payload()
    payload["required_revisions"] = []
    client = FakeCriticClient(payload)
    with pytest.raises(CriticError):
        run_critic(packet, analyst_output, validation_result, client=client)


def test_run_critic_raises_on_empty_output(packet, analyst_output, validation_result):
    class EmptyClient:
        def generate_structured(self, *, prompt, response_schema, cache_key):
            return ""

    with pytest.raises(CriticError):
        run_critic(packet, analyst_output, validation_result, client=EmptyClient())


def test_run_critic_raises_on_non_json_output(packet, analyst_output, validation_result):
    class GarbageClient:
        def generate_structured(self, *, prompt, response_schema, cache_key):
            return "not json"

    with pytest.raises(CriticError):
        run_critic(packet, analyst_output, validation_result, client=GarbageClient())


# ---------------------------------------------------------------------------
# Real client: lazy API key, no network in tests
# ---------------------------------------------------------------------------


def test_importing_critic_module_does_not_require_api_key():
    # Subprocess (not importlib.reload) for the same reason as the analyst's
    # equivalent test: reload() would corrupt already-imported classes used
    # elsewhere in this file.
    repo_root = Path(__file__).resolve().parents[1]
    env = {key: value for key, value in os.environ.items() if key != "GEMINI_API_KEY"}
    env["PYTHONPATH"] = str(repo_root / "src")
    for var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        env[var] = "1"

    result = subprocess.run(
        [sys.executable, "-c", "import product_feedback_agent.critic"],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_missing_api_key_gives_clear_error_from_real_client(monkeypatch, tmp_path):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    client = GeminiCriticClient(log_path=tmp_path / "model-calls.jsonl")

    with pytest.raises(CriticError, match="GEMINI_API_KEY"):
        client.generate_structured(prompt="hello", response_schema={}, cache_key="ck")


def test_missing_api_key_trace_does_not_reference_a_key_value(monkeypatch, tmp_path):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    log_path = tmp_path / "model-calls.jsonl"
    client = GeminiCriticClient(log_path=log_path)

    with pytest.raises(CriticError):
        client.generate_structured(prompt="hello", response_schema={}, cache_key="ck")

    contents = log_path.read_text(encoding="utf-8")
    record = json.loads(contents.strip().splitlines()[0])
    assert record["success"] is False
    assert record["stage"] == "critic"
    assert record["cache_key"] == "ck"


# ---------------------------------------------------------------------------
# Real client happy path: fully mocked google.genai, still no real network
# ---------------------------------------------------------------------------


class _FakeInteractions:
    def __init__(self, output_text: str, usage: dict | None) -> None:
        self._output_text = output_text
        self._usage = usage
        self.create_calls: list[dict] = []

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return types.SimpleNamespace(output_text=self._output_text, usage=self._usage)


class _FakeGenAIClient:
    def __init__(self, api_key: str, interactions: _FakeInteractions) -> None:
        self.api_key = api_key
        self.interactions = interactions


def _install_fake_genai(monkeypatch, interactions: _FakeInteractions) -> None:
    fake_genai_module = types.ModuleType("google.genai")
    fake_genai_module.Client = lambda api_key: _FakeGenAIClient(api_key, interactions)

    fake_google_module = types.ModuleType("google")
    fake_google_module.genai = fake_genai_module

    monkeypatch.setitem(sys.modules, "google", fake_google_module)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai_module)


def test_real_client_success_writes_trace_without_api_key(monkeypatch, tmp_path):
    secret_key = "sekrit-test-key-do-not-leak"
    monkeypatch.setenv("GEMINI_API_KEY", secret_key)

    payload = json.dumps(_pass_payload())
    interactions = _FakeInteractions(output_text=payload, usage={"input_tokens": 100, "output_tokens": 50})
    _install_fake_genai(monkeypatch, interactions)

    log_path = tmp_path / "model-calls.jsonl"
    client = GeminiCriticClient(log_path=log_path)

    raw = client.generate_structured(
        prompt="critique this", response_schema=build_critic_response_schema(), cache_key="ck123"
    )

    assert raw == payload
    assert len(interactions.create_calls) == 1
    assert interactions.create_calls[0]["model"] == "gemini-3.7-flash"
    assert interactions.create_calls[0]["response_format"] == {
        "type": "text",
        "mime_type": "application/json",
        "schema": build_critic_response_schema(),
    }

    log_contents = log_path.read_text(encoding="utf-8")
    assert secret_key not in log_contents

    record = json.loads(log_contents.strip().splitlines()[0])
    assert record["success"] is True
    assert record["stage"] == "critic"
    assert record["model"] == "gemini-3.7-flash"
    assert record["cache_key"] == "ck123"
    assert record["usage"] == {"input_tokens": 100, "output_tokens": 50}
    assert "GEMINI_API_KEY" not in json.dumps(record)


def test_real_client_empty_output_raises_and_traces_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "another-secret-key")
    interactions = _FakeInteractions(output_text="", usage=None)
    _install_fake_genai(monkeypatch, interactions)

    log_path = tmp_path / "model-calls.jsonl"
    client = GeminiCriticClient(log_path=log_path)

    with pytest.raises(CriticError):
        client.generate_structured(prompt="critique this", response_schema={}, cache_key="ck456")

    record = json.loads(log_path.read_text(encoding="utf-8").strip().splitlines()[0])
    assert record["success"] is False
    assert record["error_type"] == "EmptyOutputError"
    assert "another-secret-key" not in log_path.read_text(encoding="utf-8")


def test_default_client_construction_does_not_touch_env_or_network(monkeypatch, tmp_path):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    client = GeminiCriticClient()
    assert client is not None
