"""Credential-file change observation owned by the auth bounded context."""

from collections.abc import AsyncGenerator
from pathlib import Path

from watchfiles import awatch


async def credential_changes(auth_file: Path) -> AsyncGenerator[None]:
    """Yield whenever the credential file is created, replaced, or modified."""
    target = auth_file.resolve()
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    async for changes in awatch(target.parent, recursive=False):
        if any(Path(changed_path).resolve() == target for _, changed_path in changes):
            yield None


__all__ = ["credential_changes"]
