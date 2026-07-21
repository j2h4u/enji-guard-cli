from collections.abc import Sequence

import pytest
from scripts import release_smoke, release_smoke_mutations


def test_mutation_interlock_rejects_without_opt_in() -> None:
    settings = release_smoke.DockerSmokeSettings(repo="unused")
    assert (
        release_smoke_mutations.run_mutations(settings, enabled=False, project_name="__enji_guard_release_smoke__x")
        == 3
    )


def test_mutation_interlock_rejects_unreserved_project() -> None:
    settings = release_smoke.DockerSmokeSettings(repo="unused")
    assert release_smoke_mutations.run_mutations(settings, enabled=True, project_name="production") == 3


def test_malformed_json_mutation_response_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_runner(args: Sequence[str], *, input: str | None = None, timeout: float) -> release_smoke.CommandResult:
        del input, timeout
        if "project" in args and "list" in args:
            return release_smoke.CommandResult(0, '{"projects":[]}\n', "")
        if "project" in args and "create" in args:
            return release_smoke.CommandResult(0, "not-json\n", "")
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(release_smoke_mutations, "subprocess_runner", fake_runner)
    settings = release_smoke.DockerSmokeSettings(repo="unused")

    assert (
        release_smoke_mutations.run_mutations(settings, enabled=True, project_name="__enji_guard_release_smoke__x") == 1
    )


def test_mutation_cleanup_runs_after_repeat_safe_operations(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[list[str]] = []
    list_calls = 0
    delete_calls = 0

    def fake_runner(args: Sequence[str], *, input: str | None = None, timeout: float) -> release_smoke.CommandResult:
        del input, timeout
        nonlocal list_calls
        nonlocal delete_calls
        calls.append(list(args))
        if "project" in args and "list" in args:
            list_calls += 1
            output = (
                '{"projects":[{"name":"__enji_guard_release_smoke__x"}]}\n' if list_calls == 2 else '{"projects":[]}\n'
            )
            return release_smoke.CommandResult(0, output, "")
        if "project" in args and "create" in args:
            state = "created" if sum("create" in item for item in calls) == 1 else "already_present"
            return release_smoke.CommandResult(0, f'{{"state":"{state}"}}\n', "")
        if "delete" in args:
            delete_calls += 1
            state = "deleted" if delete_calls == 1 else "already_absent"
            return release_smoke.CommandResult(0, f'{{"state":"{state}"}}\n', "")
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(release_smoke_mutations, "subprocess_runner", fake_runner)
    settings = release_smoke.DockerSmokeSettings(repo="unused")
    assert (
        release_smoke_mutations.run_mutations(settings, enabled=True, project_name="__enji_guard_release_smoke__x") == 0
    )
    assert sum("delete" in call for call in calls) == 2
    assert list_calls == 3


def test_existing_project_is_never_deleted(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[list[str]] = []

    def fake_runner(args: Sequence[str], *, input: str | None = None, timeout: float) -> release_smoke.CommandResult:
        del input, timeout
        call = list(args)
        calls.append(call)
        if "project" in call and "list" in call:
            return release_smoke.CommandResult(0, '{"projects":[{"name":"__enji_guard_release_smoke__x"}]}', "")
        return release_smoke.CommandResult(0, '{"state":"already_present"}', "")

    monkeypatch.setattr(release_smoke_mutations, "subprocess_runner", fake_runner)
    settings = release_smoke.DockerSmokeSettings(repo="unused")
    assert (
        release_smoke_mutations.run_mutations(settings, enabled=True, project_name="__enji_guard_release_smoke__x") == 1
    )
    assert not any("delete" in call for call in calls)


def test_repeat_create_must_report_idempotent_state(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[list[str]] = []
    create_calls = 0
    delete_calls = 0

    def fake_runner(args: Sequence[str], *, input: str | None = None, timeout: float) -> release_smoke.CommandResult:
        del input, timeout
        nonlocal create_calls, delete_calls
        call = list(args)
        calls.append(call)
        if "project" in call and "list" in call:
            listed = len([item for item in calls if "project" in item and "list" in item]) == 2
            output = '{"projects":[{"name":"__enji_guard_release_smoke__x"}]}\n' if listed else '{"projects":[]}\n'
            return release_smoke.CommandResult(0, output, "")
        if "project" in call and "create" in call:
            create_calls += 1
            state = "created"
            return release_smoke.CommandResult(0, f'{{"state":"{state}"}}', "")
        if "project" in call and "delete" in call:
            delete_calls += 1
            state = "deleted" if delete_calls == 1 else "already_absent"
            return release_smoke.CommandResult(0, f'{{"state":"{state}"}}', "")
        raise AssertionError(f"unexpected command: {call}")

    monkeypatch.setattr(release_smoke_mutations, "subprocess_runner", fake_runner)
    settings = release_smoke.DockerSmokeSettings(repo="unused")
    assert (
        release_smoke_mutations.run_mutations(settings, enabled=True, project_name="__enji_guard_release_smoke__x")
        == release_smoke_mutations.EXIT_CLEANUP
    )
    assert delete_calls == 2


def test_cleanup_rejects_deleted_twice_and_still_reads_back(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[list[str]] = []
    create_calls = 0
    delete_calls = 0

    def fake_runner(args: Sequence[str], *, input: str | None = None, timeout: float) -> release_smoke.CommandResult:
        del input, timeout
        nonlocal create_calls, delete_calls
        call = list(args)
        calls.append(call)
        if "project" in call and "list" in call:
            listed = len([item for item in calls if "project" in item and "list" in item]) == 2
            output = '{"projects":[{"name":"__enji_guard_release_smoke__x"}]}\n' if listed else '{"projects":[]}\n'
            return release_smoke.CommandResult(0, output, "")
        if "project" in call and "create" in call:
            create_calls += 1
            state = "created" if create_calls == 1 else "already_present"
            return release_smoke.CommandResult(0, f'{{"state":"{state}"}}', "")
        if "project" in call and "delete" in call:
            delete_calls += 1
            return release_smoke.CommandResult(0, '{"state":"deleted"}', "")
        raise AssertionError(f"unexpected command: {call}")

    monkeypatch.setattr(release_smoke_mutations, "subprocess_runner", fake_runner)
    settings = release_smoke.DockerSmokeSettings(repo="unused")
    assert (
        release_smoke_mutations.run_mutations(settings, enabled=True, project_name="__enji_guard_release_smoke__x")
        == release_smoke_mutations.EXIT_CLEANUP
    )
    assert sum("list" in call for call in calls) == 3


def test_cleanup_rejects_fixture_still_present_after_delete(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[list[str]] = []
    delete_calls = 0

    def fake_runner(args: Sequence[str], *, input: str | None = None, timeout: float) -> release_smoke.CommandResult:
        del input, timeout
        nonlocal delete_calls
        call = list(args)
        calls.append(call)
        if "project" in call and "list" in call:
            list_calls = len([item for item in calls if "project" in item and "list" in item])
            output = (
                '{"projects":[{"name":"__enji_guard_release_smoke__x"}]}\n' if list_calls >= 2 else '{"projects":[]}\n'
            )
            return release_smoke.CommandResult(0, output, "")
        if "project" in call and "create" in call:
            create_calls = len([item for item in calls if "project" in item and "create" in item])
            state = "created" if create_calls == 1 else "already_present"
            return release_smoke.CommandResult(0, f'{{"state":"{state}"}}', "")
        if "project" in call and "delete" in call:
            delete_calls += 1
            state = "deleted" if delete_calls == 1 else "already_absent"
            return release_smoke.CommandResult(0, f'{{"state":"{state}"}}', "")
        raise AssertionError(f"unexpected command: {call}")

    monkeypatch.setattr(release_smoke_mutations, "subprocess_runner", fake_runner)
    settings = release_smoke.DockerSmokeSettings(repo="unused")
    assert (
        release_smoke_mutations.run_mutations(settings, enabled=True, project_name="__enji_guard_release_smoke__x")
        == release_smoke_mutations.EXIT_CLEANUP
    )
