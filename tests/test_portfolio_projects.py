# pyright: basic

from typing import Any, cast

from enji_guard_cli.portfolio.models import ProjectDetail, ProjectRef
from enji_guard_cli.portfolio.projects import create_project, delete_project, rename_project


class Gateway:
    def __init__(self) -> None:
        self.projects = [ProjectRef("p1", "Pets")]
        self.deleted = False

    def list_projects(self):
        return tuple(self.projects)

    def project_detail(self, project_id):
        return ProjectDetail(next(p for p in self.projects if p.project_id == project_id))

    def create_project(self, name):
        p = ProjectRef("p2", name)
        self.projects.append(p)
        return p

    def rename_project(self, project_id, name):
        p = ProjectRef(project_id, name)
        self.projects[0] = p
        return p

    def delete_project(self, project_id):
        self.deleted = True


def test_project_crud_is_repeat_safe() -> None:
    gateway = Gateway()
    typed = cast(Any, gateway)
    assert create_project("pets", gateway=typed).state == "already_present"
    assert rename_project("p1", "Pets", gateway=typed).state == "unchanged"
    assert create_project("Dogs", gateway=typed).state == "created"
    assert delete_project("p1", gateway=typed).state == "deleted"
