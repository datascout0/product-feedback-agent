import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest
from pydantic import ValidationError

from product_feedback_agent.analysis_context import (
    AnalysisContextPacket,
    ContextEvidence,
    DatasetStatistics,
    FeedbackEvidence,
    RetrievalParameters,
)
from product_feedback_agent.analyst import (
    AnalystClient,
    AnalystError,
    AnalystOutput,
    GeminiAnalystClient,
    OpportunityDraft,
    ScoreComponentProposal,
    build_analyst_prompt,
    run_analyst,
)


# ---------------------------------------------------------------------------
# Fixtures: a minimal deterministic AnalysisContextPacket, no retrieval/model
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


def _opportunity(opportunity_id: str = "opp_1", feedback_ids: list[str] | None = None) -> dict:
    feedback_ids = feedback_ids if feedback_ids is not None else ["fb_billing", "fb_invoice2"]
    return {
        "opportunity_id": opportunity_id,
        "title": "Simplify invoice payment",
        "problem_statement": "Enterprise users find invoice payment confusing.",
        "underlying_need": "Users need a clear, fast way to understand and pay invoices.",
        "affected_segments": ["enterprise"],
        "feedback_ids": feedback_ids,
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


def _three_opportunities() -> list[dict]:
    return [_opportunity("opp_1"), _opportunity("opp_2"), _opportunity("opp_3")]


class FakeAnalystClient:
    """Records calls and returns a preset raw JSON string."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    def generate_structured(self, *, prompt: str, response_schema: dict, cache_key: str) -> str:
        self.calls.append({"prompt": prompt, "response_schema": response_schema, "cache_key": cache_key})
        return json.dumps(self.payload)


# ---------------------------------------------------------------------------
# Schema: exactly-three enforcement
# ---------------------------------------------------------------------------


def test_exactly_three_valid_opportunity_drafts_parse():
    output = AnalystOutput.model_validate({"opportunities": _three_opportunities()})
    assert len(output.opportunities) == 3
    assert all(isinstance(o, OpportunityDraft) for o in output.opportunities)


def test_fewer_than_three_opportunities_fail_validation():
    with pytest.raises(ValidationError):
        AnalystOutput.model_validate({"opportunities": [_opportunity("opp_1"), _opportunity("opp_2")]})


def test_more_than_three_opportunities_fail_validation():
    opportunities = _three_opportunities() + [_opportunity("opp_4")]
    with pytest.raises(ValidationError):
        AnalystOutput.model_validate({"opportunities": opportunities})


def test_score_component_rejects_value_below_1():
    with pytest.raises(ValidationError):
        ScoreComponentProposal(score=0, rationale="too low")


def test_score_component_rejects_value_above_5():
    with pytest.raises(ValidationError):
        ScoreComponentProposal(score=6, rationale="too high")


def test_analyst_output_does_not_define_evidence_count_or_opportunity_score_fields():
    field_names = set(OpportunityDraft.model_fields)
    assert "evidence_count" not in field_names
    assert "opportunity_score" not in field_names


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def test_prompt_contains_feedback_evidence(packet):
    prompt = build_analyst_prompt(packet)
    assert "The invoice payment process is confusing and billing support is slow." in prompt


def test_prompt_contains_product_context_evidence(packet):
    prompt = build_analyst_prompt(packet)
    assert "Enterprise pricing strategy focuses on annual contracts and volume discounts." in prompt


def test_prompt_contains_stable_feedback_ids(packet):
    prompt = build_analyst_prompt(packet)
    assert "fb_billing" in prompt
    assert "fb_invoice2" in prompt


def test_prompt_contains_document_and_section_ids(packet):
    prompt = build_analyst_prompt(packet)
    assert "doc_pricing" in prompt
    assert "doc_pricing_sec000" in prompt


def test_prompt_instructs_exactly_three_and_no_invented_ids(packet):
    prompt = build_analyst_prompt(packet)
    assert "EXACTLY three" in prompt
    assert "Never invent feedback IDs" in prompt
    assert "Never invent document IDs or section IDs" in prompt


# ---------------------------------------------------------------------------
# run_analyst request boundary
# ---------------------------------------------------------------------------


def test_run_analyst_invokes_injected_client_exactly_once(packet):
    client = FakeAnalystClient({"opportunities": _three_opportunities()})
    run_analyst(packet, client=client)
    assert len(client.calls) == 1


def test_run_analyst_result_preserves_model_returned_evidence_ids(packet):
    client = FakeAnalystClient({"opportunities": _three_opportunities()})
    output = run_analyst(packet, client=client)
    assert output.opportunities[0].feedback_ids == ["fb_billing", "fb_invoice2"]
    assert output.opportunities[0].representative_evidence[0].feedback_id == "fb_billing"
    assert output.opportunities[0].related_context[0].document_id == "doc_pricing"


def test_run_analyst_passes_cache_key_to_client(packet):
    client = FakeAnalystClient({"opportunities": _three_opportunities()})
    run_analyst(packet, client=client)
    assert client.calls[0]["cache_key"] == "fake_cache_key_123"


def test_run_analyst_passes_response_schema_to_client(packet):
    client = FakeAnalystClient({"opportunities": _three_opportunities()})
    run_analyst(packet, client=client)
    assert client.calls[0]["response_schema"] == AnalystOutput.model_json_schema()


def test_run_analyst_raises_on_invalid_client_output(packet):
    client = FakeAnalystClient({"opportunities": [_opportunity("opp_1")]})
    with pytest.raises(AnalystError):
        run_analyst(packet, client=client)


def test_run_analyst_raises_on_empty_output(packet):
    class EmptyClient:
        def generate_structured(self, *, prompt, response_schema, cache_key):
            return ""

    with pytest.raises(AnalystError):
        run_analyst(packet, client=EmptyClient())


def test_run_analyst_raises_on_non_json_output(packet):
    class GarbageClient:
        def generate_structured(self, *, prompt, response_schema, cache_key):
            return "not json"

    with pytest.raises(AnalystError):
        run_analyst(packet, client=GarbageClient())


# ---------------------------------------------------------------------------
# Real client: lazy API key, no network in tests
# ---------------------------------------------------------------------------


def test_importing_analyst_module_does_not_require_api_key():
    # Run in a fresh subprocess (with GEMINI_API_KEY stripped) rather than
    # importlib.reload() in-process: reload() mutates the already-imported
    # module's shared globals in place, which would make classes imported at
    # the top of this file (AnalystError, GeminiAnalystClient, ...) silently
    # diverge from the reloaded module's own classes and corrupt every other
    # test in this file. A subprocess is the only way to genuinely test
    # "importing this module from scratch needs no API key" in isolation.
    repo_root = Path(__file__).resolve().parents[1]
    env = {key: value for key, value in os.environ.items() if key != "GEMINI_API_KEY"}
    env["PYTHONPATH"] = str(repo_root / "src")
    # Importing analyst.py transitively imports chromadb (via analysis_context
    # -> retrieval). Chromadb's native BLAS-backed deps can otherwise spawn
    # large thread pools that starve the main pytest process's concurrently
    # running chromadb tests on constrained machines.
    for var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        env[var] = "1"

    result = subprocess.run(
        [sys.executable, "-c", "import product_feedback_agent.analyst"],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_missing_api_key_gives_clear_error_from_real_client(monkeypatch, tmp_path):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    client = GeminiAnalystClient(log_path=tmp_path / "model-calls.jsonl")

    with pytest.raises(AnalystError, match="GEMINI_API_KEY"):
        client.generate_structured(prompt="hello", response_schema={}, cache_key="ck")


def test_missing_api_key_trace_does_not_reference_a_key_value(monkeypatch, tmp_path):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    log_path = tmp_path / "model-calls.jsonl"
    client = GeminiAnalystClient(log_path=log_path)

    with pytest.raises(AnalystError):
        client.generate_structured(prompt="hello", response_schema={}, cache_key="ck")

    contents = log_path.read_text(encoding="utf-8")
    record = json.loads(contents.strip().splitlines()[0])
    assert record["success"] is False
    assert record["stage"] == "analyst"
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

    payload = json.dumps({"opportunities": _three_opportunities()})
    interactions = _FakeInteractions(output_text=payload, usage={"input_tokens": 100, "output_tokens": 50})
    _install_fake_genai(monkeypatch, interactions)

    log_path = tmp_path / "model-calls.jsonl"
    client = GeminiAnalystClient(log_path=log_path)

    raw = client.generate_structured(
        prompt="analyze this", response_schema=AnalystOutput.model_json_schema(), cache_key="ck123"
    )

    assert raw == payload
    assert len(interactions.create_calls) == 1
    assert interactions.create_calls[0]["model"] == "gemini-3.7-flash"
    assert interactions.create_calls[0]["response_format"] == {
        "type": "text",
        "mime_type": "application/json",
        "schema": AnalystOutput.model_json_schema(),
    }

    log_contents = log_path.read_text(encoding="utf-8")
    assert secret_key not in log_contents

    record = json.loads(log_contents.strip().splitlines()[0])
    assert record["success"] is True
    assert record["stage"] == "analyst"
    assert record["model"] == "gemini-3.7-flash"
    assert record["cache_key"] == "ck123"
    assert record["usage"] == {"input_tokens": 100, "output_tokens": 50}
    assert "GEMINI_API_KEY" not in json.dumps(record)


def test_real_client_empty_output_raises_and_traces_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "another-secret-key")
    interactions = _FakeInteractions(output_text="", usage=None)
    _install_fake_genai(monkeypatch, interactions)

    log_path = tmp_path / "model-calls.jsonl"
    client = GeminiAnalystClient(log_path=log_path)

    with pytest.raises(AnalystError):
        client.generate_structured(prompt="analyze this", response_schema={}, cache_key="ck456")

    record = json.loads(log_path.read_text(encoding="utf-8").strip().splitlines()[0])
    assert record["success"] is False
    assert record["error_type"] == "EmptyOutputError"
    assert "another-secret-key" not in log_path.read_text(encoding="utf-8")


def test_default_client_construction_does_not_touch_env_or_network(monkeypatch, tmp_path):
    # Constructing GeminiAnalystClient (e.g. run_analyst's default) must be
    # cheap and side-effect free; only generate_structured may reach for the
    # API key or the network.
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    client = GeminiAnalystClient()
    assert client is not None
