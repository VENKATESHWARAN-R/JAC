"""Tests for env snapshot/restore — backbone of the REPL's fail-safe rebuild."""

from __future__ import annotations

import os

import pytest

from jac.secrets import restore_env, snapshot_env


def test_snapshot_records_present_and_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JAC_TEST_PRESENT", "value-1")
    monkeypatch.delenv("JAC_TEST_ABSENT", raising=False)

    snap = snapshot_env(["JAC_TEST_PRESENT", "JAC_TEST_ABSENT"])
    assert snap == {"JAC_TEST_PRESENT": "value-1", "JAC_TEST_ABSENT": None}


def test_restore_writes_back_and_deletes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JAC_TEST_A", "original")
    monkeypatch.delenv("JAC_TEST_B", raising=False)

    snap = snapshot_env(["JAC_TEST_A", "JAC_TEST_B"])

    # Mutate: change A, add B.
    os.environ["JAC_TEST_A"] = "mutated"
    os.environ["JAC_TEST_B"] = "freshly-added"

    restore_env(snap)
    assert os.environ.get("JAC_TEST_A") == "original"
    assert "JAC_TEST_B" not in os.environ


def test_restore_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JAC_TEST_X", "v")
    snap = snapshot_env(["JAC_TEST_X"])
    restore_env(snap)
    restore_env(snap)  # second call must not raise or duplicate
    assert os.environ["JAC_TEST_X"] == "v"


def test_snapshot_with_no_keys_is_empty() -> None:
    assert snapshot_env([]) == {}
    # restoring an empty snapshot is a no-op
    restore_env({})


def test_round_trip_absent_to_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JAC_TEST_NEVER", raising=False)
    snap = snapshot_env(["JAC_TEST_NEVER"])
    # Don't mutate; restore should leave it absent.
    restore_env(snap)
    assert "JAC_TEST_NEVER" not in os.environ


def test_partial_failure_rollback_pattern(monkeypatch: pytest.MonkeyPatch) -> None:
    """The intended usage: snapshot, apply (which raises midway), restore."""
    monkeypatch.setenv("JAC_TEST_KEEP", "before")
    monkeypatch.delenv("JAC_TEST_NEW", raising=False)

    snap = snapshot_env(["JAC_TEST_KEEP", "JAC_TEST_NEW"])
    try:
        os.environ["JAC_TEST_KEEP"] = "during"
        os.environ["JAC_TEST_NEW"] = "added"
        raise ValueError("simulated failure midway")
    except ValueError:
        restore_env(snap)

    assert os.environ["JAC_TEST_KEEP"] == "before"
    assert "JAC_TEST_NEW" not in os.environ
