"""Infrastructure types used to construct Enji Gateway adapters."""

from pathlib import Path

from enji_guard_cli.transport import EnjiHttpClient

type GatewayAuthFile = Path | None
type GatewayClient = EnjiHttpClient | None
