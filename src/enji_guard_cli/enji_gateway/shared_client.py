"""Settings-driven construction of the process-wide HTTP executor.

The executor itself lives in ``transport``; this module only translates
settings into pool limits and retry policy.  It sits in ``enji_gateway`` so
composition can build a client without importing ``transport`` directly, which
the ``auth-and-gateway-own-transport`` import contract forbids.
"""

import httpx

from enji_guard_cli.settings import EnjiGuardSettings, default_settings
from enji_guard_cli.transport import (
    HttpxEnjiHttpClient,
    RetryConfig,
    TransportEventSink,
    discard_transport_event,
)


def create_shared_http_client(
    settings: EnjiGuardSettings | None = None,
    *,
    event_sink: TransportEventSink = discard_transport_event,
) -> HttpxEnjiHttpClient:
    """Build the single pooled HTTP client shared by every caller and thread."""
    resolved = settings if settings is not None else default_settings()
    pool = resolved.transport.pool
    retry = resolved.transport.retry
    return HttpxEnjiHttpClient(
        limits=httpx.Limits(
            max_connections=pool.max_connections,
            max_keepalive_connections=pool.max_keepalive_connections,
            keepalive_expiry=pool.keepalive_expiry_seconds,
        ),
        retry_config=RetryConfig(
            total=retry.total,
            backoff_factor=retry.backoff_factor,
            max_delay_seconds=retry.max_delay_seconds,
            jitter_seconds=retry.jitter_seconds,
            status_forcelist=retry.retryable_status_codes,
            respect_retry_after_header=retry.respect_retry_after_header,
        ),
        event_sink=event_sink,
    )


__all__ = ["create_shared_http_client"]
