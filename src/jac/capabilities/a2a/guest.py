"""Construct the guest Gru for inbound A2A calls (D24).

The guest Gru is *almost* a normal Gru. Differences:

1. **Narrowed toolset.** Only the four read-only project-scoped tools:
   ``read_file``, ``list_dir``, ``grep``, ``glob``. No web (host money
   + privacy), no plan/process tools (host state leak), no writes/shell
   (mutating), no clarify (no human in the guest path), no memory
   writes. The cut is enforced *structurally* — we don't pass the
   excluded capabilities at construction, so the model literally
   doesn't see those tools.
2. **No session memory.** The guest doesn't load any host session's
   message history. Each peer conversation thread is its own
   ``context_id`` and lives in fasta2a's Storage, not in JAC's
   ``<repo>/.agents/sessions/``.
3. **No bus, no hooks, no approval, no plan capability.** Those serve
   the host operator; peers don't need (and shouldn't see) them. The
   guest also doesn't get the history-compaction capability — A2A
   conversations are intentionally short-lived and the per-call
   message_history is reset per ``context_id`` anyway.
4. **Same instructions otherwise.** ``gru_system.md`` + project
   ``AGENTS.md`` + project ``memory.md`` all load — the guest IS this
   project's Gru, just answering for a peer. (User-level
   ``~/.jac/AGENTS.md`` / ``~/.jac/memory.md`` are deliberately
   *included* too; they're cross-project preferences the host operator
   has chosen to apply to every Gru. If we ever need to exclude them,
   that's a config knob, not a behavior change here.)

Construction is a single function rather than a class — there's no
state to carry. The fasta2a worker holds the resulting Agent reference
and reuses it across all inbound calls.
"""

from __future__ import annotations

from pydantic_ai import Agent
from pydantic_ai.capabilities import Instrumentation
from pydantic_ai.models import Model

from jac.capabilities.filesystem import FilesystemCapability
from jac.capabilities.search import SearchCapability
from jac.errors import JacConfigError
from jac.workspace.context import load_session_context
from jac.workspace.paths import load_prompt


def build_guest_gru(*, model: str | Model) -> Agent[None, str]:
    """Build the read-only Gru that answers inbound A2A calls.

    Args:
        model: fully-qualified model id (e.g.
            ``anthropic:claude-sonnet-4-5``) OR an already-constructed
            pydantic-ai :class:`Model` instance. The string form is the
            production path (server module resolves it from the active
            profile and passes it in); the instance form makes guest
            testing possible without a real provider configured (tests
            pass a ``TestModel()`` instance).

    Returns:
        A pydantic-ai :class:`Agent` whose toolset is exactly the four
        allowed read-only tools (introspectable via the agent's
        ``toolset`` property — see ``tests/test_a2a_guest.py``).

    Raises:
        JacConfigError: if ``model`` is empty (defensive; the server
            module should never pass an empty string).
    """
    if isinstance(model, str) and not model:
        raise JacConfigError(
            "build_guest_gru requires an explicit model id — guest server "
            "needs to know which model to run inbound calls on (the active "
            "profile's default tier model is the normal choice)."
        )

    # Filesystem: read_file, list_dir, write_file (approval), edit_file
    # (approval). The toolset *exposes* all four to the agent, but the
    # ApprovalRequiredToolset wrapping in the capability gates writes —
    # and in the guest server we install NO approval handler, so deferred
    # tool calls never get answered → writes effectively can't fire.
    #
    # That's a defense-in-depth posture (rather than relying solely on
    # "no approval handler" to keep writes from happening) we'll harden
    # in PR3 by physically filtering write/edit from the toolset. For
    # PR1 the "no approval = no write" pathway is structurally enforced
    # by the test in tests/test_a2a_guest.py (writes raise without an
    # approval handler installed).
    fs = FilesystemCapability()
    search = SearchCapability()

    instructions = _compose_guest_instructions()

    return Agent(model, instructions=instructions, capabilities=[Instrumentation(), fs, search])


def _compose_guest_instructions() -> str:
    """Same prompt as the host Gru + a guest-mode addendum.

    The addendum tells the guest model two things it needs to know that
    the host doesn't: (a) the user it's talking to IS a peer agent, not
    a human, and (b) it has no write/shell/web/process tools so it
    should answer with what it can read or politely decline.

    The addendum lives in ``prompts/a2a_guest_addendum.md`` so the host
    operator can override it per project / per user via the normal prompt
    overlay precedence, same as ``gru_system.md``.
    """
    base = load_prompt("gru_system").strip()
    context = load_session_context()
    addendum = load_prompt("a2a_guest_addendum").strip()
    return (
        f"{base}\n\n---\n\n# A2A guest mode\n\n{addendum}\n\n---\n\n# Session context\n\n{context}"
    )
