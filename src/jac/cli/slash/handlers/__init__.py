"""Slash handler modules — one file per command, see filenames.

Importing this package triggers every handler's :func:`~jac.cli.slash.registry.register`
decorator at import time. ``a2a/`` is a subpackage because ``/a2a`` has six
subcommands (one file each); every other slash is a single top-level file
whose name matches the command.

Add a new slash by creating a new module here and adding its side-effect
import below.
"""

from __future__ import annotations

# Side-effect imports: each module registers its handler(s).
from jac.cli.slash.handlers import a2a as _a2a  # noqa: F401
from jac.cli.slash.handlers import budget as _budget  # noqa: F401
from jac.cli.slash.handlers import clear as _clear  # noqa: F401
from jac.cli.slash.handlers import mcp as _mcp  # noqa: F401
from jac.cli.slash.handlers import memory as _memory  # noqa: F401
from jac.cli.slash.handlers import memory_edit as _memory_edit  # noqa: F401
from jac.cli.slash.handlers import meta as _meta  # noqa: F401
from jac.cli.slash.handlers import model as _model  # noqa: F401
from jac.cli.slash.handlers import profile as _profile  # noqa: F401
from jac.cli.slash.handlers import resume as _resume  # noqa: F401
from jac.cli.slash.handlers import sessions as _sessions  # noqa: F401
from jac.cli.slash.handlers import skill as _skill  # noqa: F401
from jac.cli.slash.handlers import spawns as _spawns  # noqa: F401
from jac.cli.slash.handlers import tokens as _tokens  # noqa: F401
