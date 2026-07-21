import pytest

from enji_guard_cli.portfolio.scopes import validate_write_scope


def test_batch_scope_requires_explicit_target() -> None:
    with pytest.raises(ValueError):
        validate_write_scope(repo=None, project=None, all_repos=False, all_projects=False, operation="test")
