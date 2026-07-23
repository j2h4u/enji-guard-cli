"""Infrastructure types used to construct Enji Gateway adapters."""

from pathlib import Path
from typing import Protocol

from enji_guard_cli.auth_session.models import StoredAuth
from enji_guard_cli.transport import EnjiHttpClient

type GatewayAuthFile = Path | None
type GatewayClient = EnjiHttpClient | None


class GatewayCredentialError(Exception):
    """Typed read-only credential failure translated at the gateway boundary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class GatewayCredentialReader(Protocol):
    """Read-only credential capabilities required by the HTTP gateway client."""

    def load(self, auth_file: Path | None = None) -> StoredAuth: ...

    def headers(self, stored_auth: StoredAuth) -> dict[str, str]: ...


__all__ = [
    "GatewayAuthFile",
    "GatewayClient",
    "GatewayCredentialError",
    "GatewayCredentialReader",
]
