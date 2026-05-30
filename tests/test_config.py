"""Tests for :mod:`jac.config` validators (foundation guardrails)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from jac.config import BudgetSettings


def test_budget_knobs_default_to_none_unlimited() -> None:
    settings = BudgetSettings()
    assert settings.session_input_tokens is None
    assert settings.session_total_tokens is None
    assert settings.project_total_tokens is None


def test_budget_knobs_accept_positive() -> None:
    settings = BudgetSettings(
        session_input_tokens=1,
        session_total_tokens=200_000,
        project_total_tokens=5_000_000,
    )
    assert settings.session_input_tokens == 1
    assert settings.session_total_tokens == 200_000
    assert settings.project_total_tokens == 5_000_000


@pytest.mark.parametrize(
    "field",
    ["session_input_tokens", "session_total_tokens", "project_total_tokens"],
)
@pytest.mark.parametrize("bad", [0, -1, -100])
def test_budget_knobs_reject_non_positive(field: str, bad: int) -> None:
    # 0 must NOT silently mean "unlimited" (the opposite of intent) — R11.
    with pytest.raises(ValidationError):
        BudgetSettings(**{field: bad})
