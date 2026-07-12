import importlib.util
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast


class _Classification(Protocol):
    requires_checks: bool
    changed_files: tuple[str, ...]


class _ClassifierModule(Protocol):
    changed_files_between: Callable[..., tuple[str, ...]]
    classify_changed_files: Callable[..., _Classification]
    has_usable_base_sha: Callable[[str], bool]
    is_documentation_path: Callable[[str], bool]
    main: Callable[[list[str] | None], int]


def _load_classifier() -> _ClassifierModule:
    script_path = Path(__file__).parents[1] / "scripts" / "classify_changed_files.py"
    spec = importlib.util.spec_from_file_location("classify_changed_files", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {script_path}")
    module = cast(ModuleType, importlib.util.module_from_spec(spec))
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return cast(_ClassifierModule, module)


_classifier = _load_classifier()
changed_files_between = _classifier.changed_files_between
classify_changed_files = _classifier.classify_changed_files
has_usable_base_sha = _classifier.has_usable_base_sha
is_documentation_path = _classifier.is_documentation_path
main = _classifier.main


def test_documentation_policy_preserves_current_paths() -> None:
    documentation_paths = (
        ".planning/phase.md",
        ".planning/generated/report.json",
        "docs/api.md",
        "README.md",
        "AGENTS.md",
        "CHANGELOG.md",
        "ROADMAP.md",
        "SECURITY.md",
        "nested/reference.md",
    )

    assert all(is_documentation_path(path) for path in documentation_paths)


def test_non_documentation_paths_require_checks() -> None:
    assert not is_documentation_path(".github/workflows/ci.yml")
    assert not is_documentation_path("docs")
    assert not is_documentation_path("README.MD")


def test_empty_diff_requires_checks() -> None:
    classification = classify_changed_files("pull_request", ())

    assert classification.requires_checks
    assert classification.changed_files == ()


def test_docs_only_diff_skips_checks() -> None:
    classification = classify_changed_files("pull_request", ("docs/api.md", ".planning/state.json"))

    assert not classification.requires_checks


def test_mixed_diff_requires_checks() -> None:
    classification = classify_changed_files("pull_request", ("docs/api.md", "src/enji_guard_cli/core.py"))

    assert classification.requires_checks


def test_force_event_requires_checks_even_for_docs_only_diff() -> None:
    classification = classify_changed_files("schedule", ("README.md",), {"schedule"})

    assert classification.requires_checks


def test_missing_or_zero_base_sha_is_not_usable() -> None:
    assert not has_usable_base_sha("")
    assert not has_usable_base_sha("0000")
    assert not has_usable_base_sha("  0000  ")
    assert has_usable_base_sha("base")


def test_changed_files_between_discards_only_empty_lines() -> None:
    def git(args: Sequence[str]) -> str:
        assert args == ("diff", "--name-only", "base", "head")
        return "README.md\n\ndocker-compose.yml\n"

    assert changed_files_between("head", "base", git=git) == ("README.md", "docker-compose.yml")


def test_cli_writes_a_clean_github_output_assignment(tmp_path: Path) -> None:
    output_file = tmp_path / "github-output"

    assert (
        main(
            [
                "--event-name",
                "schedule",
                "--force-event",
                "schedule",
                "--output-key",
                "requires-code-checks",
                "--output-file",
                str(output_file),
            ]
        )
        == 0
    )

    assert output_file.read_text(encoding="utf-8") == "requires-code-checks=true\n"


def test_cli_uses_git_for_an_empty_diff(tmp_path: Path) -> None:
    output_file = tmp_path / "github-output"

    assert (
        main(
            [
                "--event-name",
                "pull_request",
                "--base-sha",
                "HEAD",
                "--head-sha",
                "HEAD",
                "--output-key",
                "requires-code-checks",
                "--output-file",
                str(output_file),
            ]
        )
        == 0
    )

    assert output_file.read_text(encoding="utf-8") == "requires-code-checks=true\n"


def test_cli_requires_checks_without_a_base_sha(tmp_path: Path) -> None:
    output_file = tmp_path / "github-output"

    assert (
        main(
            [
                "--event-name",
                "push",
                "--base-sha",
                "0000000000000000000000000000000000000000",
                "--head-sha",
                "not-a-real-commit",
                "--output-key",
                "requires-code-checks",
                "--output-file",
                str(output_file),
            ]
        )
        == 0
    )

    assert output_file.read_text(encoding="utf-8") == "requires-code-checks=true\n"


def test_cli_rejects_output_key_injection(tmp_path: Path) -> None:
    output_file = tmp_path / "github-output"

    try:
        main(
            [
                "--event-name",
                "schedule",
                "--force-event",
                "schedule",
                "--output-key",
                "bad\nkey",
                "--output-file",
                str(output_file),
            ]
        )
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("invalid output key was accepted")
