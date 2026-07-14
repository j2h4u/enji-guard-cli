from enum import StrEnum


class RetryProfile(StrEnum):
    READ = "READ"
    IDEMPOTENT_MUTATION = "IDEMPOTENT_MUTATION"
    UNSAFE_MUTATION = "UNSAFE_MUTATION"
    AUTH_REFRESH = "AUTH_REFRESH"
    SAFE_PROBE = "SAFE_PROBE"

    @property
    def can_retry(self) -> bool:
        return self in {self.READ, self.SAFE_PROBE, self.IDEMPOTENT_MUTATION}

    @property
    def replay_safe(self) -> bool:
        return self in {self.READ, self.SAFE_PROBE, self.IDEMPOTENT_MUTATION}
