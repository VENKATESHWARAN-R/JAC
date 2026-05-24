"""Tests for jac.capabilities.a2a.card.

Asserts:
- name composition (``jac-<profile>`` vs ``jac``)
- securitySchemes shape with auth vs omitted with --unsafe
- single generic skill present in v1
- generated dict validates against fasta2a's ``agent_card_ta``
  (round-trippable on the wire)
"""

from __future__ import annotations

from fasta2a.schema import agent_card_ta

from jac.capabilities.a2a.card import build_agent_card, card_to_summary


def test_card_name_includes_profile():
    card = build_agent_card(profile_name="claude", base_url="http://127.0.0.1:8001", unsafe=False)
    assert card["name"] == "jac-claude"


def test_card_name_without_profile_is_just_jac():
    card = build_agent_card(profile_name=None, base_url="http://127.0.0.1:8001", unsafe=False)
    assert card["name"] == "jac"


def test_card_carries_url_and_protocol_version():
    card = build_agent_card(profile_name="x", base_url="http://10.0.0.1:9000", unsafe=False)
    assert card["url"] == "http://10.0.0.1:9000"
    assert card["protocol_version"] == "0.3.0"


def test_card_advertises_no_streaming_in_v1():
    card = build_agent_card(profile_name="x", base_url="http://127.0.0.1:8001", unsafe=False)
    caps = card["capabilities"]
    assert caps.get("streaming") is False
    assert caps.get("push_notifications") is False
    assert caps.get("state_transition_history") is False


def test_card_with_auth_declares_bearer_scheme():
    card = build_agent_card(profile_name="x", base_url="http://127.0.0.1:8001", unsafe=False)
    schemes = card.get("security_schemes")
    assert schemes is not None
    assert "bearer" in schemes
    assert schemes["bearer"]["type"] == "http"
    assert schemes["bearer"]["scheme"] == "bearer"
    # Security requirements list the named scheme
    assert {"bearer": []} in card.get("security", [])


def test_card_unsafe_omits_security_schemes():
    card = build_agent_card(profile_name="x", base_url="http://127.0.0.1:8001", unsafe=True)
    assert "security_schemes" not in card
    assert "security" not in card


def test_card_has_exactly_one_skill_in_v1():
    card = build_agent_card(profile_name="x", base_url="http://127.0.0.1:8001", unsafe=False)
    assert len(card["skills"]) == 1
    skill = card["skills"][0]
    assert skill["id"] == "jac-coding-assistant"
    assert "read" in skill["description"].lower() or "code" in skill["description"].lower()


def test_card_is_valid_per_fasta2a_schema():
    """The generated dict must round-trip through fasta2a's TypeAdapter — that's
    what fasta2a uses on the wire, so a failure here = a 500 from the server."""
    card = build_agent_card(profile_name="x", base_url="http://127.0.0.1:8001", unsafe=False)
    # dump_json + validate_json catches both shape and field-name (snake_case →
    # camelCase) issues.
    payload = agent_card_ta.dump_json(card, by_alias=True)
    parsed = agent_card_ta.validate_json(payload)
    assert parsed["name"] == card["name"]


def test_summary_pulls_human_relevant_fields():
    card = build_agent_card(profile_name="x", base_url="http://127.0.0.1:8001", unsafe=False)
    summary = card_to_summary(card)
    assert summary["name"] == "jac-x"
    assert summary["streaming"] is False
    assert summary["auth"] == "bearer"
    assert summary["skills"] == ["jac-coding-assistant"]
    assert summary["protocol_version"] == "0.3.0"


def test_summary_marks_unsafe_no_auth():
    card = build_agent_card(profile_name="x", base_url="http://127.0.0.1:8001", unsafe=True)
    assert card_to_summary(card)["auth"] == "none"
