"""Failure vocabulary shared by every application facade."""


class ApplicationAuthError(Exception):
    """Typed authentication failure exposed by the application layer."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ApplicationCommandError(Exception):
    """Operator-facing failure translated at the application boundary."""

    def __init__(self, code: str, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code


def exit_code_for_error(code: str) -> int:
    """Map one error code to the process exit code delivery must report."""
    if code.startswith("AUTH_"):
        return 3
    if code in {"NOT_FOUND", "BAD_SELECTOR"}:
        return 4
    return 1


__all__ = ["ApplicationAuthError", "ApplicationCommandError", "exit_code_for_error"]
