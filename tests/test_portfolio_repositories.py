# pyright: basic

from typing import Any, cast

from enji_guard_cli.portfolio.models import ProjectDetail, ProjectRef, RepositoryRef
from enji_guard_cli.portfolio.repositories import add_repository, move_repository


class Gateway:
    def __init__(self) -> None:
        self.projects = (ProjectRef("p1", "Pets"), ProjectRef("p2", "Dogs"))
        self.repos = [RepositoryRef("r1", "p1", "Pets", "acme/cat", connected=True)]

    def list_projects(self):
        return self.projects

    def project_detail(self, project_id):
        return ProjectDetail(
            next(p for p in self.projects if p.project_id == project_id),
            tuple(r for r in self.repos if r.project_id == project_id),
        )

    def add_repository(self, project_id, owner, name):
        r = RepositoryRef("r2", project_id, "Pets", f"{owner}/{name}")
        self.repos.append(r)
        return r

    def connect_repository(self, project_id, repo_id):
        return next(r for r in self.repos if r.repo_id == repo_id)

    def preflight_repository_move(self, source, repo, target):
        from enji_guard_cli.portfolio.models import MovePreflight

        return MovePreflight()

    def move_repository(self, source, repo, target):
        old = next(r for r in self.repos if r.repo_id == repo)
        moved = RepositoryRef(old.repo_id, target, "Dogs", old.full_name, old.connected)
        self.repos[self.repos.index(old)] = moved
        return moved


def test_existing_add_and_move() -> None:
    gateway = Gateway()
    typed = cast(Any, gateway)
    assert add_repository("acme/cat", "Pets", gateway=typed).state == "already_present"
    assert move_repository("r1", "Pets", "Dogs", gateway=typed).state == "moved"
