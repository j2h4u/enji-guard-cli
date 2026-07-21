from dataclasses import dataclass


class EnjiApiError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int | None = None,
        response_malformed: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.response_malformed = response_malformed


@dataclass(frozen=True, slots=True)
class PartialStateDetails:
    operation: str
    completed_step: str
    failed_step: str
    upstream_code: str
    upstream_message: str
    project_id: str | None = None
    project_name: str | None = None


class EnjiPartialStateError(EnjiApiError):
    def __init__(self, details: PartialStateDetails) -> None:
        message_parts: list[str] = [
            f"operation={details.operation}",
            f"completed_step={details.completed_step}",
            f"failed_step={details.failed_step}",
        ]
        if details.project_id is not None:
            message_parts.append(f"project_id={details.project_id}")
        if details.project_name is not None:
            message_parts.append(f"project_name={details.project_name}")
        message_parts.append(f"upstream_code={details.upstream_code}")
        message_parts.append(f"upstream_message={details.upstream_message}")
        super().__init__("PARTIAL_STATE", "; ".join(message_parts))
        self.operation = details.operation
        self.completed_step = details.completed_step
        self.failed_step = details.failed_step
        self.project_id = details.project_id
        self.project_name = details.project_name
        self.upstream_code = details.upstream_code
        self.upstream_message = details.upstream_message
