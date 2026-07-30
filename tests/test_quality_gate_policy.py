import json
import re
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[1]
UNKNOWN_DIAGNOSTICS = {
    "reportUnknownArgumentType",
    "reportUnknownLambdaType",
    "reportUnknownMemberType",
    "reportUnknownParameterType",
    "reportUnknownVariableType",
}


def test_production_unknown_type_ratchet_is_scoped_and_locked() -> None:
    config = cast(dict[str, object], json.loads((ROOT / "basedpyright.production.json").read_text(encoding="utf-8")))
    baseline = cast(
        dict[str, object], json.loads((ROOT / "basedpyright.production-baseline.json").read_text(encoding="utf-8"))
    )
    justfile = (ROOT / "Justfile").read_text(encoding="utf-8")

    assert config["include"] == ["src/enji_guard_cli"]
    assert config["typeCheckingMode"] == "standard"
    assert "strict" not in config
    assert all(config[diagnostic] == "error" for diagnostic in UNKNOWN_DIAGNOSTICS)
    assert "--project basedpyright.production.json --baselinemode=lock" in justfile
    assert "basedpyright --project pyproject.toml tests --warnings" in justfile

    files = cast(dict[str, list[dict[str, object]]], baseline["files"])
    baselined_rules = {cast(str, diagnostic["code"]) for diagnostics in files.values() for diagnostic in diagnostics}
    assert baselined_rules <= UNKNOWN_DIAGNOSTICS


def test_verify_uses_one_parallel_non_docker_suite_for_coverage_and_crap() -> None:
    justfile = (ROOT / "Justfile").read_text(encoding="utf-8")
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    test_gate = re.search(r"^test-gate:\n(?P<body>(?:    .*\n)+)", justfile, flags=re.MULTILINE)
    assert test_gate is not None
    assert "pytest -q -n auto --cov=src/enji_guard_cli" in test_gate.group("body")
    assert '--cov-report=json:"$coverage_file"' in test_gate.group("body")
    assert "scripts/crap_gate.py" in test_gate.group("body")
    assert re.search(r"^verify: check test-gate docker-tests docker-build$", justfile, flags=re.MULTILINE)
    assert "run: just test-gate" in ci
    assert not re.search(r"^  crap:$", ci, flags=re.MULTILINE)
