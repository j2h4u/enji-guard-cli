from enji_guard_cli.transport import EnjiHttpError, EnjiHttpResponse

AUTH_INVALID_CODE = "AUTH_INVALID"
INVALID_REFRESH_TOKEN_ERROR = "invalid refresh token"
HTTP_OK = 200
HTTP_FORBIDDEN = 403
HTTP_UNAUTHORIZED = 401
HTTP_AUTH_FAILURE_CODES = frozenset({HTTP_UNAUTHORIZED, HTTP_FORBIDDEN})


def is_auth_invalid_response(response: EnjiHttpResponse) -> bool:
    if response.status_code not in HTTP_AUTH_FAILURE_CODES:
        return False
    try:
        payload = response.json(operation="auth invalid check")
    except EnjiHttpError:
        return False
    if not isinstance(payload, dict):
        return False
    error = payload.get("error")
    if isinstance(error, dict):
        return error.get("code") == AUTH_INVALID_CODE
    return payload.get("code") == AUTH_INVALID_CODE


def is_refresh_token_invalid_response(response: EnjiHttpResponse) -> bool:
    if is_auth_invalid_response(response):
        return True
    if response.status_code not in HTTP_AUTH_FAILURE_CODES:
        return False
    try:
        payload = response.json(operation="refresh token invalid check")
    except EnjiHttpError:
        return False
    return isinstance(payload, dict) and payload.get("error") == INVALID_REFRESH_TOKEN_ERROR
