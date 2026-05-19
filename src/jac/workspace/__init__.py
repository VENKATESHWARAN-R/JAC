"""JAC workspace — paths, bootstrap, layered config and prompt loading.

Single source of truth for *where* JAC reads and writes files:

- User workspace: ``~/.jac/`` (JAC-private, cross-project)
- Project workspace: ``<project_root>/.agents/`` (community-neutral dir name)
- Project context: ``<project_root>/AGENTS.md`` (community convention)
- User context: ``~/.jac/AGENTS.md``
- Package defaults: shipped with the installed ``jac`` package

See CLAUDE.md "Configuration & workspace" for the conventions.
"""
