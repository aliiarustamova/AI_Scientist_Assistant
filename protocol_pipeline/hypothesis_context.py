"""Shared snippets for LLM prompts (user constraints, etc.)."""

from __future__ import annotations

from src.types import Hypothesis


def append_user_constraints(h: Hypothesis) -> str:
    """If the researcher set constraints on the hypothesis, append them to the user prompt."""
    t = (h.user_constraints or "").strip()
    if not t:
        return ""
    return (
        "\n\nResearcher-specified constraints (must be respected; prefer these over generic"
        " defaults when they conflict):\n"
        + t
    )
