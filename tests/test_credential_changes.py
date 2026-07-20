import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest
from watchfiles import Change

import enji_guard_cli.auth_session.credential_changes as changes_module


def test_credential_watcher_filters_unrelated_directory_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth_file = tmp_path / "auth.json"
    captured: dict[str, object] = {}

    async def fake_awatch(
        *paths: Path,
        watch_filter: Callable[[Change, str], bool] | None = None,
        **kwargs: object,
    ) -> AsyncIterator[set[tuple[Change, str]]]:
        captured["paths"] = paths
        captured["watch_filter"] = watch_filter
        captured["kwargs"] = kwargs
        assert watch_filter is not None
        assert watch_filter(Change.modified, str(tmp_path / "telemetry.jsonl")) is False
        assert watch_filter(Change.modified, str(auth_file)) is True
        yield {(Change.modified, str(auth_file))}

    monkeypatch.setattr(changes_module, "awatch", fake_awatch)

    async def consume() -> None:
        async for _ in changes_module.credential_changes(auth_file):
            break

    asyncio.run(consume())
    assert captured["paths"] == (tmp_path,)
    assert captured["kwargs"] == {"recursive": False}


def test_credential_watcher_observes_atomic_replacement(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    replacement = tmp_path / "auth.json.tmp"
    auth_file.write_text("old", encoding="utf-8")

    async def replace_and_wait() -> None:
        async def observe_one_change() -> None:
            async for _ in changes_module.credential_changes(auth_file):
                return

        observer = asyncio.create_task(observe_one_change())
        await asyncio.sleep(0.2)
        replacement.write_text("new", encoding="utf-8")
        replacement.replace(auth_file)
        await asyncio.wait_for(observer, timeout=5)

    asyncio.run(replace_and_wait())
