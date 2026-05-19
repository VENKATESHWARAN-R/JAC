"""JAC-specific exception types.

Keep this module dependency-free: it's imported everywhere, including from
configuration code that runs before logging is set up.
"""

from __future__ import annotations


class JacError(Exception):
    """Base class for all JAC-specific errors."""


class JacConfigError(JacError):
    """A required configuration value is missing, invalid, or ambiguous.

    Raised eagerly at the boundary where the value is first needed, with a
    message that tells the user exactly how to fix it. Fail-first: do not
    silently default to something that costs money or behaves unexpectedly.
    """
