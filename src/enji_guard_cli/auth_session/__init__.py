"""Auth Session bounded-context seam."""

from enji_guard_cli.auth_session.api import AuthError
from enji_guard_cli.auth_session.api_key import (
    ApiKeyCredentialReader,
    EnvironmentCredentialReader,
    api_key_from_environment,
)
from enji_guard_cli.auth_session.models import (
    AuthSessionStatus,
    AuthStatusPayload,
    CredentialType,
    ImportCredentialPayload,
    StoredAuth,
)
from enji_guard_cli.auth_session.ports import AuthSessionPort, CredentialError, CredentialReader, RequestCredentials
from enji_guard_cli.auth_session.service import (
    AuthSessionService,
    auth_status,
    default_auth_file,
    import_bearer_token,
    import_cookie,
)

__all__ = [
    "ApiKeyCredentialReader",
    "AuthError",
    "AuthSessionPort",
    "AuthSessionService",
    "AuthSessionStatus",
    "AuthStatusPayload",
    "CredentialError",
    "CredentialReader",
    "CredentialType",
    "EnvironmentCredentialReader",
    "ImportCredentialPayload",
    "RequestCredentials",
    "StoredAuth",
    "api_key_from_environment",
    "auth_status",
    "default_auth_file",
    "import_bearer_token",
    "import_cookie",
]
