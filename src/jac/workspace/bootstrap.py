"""First-run workspace bootstrap.

Ensures ``~/.jac/`` exists with the expected skeleton. **Idempotent and
silent** — never prompts the user. Interactive setup lives in
``jac.cli.init``; this module only guarantees the bare structure is there
so later code never has to guess whether a directory exists.
"""

from __future__ import annotations

from . import paths

_USER_SKELETON_DIRS = (
    paths.USER_WORKSPACE,
    paths.USER_PROMPTS_DIR,
    paths.USER_MINIONS_DIR,
    paths.USER_SKILLS_DIR,
)

_USER_CONFIG_TEMPLATE = """\
# JAC user-level configuration.
#
# Overrides keys in the shipped package defaults. Project-level config
# at <repo>/.agents/config.yaml overrides this file. Environment variables
# (JAC_*) and CLI flags override everything.
#
# Run `jac init` for an interactive setup wizard.
"""

_USER_CONTEXT_TEMPLATE = """\
# AGENTS.md

> User-level context for AI coworkers. Loaded by JAC into Gru's instructions
> on every session. Keep this short — it's repeated context every turn.

## About me

<!-- Who you are, what you tend to work on, recurring preferences. -->

## Conventions you should follow

<!-- e.g. "I prefer explicit types over Any.",
         "I write commit messages in imperative mood." -->
"""


def ensure_user_workspace() -> bool:
    """Create ``~/.jac/`` skeleton if missing.

    Returns ``True`` if anything was created — useful for printing a one-time
    "welcome, run ``jac init``" message on the first invocation.
    """
    created_anything = False

    for d in _USER_SKELETON_DIRS:
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            created_anything = True

    if not paths.USER_CONFIG_FILE.exists():
        paths.USER_CONFIG_FILE.write_text(_USER_CONFIG_TEMPLATE, encoding="utf-8")
        created_anything = True

    if not paths.USER_CONTEXT_FILE.exists():
        paths.USER_CONTEXT_FILE.write_text(_USER_CONTEXT_TEMPLATE, encoding="utf-8")
        created_anything = True

    return created_anything
