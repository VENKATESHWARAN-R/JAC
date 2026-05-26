"""Tests for ``@jac_tool`` — both bare and parameterized forms."""

from __future__ import annotations

import pytest

from jac.tools import is_jac_tool, is_summarizable, jac_tool


def test_bare_form_marks_jac_tool() -> None:
    @jac_tool
    def foo(reason: str, x: int) -> int:
        return x

    assert is_jac_tool(foo)
    assert is_summarizable(foo) is False


def test_parameterized_form_with_summarizable() -> None:
    @jac_tool(summarizable=True)
    def bar(reason: str, x: int) -> int:
        return x

    assert is_jac_tool(bar)
    assert is_summarizable(bar) is True


def test_parameterized_form_with_default() -> None:
    @jac_tool()
    def baz(reason: str) -> str:
        return "ok"

    assert is_jac_tool(baz)
    assert is_summarizable(baz) is False


def test_missing_reason_param_raises() -> None:
    with pytest.raises(TypeError, match="missing required `reason: str`"):

        @jac_tool
        def bad() -> None:  # noqa: ANN
            pass


def test_wrong_first_param_name_raises() -> None:
    with pytest.raises(TypeError, match="first non-ctx parameter must be named `reason`"):

        @jac_tool
        def bad(why: str) -> None:
            pass


def test_wrong_reason_annotation_raises() -> None:
    with pytest.raises(TypeError, match="`reason` must be annotated `str`"):

        @jac_tool
        def bad(reason: int) -> None:
            pass


def test_leading_ctx_param_is_tolerated() -> None:
    @jac_tool(summarizable=True)
    def with_ctx(ctx, reason: str, x: int) -> int:  # noqa: ANN
        return x

    assert is_jac_tool(with_ctx)
    assert is_summarizable(with_ctx) is True


def test_is_summarizable_on_plain_function_is_false() -> None:
    def plain() -> None:
        pass

    assert is_summarizable(plain) is False
