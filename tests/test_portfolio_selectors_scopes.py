# pyright: basic

import pytest

from enji_guard_cli.portfolio.models import ProjectRef, RepositoryRef
from enji_guard_cli.portfolio.scopes import MutationScope
from enji_guard_cli.portfolio.selectors import parse_github_repo, resolve_project, resolve_repository


def test_selectors_and_explicit_scope() -> None:
    projects = (ProjectRef("p1", "Pets"),)
    assert resolve_project(projects, "pets").project_id == "p1"
    assert MutationScope.from_args(all_repos=True, project="p1").kind == "all_repos"
    assert parse_github_repo("acme/cat") == ("acme", "cat")
    with pytest.raises(ValueError):
        MutationScope.from_args()
    with pytest.raises(ValueError):
        resolve_repository((RepositoryRef("r1", "p1", "Pets", "acme/cat"),), "nope")
