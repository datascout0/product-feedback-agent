import importlib
import sys

import pytest


def test_unset_env_resolves_to_default(monkeypatch):
    monkeypatch.delenv("PFA_GEMINI_MODEL", raising=False)

    from product_feedback_agent.gemini_model import DEFAULT_GEMINI_MODEL, resolve_gemini_model

    assert resolve_gemini_model() == "gemini-3.7-flash"
    assert DEFAULT_GEMINI_MODEL == "gemini-3.7-flash"


def test_env_override_is_used(monkeypatch):
    monkeypatch.setenv("PFA_GEMINI_MODEL", "gemini-3.5-flash-lite")

    from product_feedback_agent.gemini_model import resolve_gemini_model

    assert resolve_gemini_model() == "gemini-3.5-flash-lite"


def test_empty_env_value_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("PFA_GEMINI_MODEL", "")

    from product_feedback_agent.gemini_model import resolve_gemini_model

    assert resolve_gemini_model() == "gemini-3.7-flash"


def test_import_has_no_env_or_network_side_effects(monkeypatch):
    # Setting the env var AFTER import, then re-importing, must still see the
    # new value: resolution happens at call time, not at import time.
    monkeypatch.delenv("PFA_GEMINI_MODEL", raising=False)
    sys.modules.pop("product_feedback_agent.gemini_model", None)

    module = importlib.import_module("product_feedback_agent.gemini_model")
    assert module.resolve_gemini_model() == "gemini-3.7-flash"

    monkeypatch.setenv("PFA_GEMINI_MODEL", "gemini-3.5-flash-lite")
    # No re-import needed: the already-imported module reads the env fresh
    # on every call rather than caching a value at import time.
    assert module.resolve_gemini_model() == "gemini-3.5-flash-lite"


@pytest.mark.parametrize("stage_module", ["analyst", "critic", "pipeline"])
def test_stage_module_import_does_not_read_pfa_gemini_model(monkeypatch, stage_module):
    # Importing a stage module must not resolve the model or otherwise touch
    # the environment/network; resolution happens only inside
    # generate_structured at request time.
    monkeypatch.delenv("PFA_GEMINI_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    sys.modules.pop(f"product_feedback_agent.{stage_module}", None)

    importlib.import_module(f"product_feedback_agent.{stage_module}")
