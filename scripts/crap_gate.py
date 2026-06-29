#!/usr/bin/env -S uv run python

import argparse
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypedDict, TypeGuard, cast

from radon.complexity import cc_visit

DEFAULT_THRESHOLD = 30.0
DEFAULT_SOURCE_ROOT = Path("src/enji_guard_cli")


class _FunctionSummary(TypedDict):
    covered_lines: int
    num_statements: int


class _CoverageFunctionEntry(TypedDict):
    summary: _FunctionSummary


class _CoverageFileEntry(TypedDict):
    functions: dict[str, _CoverageFunctionEntry]


class _CoverageReport(TypedDict):
    files: dict[str, _CoverageFileEntry]


class _RadonBlock(Protocol):
    name: str
    lineno: int
    endline: int
    complexity: int


class _RadonFunctionBlock(_RadonBlock, Protocol):
    closures: Sequence[_RadonFunctionBlock]


class _RadonClassBlock(_RadonBlock, Protocol):
    inner_classes: Sequence[_RadonClassBlock]
    methods: Sequence[_RadonFunctionBlock]


class _GateArgs(Protocol):
    coverage: Path
    src: Path
    threshold: float


@dataclass(frozen=True, slots=True)
class FunctionMetric:
    key: str
    start_line: int
    complexity: int
    coverage_fraction: float
    crap: float


RadonVisit = Callable[[str], Sequence[_RadonBlock]]
_CC_VISIT: RadonVisit = cast(RadonVisit, cc_visit)


def _round_metric(value: float) -> float:
    return round(value, 6)


def _expect_dict(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(context)
    return cast(dict[str, object], value)


def _expect_str(value: object, context: str) -> str:
    if not isinstance(value, str):
        raise ValueError(context)
    return value


def _expect_int(value: object, context: str) -> int:
    if not isinstance(value, int):
        raise ValueError(context)
    return value


def _is_class_block(block: _RadonBlock) -> TypeGuard[_RadonClassBlock]:
    return hasattr(block, "methods") and hasattr(block, "inner_classes")


def _qualname_from_block(block: _RadonBlock, prefix: tuple[str, ...] = ()) -> list[tuple[str, _RadonBlock]]:
    if _is_class_block(block):
        return _class_member_qualnames(block, prefix)

    function_block = cast(_RadonFunctionBlock, block)
    qualname = ".".join((*prefix, function_block.name))
    items: list[tuple[str, _RadonBlock]] = [(qualname, function_block)]
    for closure in function_block.closures:
        items.extend(_qualname_from_block(closure, (*prefix, function_block.name)))
    return items


def _class_member_qualnames(block: _RadonClassBlock, prefix: tuple[str, ...]) -> list[tuple[str, _RadonBlock]]:
    class_prefix = (*prefix, block.name)
    items: list[tuple[str, _RadonBlock]] = []
    for inner_class in block.inner_classes:
        items.extend(_qualname_from_block(inner_class, class_prefix))
    for method in block.methods:
        items.extend(_qualname_from_block(method, class_prefix))
    return items


def _qualnames_from_blocks(blocks: Sequence[_RadonBlock]) -> list[tuple[str, _RadonBlock]]:
    items: list[tuple[str, _RadonBlock]] = []
    for block in blocks:
        items.extend(_qualname_from_block(block))
    return items


def _load_coverage_report(path: Path) -> _CoverageReport:
    raw_report = cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
    return cast(
        _CoverageReport,
        {"files": _expect_dict(raw_report.get("files"), "coverage report does not contain an object at 'files'")},
    )


def _function_metrics_from_report(coverage_report: _CoverageReport, source_root: Path) -> list[FunctionMetric]:
    source_root = source_root.resolve()
    metrics: list[FunctionMetric] = []
    for raw_path, file_data in coverage_report["files"].items():
        file_metrics = _function_metrics_from_file(raw_path, file_data, source_root)
        metrics.extend(file_metrics)
    return metrics


def _function_metrics_from_file(
    raw_path: str,
    file_data: _CoverageFileEntry,
    source_root: Path,
) -> list[FunctionMetric]:
    raw_path = _expect_str(raw_path, "coverage report file path is invalid")
    file_path = Path(raw_path).resolve()
    if source_root not in file_path.parents and file_path != source_root:
        return []

    relative_path = file_path.relative_to(source_root).as_posix()
    source_text = file_path.read_text(encoding="utf-8")
    return [
        metric
        for qualname, block in _qualnames_from_blocks(_CC_VISIT(source_text))
        if (metric := _metric_for_function(relative_path, qualname, block, file_data["functions"])) is not None
    ]


def _metric_for_function(
    relative_path: str,
    qualname: str,
    block: _RadonBlock,
    functions: dict[str, _CoverageFunctionEntry],
) -> FunctionMetric | None:
    if qualname not in functions:
        return None

    summary = functions[qualname]["summary"]
    num_statements = _expect_int(
        summary["num_statements"],
        f"coverage summary for {relative_path}::{qualname} has invalid num_statements",
    )
    covered_lines = _expect_int(
        summary["covered_lines"],
        f"coverage summary for {relative_path}::{qualname} has invalid covered_lines",
    )
    coverage_fraction = 1.0 if num_statements <= 0 else covered_lines / num_statements
    crap = (block.complexity**2) * ((1 - coverage_fraction) ** 3) + block.complexity
    return FunctionMetric(
        key=f"{relative_path}::{qualname}",
        start_line=block.lineno,
        complexity=block.complexity,
        coverage_fraction=_round_metric(coverage_fraction),
        crap=_round_metric(crap),
    )


def _format_offender(metric: FunctionMetric) -> str:
    return (
        f"{metric.key}:{metric.start_line} "
        f"CRAP {metric.crap:.2f}, complexity {metric.complexity}, coverage {metric.coverage_fraction:.1%}"
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fail if any function exceeds the configured CRAP threshold.")
    parser.add_argument("--coverage", type=Path, required=True, help="pytest-cov JSON report")
    parser.add_argument("--src", type=Path, default=DEFAULT_SOURCE_ROOT, help="source root to scan")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, help="maximum allowed CRAP per function")
    return parser


def _parse_args(argv: list[str] | None) -> _GateArgs:
    parser = _build_parser()
    return cast(_GateArgs, parser.parse_args(argv))


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    metrics = _function_metrics_from_report(_load_coverage_report(args.coverage), args.src)
    offenders = [metric for metric in metrics if metric.crap > args.threshold]
    if offenders:
        print(f"CRAP gate failed: {len(offenders)} function(s) exceed {args.threshold:.2f}")
        for metric in sorted(offenders, key=lambda item: (-item.crap, item.key))[:20]:
            print(f"  {_format_offender(metric)}")
        return 1

    print(f"CRAP gate passed: {len(metrics)} function(s), threshold {args.threshold:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
