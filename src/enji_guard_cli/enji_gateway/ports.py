"""Infrastructure types used to construct Enji Gateway adapters."""

from pathlib import Path
from typing import Protocol

from enji_guard_cli.auth_session.models import StoredAuth
from enji_guard_cli.transport import EnjiHttpClient

type GatewayAuthFile = Path | None
type GatewayClient = EnjiHttpClient | None


class GatewayAuthPort(Protocol):
    """Auth capabilities required by the HTTP gateway client."""

    def load(self, auth_file: Path | None = None) -> StoredAuth | None: ...

    def headers(self, stored_auth: StoredAuth) -> dict[str, str]: ...

    def is_cookie_session(self, stored_auth: StoredAuth) -> bool: ...

    async def refresh(self, auth_file: Path, stored_auth: StoredAuth, client: EnjiHttpClient) -> StoredAuth: ...


__all__ = [
    "GatewayAuthFile",
    "GatewayAuthPort",
    "GatewayClient",
]
