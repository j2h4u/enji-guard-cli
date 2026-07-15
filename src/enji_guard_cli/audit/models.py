from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AuditDefinition:
    """One live audit action discovered from the upstream catalog."""

    action_key: str
    title: str
    metric_group: str | None
    runbook_kind: str

    @property
    def selector(self) -> str:
        """Return the CLI selector derived from the live action key."""

        prefix = "audit."
        if not self.action_key.startswith(prefix) or len(self.action_key) == len(prefix):
            raise ValueError(f"audit action key must start with {prefix}: {self.action_key}")
        return self.action_key.removeprefix(prefix)


@dataclass(frozen=True, slots=True)
class AuditCatalog:
    """Live published audits, with recon kept separate."""

    published_audits: tuple[AuditDefinition, ...]
    recon: AuditDefinition
