"""Mini argument parsers for ``/a2a`` subcommands.

Slash input is a single string; ``/a2a serve`` and ``/a2a peer add`` accept
flags. These parsers convert the trailing string into typed values, raising
``ValueError`` with an actionable message on bad input. The dispatchers
catch and render the error so the REPL never crashes on a typo.
"""

from __future__ import annotations


def parse_serve_args(rest: str, *, default_host: str, default_port: int) -> tuple[str, int, bool]:
    """Mini parser for ``[--port N] [--host ADDR] [--unsafe]``.

    Order doesn't matter; unknown args raise ``ValueError``. The headless
    ``jac a2a serve`` typer command parses its own flags upstream and
    never needs this.
    """
    if not rest:
        return default_host, default_port, False

    host = default_host
    port = default_port
    unsafe = False

    tokens = rest.split()
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--unsafe":
            unsafe = True
            i += 1
            continue
        if tok in {"--host", "--port"}:
            if i + 1 >= len(tokens):
                raise ValueError(f"{tok} requires a value")
            value = tokens[i + 1]
            if tok == "--host":
                host = value
            else:
                try:
                    port = int(value)
                except ValueError as exc:
                    raise ValueError(f"--port must be an integer; got {value!r}") from exc
                if not (1 <= port <= 65535):
                    raise ValueError(f"--port must be 1-65535; got {port}")
            i += 2
            continue
        raise ValueError(f"unknown arg {tok!r}; expected --host ADDR | --port N | --unsafe")

    return host, port, unsafe


def parse_peer_add(args: str) -> tuple[str, str, dict | None]:
    """Parse ``NAME URL [--bearer | --api-key HEADER | --oauth2 ...]``.

    Returns ``(name, url, auth_spec_or_None)`` where ``auth_spec`` is a
    dict with a ``kind`` key plus the non-secret parameters parsed from
    the command line. Secret values (token, client_secret) are NOT in
    the spec — they come from the interactive prompt.

    Raises:
        ValueError: bad arg shape.
    """
    tokens = args.split()
    if len(tokens) < 2:
        raise ValueError("expected at least NAME URL")
    name, url = tokens[0], tokens[1]
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"URL must start with http:// or https://; got {url!r}")
    rest = tokens[2:]

    if not rest:
        return name, url, None

    flag = rest[0]
    if flag == "--bearer":
        if len(rest) != 1:
            raise ValueError("--bearer takes no positional args (token is prompted)")
        return name, url, {"kind": "bearer"}

    if flag == "--api-key":
        if len(rest) != 2:
            raise ValueError("--api-key takes one arg: the HEADER name (value is prompted)")
        return name, url, {"kind": "api_key", "header": rest[1]}

    if flag == "--oauth2":
        if len(rest) < 3:
            raise ValueError(
                "--oauth2 expects TOKEN_URL CLIENT_ID [--scope SCOPE] (client_secret is prompted)"
            )
        spec: dict = {
            "kind": "oauth2",
            "token_url": rest[1],
            "client_id": rest[2],
        }
        remaining = rest[3:]
        if remaining:
            if len(remaining) != 2 or remaining[0] != "--scope":
                raise ValueError(
                    f"unexpected trailing args {remaining!r}; supported: [--scope SCOPE]"
                )
            spec["scope"] = remaining[1]
        return name, url, spec

    raise ValueError(
        f"unknown auth flag {flag!r}; expected --bearer | --api-key HEADER | "
        "--oauth2 TOKEN_URL CLIENT_ID [--scope SCOPE]"
    )
