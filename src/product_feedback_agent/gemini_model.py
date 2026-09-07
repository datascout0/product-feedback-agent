"""Shared resolution of the Gemini runtime model name.

Analyst, critic, and revision clients must all use the same resolved model,
and traces must record the model actually used. Resolution happens lazily
inside resolve_gemini_model() -- never at import time -- so importing this
module has no environment or network side effects.
"""

from __future__ import annotations

import os

GEMINI_MODEL_ENV = "PFA_GEMINI_MODEL"
DEFAULT_GEMINI_MODEL = "gemini-3.7-flash"


def resolve_gemini_model() -> str:
    """Return the Gemini model name for this request.

    Reads PFA_GEMINI_MODEL from the environment; falls back to
    DEFAULT_GEMINI_MODEL when unset or empty.
    """
    return os.environ.get(GEMINI_MODEL_ENV) or DEFAULT_GEMINI_MODEL
