#!/usr/bin/env python3
"""Build-artifact contract for the dependency-light CLI and optional MCP service.

The contract runs only against the supplied wheel: its two virtual environments
are created outside the checkout and every smoke command starts from an empty
directory with ``PYTHONPATH`` removed.  It intentionally uses only the Python
standard library so the artifact gate does not accidentally test this checkout's
development environment.
"""

from __future__ import annotations

import argparse
import configparser
import os
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from collections.abc import Sequence
from email.parser import Parser
from pathlib import Path
from typing import Final, cast

EXPECTED_ENTRYPOINTS: Final = {
    "enji-guard": "enji_guard_cli.delivery.cli:app",
    "enji-guard-service": "enji_guard_cli.delivery.service:app",
}
EXPECTED_MCP_REQUIREMENT: Final = "mcp[cli]==1.28.1"
EXPECTED_MCP_VERSION: Final = "1.28.1"
EXPECTED_MCP_TOOLS: Final = ("enji_portfolio_overview", "enji_repo_audits")
WHEEL_LEAK_COMPONENTS: Final = frozenset({"tests", ".planning", "__pycache__", ".git", "src"})


class ContractError(RuntimeError):
    """An artifact does not satisfy the public distribution contract."""


def _run(command: Sequence[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(  # noqa: S603 - argument vector is executed without a shell.
        command, cwd=cwd, env=env, text=True, capture_output=True, check=False
    )
    if result.returncode != 0:
        rendered = " ".join(command)
        raise ContractError(f"command failed ({result.returncode}): {rendered}\n{result.stdout}{result.stderr}")
    return result


def _isolated_environment(home: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment["HOME"] = str(home)
    environment["PYTHONPATH"] = ""
    environment["PYTHONSAFEPATH"] = "1"
    environment.pop("VIRTUAL_ENV", None)
    return environment


def _one_artifact(dist_dir: Path, pattern: str, label: str) -> Path:
    matches = sorted(dist_dir.glob(pattern))
    if len(matches) != 1:
        raise ContractError(f"expected exactly one {label}; found {[path.name for path in matches]}")
    return matches[0]


def _artifacts(dist_dir: Path) -> tuple[Path, Path]:
    if not dist_dir.is_dir():
        raise ContractError(f"artifact directory does not exist: {dist_dir}")
    wheel = _one_artifact(dist_dir, "*.whl", "wheel")
    sdist = _one_artifact(dist_dir, "*.tar.gz", "sdist")
    contents = {path for path in dist_dir.iterdir() if path.is_file()}
    if contents != {wheel, sdist}:
        raise ContractError(
            f"artifact directory must contain only one wheel and one sdist; found {[p.name for p in contents]}"
        )
    if not tarfile.is_tarfile(sdist):
        raise ContractError(f"sdist is not a readable tar archive: {sdist.name}")
    return wheel, sdist


def _wheel_metadata(wheel: Path) -> tuple[dict[str, str], tuple[str, ...], tuple[str, ...]]:
    with zipfile.ZipFile(wheel) as archive:
        names = tuple(archive.namelist())
        leaked = [
            name
            for name in names
            if any(component in WHEEL_LEAK_COMPONENTS for component in Path(name).parts)
            or name.endswith((".pyc", ".pyo"))
        ]
        if leaked:
            raise ContractError(f"wheel contains source-tree or cache leakage: {leaked}")
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        entrypoint_names = [name for name in names if name.endswith(".dist-info/entry_points.txt")]
        if len(metadata_names) != 1 or len(entrypoint_names) != 1:
            raise ContractError("wheel must contain exactly one METADATA and entry_points.txt")
        metadata = Parser().parsestr(archive.read(metadata_names[0]).decode("utf-8"))
        entries = _entrypoints(archive.read(entrypoint_names[0]).decode("utf-8"))
    return entries, tuple(metadata.get_all("Requires-Dist", ())), tuple(metadata.get_all("Provides-Extra", ()))


def _entrypoints(raw: str) -> dict[str, str]:
    parser = configparser.ConfigParser()
    parser.read_string(raw)
    if parser.sections() != ["console_scripts"]:
        raise ContractError(f"unexpected wheel entrypoint sections: {parser.sections()}")
    return dict(parser.items("console_scripts"))


def _assert_metadata(wheel: Path) -> None:
    entrypoints, requirements, provided_extras = _wheel_metadata(wheel)
    if entrypoints != EXPECTED_ENTRYPOINTS:
        raise ContractError(f"wheel console entrypoints drifted: {entrypoints}")
    if provided_extras != ("mcp",):
        raise ContractError(f"wheel must provide exactly the mcp extra, found {provided_extras}")
    mcp_requirements = [requirement for requirement in requirements if requirement.casefold().startswith("mcp[")]
    if len(mcp_requirements) != 1:
        raise ContractError(f"wheel must contain one conditional MCP requirement, found {mcp_requirements}")
    normalized = mcp_requirements[0].replace(" ", "").replace('"', "'")
    if normalized != "mcp[cli]==1.28.1;extra=='mcp'":
        raise ContractError(f"wheel MCP requirement must remain {EXPECTED_MCP_REQUIREMENT} behind the mcp extra")


def _venv_python(root: Path, name: str, *, cwd: Path, env: dict[str, str]) -> Path:
    venv = root / name
    _run((sys.executable, "-m", "venv", str(venv)), cwd=cwd, env=env)
    python = venv / "bin" / "python"
    version = _run(
        (str(python), "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"), cwd=cwd, env=env
    )
    if version.stdout.strip() != "3.14":
        raise ContractError(f"artifact contract requires Python 3.14, got {version.stdout.strip()}")
    return python


def _install(python: Path, requirement: str, *, cwd: Path, env: dict[str, str]) -> None:
    _run((str(python), "-m", "pip", "install", "--disable-pip-version-check", requirement), cwd=cwd, env=env)


def _base_contract(python: Path, wheel: Path, *, cwd: Path, env: dict[str, str]) -> None:
    _install(python, str(wheel), cwd=cwd, env=env)
    _run(
        (
            str(python),
            "-I",
            "-c",
            "import importlib.util\n"
            "import sys\n"
            "assert importlib.util.find_spec('mcp') is None\n"
            "from enji_guard_cli.client import EnjiGuardClient\n"
            "from enji_guard_cli.delivery.cli import app\n"
            "assert app is not None\n"
            "assert not any(name == 'mcp' or name.startswith('mcp.') for name in sys.modules)\n"
            "with EnjiGuardClient():\n"
            "    pass\n",
        ),
        cwd=cwd,
        env=env,
    )
    scripts = python.parent
    _run((str(scripts / "enji-guard"), "--help"), cwd=cwd, env=env)
    _run((str(scripts / "enji-guard"), "--version"), cwd=cwd, env=env)
    health = _run((str(scripts / "enji-guard"), "health", "--json"), cwd=cwd, env=env)
    if health.stdout.strip() != '{\n  "status": "ok"\n}':
        raise ContractError(f"base wheel health --json output drifted: {health.stdout!r}")


def _mcp_contract(python: Path, wheel: Path, *, cwd: Path, env: dict[str, str]) -> None:
    _install(python, f"{wheel}[mcp]", cwd=cwd, env=env)
    _run(
        (
            str(python),
            "-I",
            "-c",
            "import asyncio, importlib.util; "
            "from importlib.metadata import version; "
            "assert importlib.util.find_spec('mcp') is not None; "
            f"assert version('mcp') == {EXPECTED_MCP_VERSION!r}; "
            "from enji_guard_cli.delivery.mcp.server import create_mcp_server; "
            "names = tuple(sorted(tool.name for tool in asyncio.run(create_mcp_server().list_tools()))); "
            f"assert names == {EXPECTED_MCP_TOOLS!r}, names",
        ),
        cwd=cwd,
        env=env,
    )
    _run((str(python.parent / "enji-guard-service"), "--help"), cwd=cwd, env=env)


def run_contract(dist_dir: Path) -> None:
    wheel, _sdist = _artifacts(dist_dir)
    _assert_metadata(wheel)
    print("PASS wheel metadata and contents", flush=True)
    with tempfile.TemporaryDirectory(prefix="enji-guard-package-contract-") as temporary:
        root = Path(temporary)
        cwd = root / "empty"
        home = root / "home"
        cwd.mkdir()
        home.mkdir()
        env = _isolated_environment(home)
        base_python = _venv_python(root, "base", cwd=cwd, env=env)
        print("CHECK isolated base-wheel install", flush=True)
        _base_contract(base_python, wheel, cwd=cwd, env=env)
        print("PASS isolated base-wheel install", flush=True)
        mcp_python = _venv_python(root, "mcp", cwd=cwd, env=env)
        print("CHECK isolated MCP-extra install", flush=True)
        _mcp_contract(mcp_python, wheel, cwd=cwd, env=env)
        print("PASS isolated MCP-extra install", flush=True)
    print("PASS package artifact contract")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist_dir", type=Path, help="Directory containing exactly one wheel and one sdist")
    args = parser.parse_args(argv)
    try:
        run_contract(cast(Path, args.dist_dir))
    except ContractError as error:
        print(f"FAIL package artifact contract: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
