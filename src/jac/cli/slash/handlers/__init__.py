"""Slash handler modules.

Importing this package triggers every handler's :func:`~jac.cli.slash.registry.register`
decorator at import time. Add new handler modules to the import list below.
"""

from __future__ import annotations

# Side-effect imports: each module registers its handler(s).
from jac.cli.slash.handlers import a2a as _a2a  # noqa: F401
from jac.cli.slash.handlers import budget as _budget  # noqa: F401
from jac.cli.slash.handlers import exit as _exit  # noqa: F401
from jac.cli.slash.handlers import help as _help  # noqa: F401
from jac.cli.slash.handlers import model as _model  # noqa: F401
from jac.cli.slash.handlers import profile as _profile  # noqa: F401
from jac.cli.slash.handlers import session as _session  # noqa: F401
