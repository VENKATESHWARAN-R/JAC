"""Tests for jac.capabilities.a2a.card.

Asserts:
- name composition (``jac-<profile>`` vs ``jac``)
- securitySchemes shape with auth vs omitted with --unsafe
- single generic skill present in v1
- generated dict validates against fasta2a's ``agent_card_ta``
  (round-trippable on the wire)
"""

from __future__ import annotations

from pathlib import Path

from fasta2a.schema import agent_card_ta

from jac.capabilities.a2a.card import build_agent_card, card_to_summary
from jac.capabilities.skills import LoadedSkill, SkillFrontmatter


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


# ---------- loaded-skills publication (Phase D / D21) ----------------


def _fake_skill(name: str, *, source: str = "package", tools_required=None) -> LoadedSkill:
    return LoadedSkill(
        frontmatter=SkillFrontmatter(
            name=name,
            description=f"do {name} things",
            tools_required=list(tools_required or []),
        ),
        body=f"# {name}\n\nbody",
        source=source,  # type: ignore[arg-type]
        path=Path(f"/fake/{name}/SKILL.md"),
    )


def test_loaded_skills_appended_to_card():
    """Each loaded skill becomes an additional A2A Skill entry; generic stays."""
    loaded = {
        "code-review": _fake_skill("code-review", source="package"),
        "verify-change": _fake_skill("verify-change", source="user"),
    }
    card = build_agent_card(
        profile_name="x",
        base_url="http://127.0.0.1:8001",
        unsafe=False,
        loaded_skills=loaded,
    )
    skill_ids = {s["id"] for s in card["skills"]}
    assert "jac-coding-assistant" in skill_ids  # generic always present
    assert "jac-skill-code-review" in skill_ids
    assert "jac-skill-verify-change" in skill_ids


def test_loaded_skill_tags_carry_source():
    loaded = {"alpha": _fake_skill("alpha", source="project", tools_required=["read_file"])}
    card = build_agent_card(
        profile_name="x",
        base_url="http://127.0.0.1:8001",
        unsafe=False,
        loaded_skills=loaded,
    )
    alpha = next(s for s in card["skills"] if s["id"] == "jac-skill-alpha")
    assert "project" in alpha["tags"]
    assert "read_file" in alpha["tags"]
    assert alpha["examples"] == ["read_file"]


def test_no_loaded_skills_is_equivalent_to_no_arg():
    """Passing an empty dict must behave the same as omitting the param."""
    a = build_agent_card(profile_name="x", base_url="http://127.0.0.1:8001", unsafe=False)
    b = build_agent_card(
        profile_name="x", base_url="http://127.0.0.1:8001", unsafe=False, loaded_skills={}
    )
    assert [s["id"] for s in a["skills"]] == [s["id"] for s in b["skills"]]


def test_loaded_skills_are_sorted_deterministically():
    loaded = {
        "zeta": _fake_skill("zeta"),
        "alpha": _fake_skill("alpha"),
        "mu": _fake_skill("mu"),
    }
    card = build_agent_card(
        profile_name="x",
        base_url="http://127.0.0.1:8001",
        unsafe=False,
        loaded_skills=loaded,
    )
    # First entry is the generic skill; the rest are sorted by name.
    skill_names = [s["id"] for s in card["skills"][1:]]
    assert skill_names == ["jac-skill-alpha", "jac-skill-mu", "jac-skill-zeta"]


def test_card_with_loaded_skills_still_validates():
    loaded = {"alpha": _fake_skill("alpha", tools_required=["x", "y"])}
    card = build_agent_card(
        profile_name="x",
        base_url="http://127.0.0.1:8001",
        unsafe=False,
        loaded_skills=loaded,
    )
    payload = agent_card_ta.dump_json(card, by_alias=True)
    agent_card_ta.validate_json(payload)  # raises on schema drift
