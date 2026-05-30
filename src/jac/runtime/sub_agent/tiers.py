"""Sub-agent tier names and cascade resolution.

A *tier* (small / medium / large) is the cognitive-budget knob the main
agent reasons about; :func:`resolve_tier` maps a requested tier to a
concrete model on the active profile, cascading *up* (never down) when the
requested tier is absent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from jac.errors import JacConfigError
from jac.profiles import Profile

TierName = Literal["small", "medium", "large"]
"""Conventional tier names. Profile schema allows any lowercase identifier,
but the sub-agent tool exposes only these three — they're the cognitive
budget knobs the main agent reasons about."""

_TIER_CASCADE: dict[str, list[str]] = {
    "small": ["small", "medium", "large"],
    "medium": ["medium", "large"],
    "large": ["large"],
}
"""Cascade order: requested tier first, then strictly *larger* tiers as
fallback. Never cascades downward — that would silently exceed budget."""


@dataclass(frozen=True)
class _ResolvedTier:
    requested: str
    resolved: str
    model: str
    cascaded: bool

    @property
    def cascade_note(self) -> str | None:
        if not self.cascaded:
            return None
        return f"requested {self.requested!r}, cascaded up to {self.resolved!r}"


def resolve_tier(profile: Profile, requested: str) -> _ResolvedTier:
    """Pick the cheapest available tier ≥ ``requested`` from ``profile``.

    Cascades upward through :data:`_TIER_CASCADE`. Raises
    :class:`JacConfigError` when neither the requested tier nor any
    upward fallback exists — the main agent gets a structured error it
    can show the user.
    """
    candidates = _TIER_CASCADE.get(requested)
    if candidates is None:
        raise JacConfigError(
            f"unknown sub-agent tier {requested!r}; valid tiers: small, medium, large"
        )
    for candidate in candidates:
        if profile.tiers.get(candidate):
            return _ResolvedTier(
                requested=requested,
                resolved=candidate,
                model=profile.tiers[candidate][0],
                cascaded=(candidate != requested),
            )
    raise JacConfigError(
        f"no tier ≥ {requested!r} configured on the active profile "
        f"(have: {', '.join(sorted(profile.tiers)) or '<none>'}). "
        "Add a tier to ~/.jac/config.yaml or pick a different tier."
    )
