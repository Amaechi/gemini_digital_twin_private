"""Test-wide safety net: no test may send a request to a real LLM (CLAUDE.md, spec §4.3).

``TestModel`` and ``FunctionModel`` are unaffected. A real (Ollama) model is never built: the
engine's ``build_ollama_model`` raises unless a test replaces it on purpose, and the error
propagates (``EngineFailure`` fallback does not catch it), so the test fails loudly instead of
silently falling back to the policy.
"""

import pydantic_ai.models
import pytest

from gemini_agent.agents import engine as engine_module

pydantic_ai.models.ALLOW_MODEL_REQUESTS = False


def _no_real_model(*args, **kwargs):
    raise AssertionError("tests must not build a real LLM model; pass a FunctionModel/TestModel")


@pytest.fixture(autouse=True)
def _block_real_llm(monkeypatch):
    """Make any accidental real-model construction fail the test."""
    monkeypatch.setattr(engine_module, "build_ollama_model", _no_real_model)
