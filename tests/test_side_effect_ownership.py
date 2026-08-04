"""Pin which modules may own a side effect.

`tach` governs imports.  It cannot see that a module started writing files,
reading the environment, or opening a socket, because those are calls, not
edges.  Today each capability has a small and deliberate owner set; nothing
kept it that way.

These are exact owner lists, not counts.  A new owner fails, and so does a
stale entry: if a module stops owning a capability, the list must shrink with
it, or it would keep advertising permission nobody uses -- the same defect the
tach interface gate exists to prevent.
"""

from __future__ import annotations

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "src" / "enji_guard_cli"

# Writing to disk.  Each owner persists its own state and nothing else's:
# `atomic_json` is the shared primitive, and the other three own the ledger,
# the credential store and the telemetry log respectively.
FILESYSTEM_WRITERS = frozenset(
    {
        "atomic_json.py",
        "audit/ledger.py",
        "auth_session/credential_changes.py",
        "auth_session/store.py",
        "runtime_observability/telemetry_sink.py",
    }
)

# Runtime tuning belongs in frozen settings, never in the environment.
# Credential ingress and test detection inside telemetry are the exceptions.
ENVIRONMENT_READERS = frozenset({"auth_session/api_key.py", "runtime_observability/telemetry.py"})

# Only the transport and the client it hands to the gateway may name httpx.
HTTP_CLIENT_OWNERS = frozenset({"enji_gateway/shared_client.py", "transport.py"})

# The CLI binds a health listener and must check the address first.
SOCKET_OWNERS = frozenset({"delivery/cli/app.py"})

# Advancing durable credential state.  `decisions.md` gives this to the
# supervisor alone: gateway, status, readiness and MCP are observers, and an
# observer that mutates is the defect this list exists to catch.  It really
# `store.py` is excluded: it is the
# primitive these owners are built from, not a caller.
CREDENTIAL_STATE_MUTATORS = frozenset({"auth_session/api.py", "auth_session/coordinator.py"})

_MUTATING_NAMES = frozenset(
    {
        "cas_replace_cookie",
        "delete_journal",
        "enqueue_outcome",
        "import_credential",
        "write_auth_file",
        "write_journal",
    }
)

# Unambiguous path-writing methods only.  `replace` and `rename` are left out
# on purpose: `str.replace` is everywhere, and matching it would make this
# gate cry wolf until someone widened the list to silence it.
_WRITE_METHODS = frozenset({"write_text", "write_bytes", "mkdir", "touch", "unlink"})
_OS_WRITE_FUNCTIONS = frozenset({"replace", "rename", "remove", "makedirs"})


def _sources() -> list[tuple[str, ast.Module]]:
    return [
        (str(path.relative_to(SOURCE)), ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        for path in sorted(SOURCE.rglob("*.py"))
    ]


def _imports(tree: ast.Module) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[0])
    return found


def _writes_to_disk(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "open":
                return True
            if isinstance(func, ast.Attribute) and func.attr in _WRITE_METHODS:
                return True
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "os"
                and func.attr in _OS_WRITE_FUNCTIONS
            ):
                return True
    return "fcntl" in _imports(tree)


def _referenced_names(tree: ast.Module) -> set[str]:
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}


def _reads_environment(tree: ast.Module) -> bool:
    return any(isinstance(node, ast.Attribute) and node.attr in {"environ", "getenv"} for node in ast.walk(tree))


def _owners(predicate: object) -> frozenset[str]:
    assert callable(predicate)
    return frozenset(name for name, tree in _sources() if predicate(tree))


def _assert_exact(capability: str, actual: frozenset[str], declared: frozenset[str]) -> None:
    new = sorted(actual - declared)
    stale = sorted(declared - actual)
    assert not new, f"{capability}: undeclared owner(s) {new}. Justify the ownership, then add it here."
    assert not stale, f"{capability}: {stale} no longer own this. Remove the stale entry."


def test_only_declared_modules_write_to_disk() -> None:
    _assert_exact("filesystem writes", _owners(_writes_to_disk), FILESYSTEM_WRITERS)


def test_only_declared_modules_read_the_environment() -> None:
    _assert_exact("environment reads", _owners(_reads_environment), ENVIRONMENT_READERS)


def test_only_declared_modules_name_the_http_client() -> None:
    _assert_exact("raw httpx use", _owners(lambda tree: "httpx" in _imports(tree)), HTTP_CLIENT_OWNERS)


def test_only_declared_modules_open_sockets() -> None:
    _assert_exact("socket use", _owners(lambda tree: "socket" in _imports(tree)), SOCKET_OWNERS)


def test_only_declared_modules_advance_credential_state() -> None:
    _assert_exact(
        "credential state mutation",
        _owners(lambda tree: bool(_MUTATING_NAMES & _referenced_names(tree))) - {"auth_session/store.py"},
        CREDENTIAL_STATE_MUTATORS,
    )


def test_no_module_spawns_a_process() -> None:
    # Zero-owner policy: this product shells out to nothing.  A failure here
    # means a boundary that has not been designed yet, so the fix is an owned
    # seam rather than the first entry in this list.
    spawners = _owners(lambda tree: bool({"subprocess", "multiprocessing"} & _imports(tree)))

    assert spawners == frozenset(), f"process spawning appeared in {sorted(spawners)}"
