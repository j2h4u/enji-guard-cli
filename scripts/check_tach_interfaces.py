#!/usr/bin/env -S uv run python

"""Fail when a declared `expose` pattern in tach.toml matches nothing.

`exact = true` makes tach reject a `depends_on` edge that no import uses, so a
dependency cannot rot in the config unnoticed.  Interface patterns get no such
treatment: an `expose` entry that stopped matching anything stays declared, and
silently keeps a wider public surface open than the code needs.

That is the same class of defect `exact` exists to prevent, on the other half
of the model, so this closes it.  A pattern is dead when no import that the
interface actually governs matches it.

A second rule covers what the first cannot see: `expose = [".*"]` never matches
nothing, so a catch-all can never be dead.  Outside the composition root it
defeats the interface entirely, and is rejected on sight.

Read-only.  Reports every finding rather than the first, so one run gives the
whole cleanup.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

SOURCE_ROOT = Path("src")
PACKAGE = "enji_guard_cli"
CONFIG_PATH = Path("tach.toml")
CATCH_ALL = ".*"
# Wiring is the one consumer entitled to reach concretes: that is what a
# composition root is for.  Everywhere else a catch-all defeats the interface,
# and it cannot be caught by the dead-pattern rule below because `.*` always
# matches something.
CATCH_ALL_OWNER = f"{PACKAGE}.composition"


@dataclass(frozen=True, slots=True)
class Import:
    """One name a module imports from another top-level module of the package."""

    consumer: str
    target: str
    path: str


def module_paths(config: dict[str, object]) -> tuple[str, ...]:
    """Return declared module paths, longest first.

    Modules are not all one level deep -- `delivery.cli` and `delivery.mcp` are
    siblings under a package that is not itself a module -- so ownership has to
    come from the declared graph rather than from counting dots.
    """

    modules = config.get("modules", [])
    assert isinstance(modules, list)
    paths: list[str] = []
    for module in modules:
        assert isinstance(module, dict)
        paths.append(str(module["path"]))
    return tuple(sorted(paths, key=len, reverse=True))


def _owning_module(dotted: str, modules: tuple[str, ...]) -> str | None:
    return next((path for path in modules if dotted == path or dotted.startswith(f"{path}.")), None)


def _dotted_name(path: Path) -> str:
    parts = path.relative_to(SOURCE_ROOT).parts
    return ".".join((*parts[:-1], parts[-1].removesuffix(".py")))


def _imports(modules: tuple[str, ...]) -> Iterator[Import]:
    for file in sorted((SOURCE_ROOT / PACKAGE).rglob("*.py")):
        consumer = _owning_module(_dotted_name(file), modules)
        if consumer is None:
            continue
        try:
            tree = ast.parse(file.read_text(encoding="utf-8"))
        except SyntaxError as exc:  # pragma: no cover - a parse error fails the gate elsewhere
            raise SystemExit(f"{file}: {exc}") from exc
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.module is None:
                continue
            target = _owning_module(node.module, modules)
            if target is None or target == consumer:
                continue
            submodule = node.module[len(target) :].lstrip(".")
            for alias in node.names:
                yield Import(consumer, target, f"{submodule}.{alias.name}" if submodule else alias.name)


def _governs(visibility: list[str], consumer: str) -> bool:
    return any(re.fullmatch(entry.replace("*", ".*"), consumer) for entry in visibility)


def dead_patterns(config: dict[str, object], imports: list[Import]) -> list[tuple[str, list[str], str]]:
    """Return every (module, visibility, pattern) whose pattern matches no governed import."""

    interfaces = config.get("interfaces", [])
    assert isinstance(interfaces, list)
    dead: list[tuple[str, list[str], str]] = []
    for interface in interfaces:
        assert isinstance(interface, dict)
        target = interface["from"][0]
        visibility = interface.get("visibility", ["*"])
        reachable = [item.path for item in imports if item.target == target and _governs(visibility, item.consumer)]
        dead.extend(
            (target, visibility, pattern)
            for pattern in interface.get("expose", [])
            if not any(re.fullmatch(pattern, path) for path in reachable)
        )
    return dead


def stray_catch_alls(config: dict[str, object]) -> list[tuple[str, list[str]]]:
    """Return every interface outside wiring that exposes everything."""

    interfaces = config.get("interfaces", [])
    assert isinstance(interfaces, list)
    stray: list[tuple[str, list[str]]] = []
    for interface in interfaces:
        assert isinstance(interface, dict)
        if CATCH_ALL not in interface.get("expose", []):
            continue
        visibility = interface.get("visibility", ["*"])
        if visibility != [CATCH_ALL_OWNER]:
            stray.append((interface["from"][0], visibility))
    return stray


def main() -> int:
    config = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    modules = module_paths(config)
    dead = dead_patterns(config, list(_imports(modules)))
    stray = stray_catch_alls(config)
    if not dead and not stray:
        print("✅ Every tach interface pattern is exercised by a real import.")
        return 0
    for target, visibility, pattern in dead:
        print(f"  {target} -> {', '.join(visibility)}: expose {pattern!r} matches nothing", file=sys.stderr)
    if dead:
        print("Remove the pattern, or narrow it to what consumers import.", file=sys.stderr)
    for target, visibility in stray:
        print(f"  {target} -> {', '.join(visibility)}: expose {CATCH_ALL!r} defeats the interface", file=sys.stderr)
    if stray:
        print(f"Only {CATCH_ALL_OWNER} may reach concretes; list what the consumer needs.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
