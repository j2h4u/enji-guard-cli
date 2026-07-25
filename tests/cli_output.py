"""Normalise Typer/rich output so assertions test content, not presentation.

Typer renders help through rich, which styles option names by splitting the
token: ``--ready`` is emitted as ``\\x1b[1m-\\x1b[0m\\x1b[1m-ready\\x1b[0m``, with an
escape sequence between the two dashes.  A plain ``"--ready" in stdout`` is
therefore false whenever styling is on, and -- worse -- a negative assertion
like ``"--repo" not in stdout`` passes vacuously.

Pinning environment variables does not fix this: rich builds its console when
Typer is imported, before any fixture runs.  Normalise the output instead.
"""

import re

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def rendered(text: str) -> str:
    """Return `text` without ANSI styling and with whitespace collapsed.

    Whitespace is collapsed because rich wraps and pads inside its boxes, so a
    token can otherwise be split across lines or padded apart from its label.
    """
    return " ".join(_ANSI.sub("", text).replace("│", " ").split())
