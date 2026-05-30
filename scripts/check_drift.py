"""Doc/code drift guard (review item R20).

Replaces the hand-maintained ``docs/design/audit/drift-matrix.md`` (which
itself rotted) with a generated check: introspect the *code* — the slash
registry, the package version — and assert the docs that mirror those facts
are in sync. A drift detector that can't itself drift.

Run via ``just drift`` (or ``uv run python scripts/check_drift.py``). Exits
non-zero on any mismatch so it can gate CI.

Intentionally narrow: it guards the two surfaces that actually drifted in
practice (slash-command coverage and the version pin). The ``filename =
command`` convention and the tool inventory stay review-enforced — encoding
them here would add fragile markdown parsing for little gain.
"""

from __future__ import annotations

import pathlib
import sys
import tomllib

REPO = pathlib.Path(__file__).resolve().parent.parent
CLI_REFERENCE = REPO / "docs" / "user-guide" / "cli-reference.md"
CODEBASE_MAP = REPO / "docs" / "developer" / "codebase-map.md"


def _registered_slash_commands() -> set[str]:
    import jac.cli.slash.handlers  # noqa: F401 — fire @register side effects
    from jac.cli.slash.registry import SLASH_COMMANDS

    return set(SLASH_COMMANDS)


def check_slash_coverage() -> list[str]:
    """Every registered slash command must be documented in both user + dev docs."""
    failures: list[str] = []
    commands = _registered_slash_commands()
    for doc in (CLI_REFERENCE, CODEBASE_MAP):
        text = doc.read_text()
        missing = sorted(c for c in commands if f"/{c}" not in text)
        if missing:
            rel = doc.relative_to(REPO)
            failures.append(
                f"{rel}: registered slash commands not documented: "
                + ", ".join(f"/{c}" for c in missing)
            )
    return failures


def check_version_sync() -> list[str]:
    """``jac.__version__`` must match ``pyproject.toml``'s version."""
    import jac

    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text())
    declared = pyproject["project"]["version"]
    if jac.__version__ != declared:
        return [
            f"version drift: jac.__version__={jac.__version__!r} but "
            f"pyproject.toml version={declared!r} — bump them together."
        ]
    return []


def main() -> int:
    failures: list[str] = []
    failures += check_version_sync()
    failures += check_slash_coverage()
    if failures:
        print("✗ doc/code drift detected:\n")
        for f in failures:
            print(f"  - {f}")
        print("\nFix the doc (or the code) so they agree. See review item R20.")
        return 1
    print("✓ no doc/code drift (slash coverage + version sync)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
