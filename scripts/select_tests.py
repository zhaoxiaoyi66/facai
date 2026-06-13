"""Select targeted pytest commands from changed files.

This helper is intentionally conservative: it maps common project areas to the
smallest useful regression set, while keeping release and trading profiles
available for explicit escalation.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


@dataclass(frozen=True, order=True)
class PytestTarget:
    path: str
    keyword: str | None = None


def target(path: str, keyword: str | None = None) -> PytestTarget:
    return PytestTarget(path=path, keyword=keyword)


TARGETED_MAPPING: tuple[tuple[str, tuple[PytestTarget, ...]], ...] = (
    (
        "ui/dashboard.py",
        (
            target("tests/test_dashboard_freshness.py"),
            target("tests/test_macro_regime.py"),
            target("tests/test_core_logic.py", "dashboard"),
        ),
    ),
    (
        "ui/dashboard_tables.py",
        (
            target("tests/test_entry_display.py"),
            target("tests/test_core_logic.py", "dashboard"),
        ),
    ),
    (
        "ui/dashboard_drawer.py",
        (
            target("tests/test_entry_display.py"),
            target("tests/test_structure_entry.py"),
            target("tests/test_pullback_acceptance.py"),
            target("tests/test_core_logic.py", "dashboard"),
        ),
    ),
    (
        "ui/ai_stock_radar.py",
        (
            target("tests/test_ai_stock_radar.py"),
            target("tests/test_entry_display.py"),
        ),
    ),
    (
        "data/ai_stock_radar.py",
        (
            target("tests/test_ai_stock_radar.py"),
            target("tests/test_entry_display.py"),
            target("tests/test_core_logic.py", "Scoring"),
        ),
    ),
    (
        "data/entry_display.py",
        (
            target("tests/test_entry_display.py"),
            target("tests/test_ai_stock_radar.py"),
        ),
    ),
    (
        "data/macro_regime.py",
        (
            target("tests/test_macro_regime.py"),
            target("tests/test_core_logic.py", "macro"),
        ),
    ),
    (
        "data/macro_sources.py",
        (
            target("tests/test_macro_regime.py"),
            target("tests/test_core_logic.py", "macro"),
        ),
    ),
    (
        "data/fear_greed_provider.py",
        (
            target("tests/test_macro_regime.py"),
            target("tests/test_core_logic.py", "macro"),
        ),
    ),
    (
        "data/structure_entry.py",
        (
            target("tests/test_structure_entry.py"),
            target("tests/test_portfolio_trade_entry.py"),
        ),
    ),
    (
        "data/pullback_acceptance.py",
        (
            target("tests/test_pullback_acceptance.py"),
            target("tests/test_portfolio_trade_entry.py"),
        ),
    ),
    (
        "data/buy_execution_context.py",
        (
            target("tests/test_buy_execution_context.py"),
            target("tests/test_portfolio_trade_entry.py"),
        ),
    ),
    (
        "data/portfolio_trade_entry.py",
        (
            target("tests/test_portfolio_trade_entry.py"),
            target("tests/test_decision_log.py"),
        ),
    ),
    (
        "data/portfolio_trade_sync.py",
        (
            target("tests/test_portfolio_trade_sync.py"),
            target("tests/test_trade_performance.py"),
        ),
    ),
    (
        "ui/trade_journal.py",
        (
            target("tests/test_trade_journal_ui.py"),
            target("tests/test_sell_review.py"),
            target("tests/test_trade_performance.py"),
        ),
    ),
    (
        "ui/portfolio.py",
        (
            target("tests/test_portfolio_trade_entry.py"),
            target("tests/test_portfolio_model.py"),
            target("tests/test_core_logic.py", "portfolio"),
        ),
    ),
    (
        "data/price_alerts.py",
        (
            target("tests/test_price_alerts.py"),
            target("tests/test_buy_plan.py"),
        ),
    ),
    (
        "data/buy_plan.py",
        (
            target("tests/test_buy_plan.py"),
            target("tests/test_price_alerts.py"),
            target("tests/test_portfolio_trade_entry.py"),
        ),
    ),
    (
        "scripts/select_tests.py",
        (target("tests/test_test_selection.py"),),
    ),
)


TRADING_WORKFLOW_TARGETS: tuple[PytestTarget, ...] = (
    target("tests/test_portfolio_trade_entry.py"),
    target("tests/test_portfolio_trade_sync.py"),
    target("tests/test_decision_log.py"),
    target("tests/test_trade_journal_ui.py"),
    target("tests/test_trade_performance.py"),
    target("tests/test_sell_review.py"),
)


FULL_CORE_TARGETS: tuple[PytestTarget, ...] = (
    target("tests/test_core_logic.py"),
    target("tests/test_ai_stock_radar.py"),
    target("tests/test_portfolio_trade_entry.py"),
    target("tests/test_portfolio_trade_sync.py"),
    target("tests/test_decision_log.py"),
    target("tests/test_trade_journal_ui.py"),
    target("tests/test_trade_performance.py"),
    target("tests/test_sell_review.py"),
    target("tests/test_macro_regime.py"),
    target("tests/test_refresh_policy.py"),
)


PROFILE_TARGETS: dict[str, tuple[PytestTarget, ...]] = {
    "no-test": (),
    "trading": TRADING_WORKFLOW_TARGETS,
    "release": FULL_CORE_TARGETS,
}


def normalize_path(path: str | Path) -> str:
    return str(path).replace("\\", "/").lstrip("./").lower()


def _matches(changed_file: str, pattern: str) -> bool:
    normalized = normalize_path(changed_file)
    normalized_pattern = normalize_path(pattern)
    return normalized == normalized_pattern


def unique_targets(targets: Iterable[PytestTarget]) -> list[PytestTarget]:
    seen: set[PytestTarget] = set()
    ordered: list[PytestTarget] = []
    for item in targets:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def select_test_targets(changed_files: Sequence[str]) -> list[PytestTarget]:
    selected: list[PytestTarget] = []
    for changed_file in changed_files:
        normalized = normalize_path(changed_file)
        if normalized.startswith("tests/test_") and normalized.endswith(".py"):
            selected.append(target(normalized))
            continue
        for pattern, targets in TARGETED_MAPPING:
            if _matches(normalized, pattern):
                selected.extend(targets)
    return unique_targets(selected)


def render_pytest_commands(
    targets: Sequence[PytestTarget],
    *,
    python_executable: str = "python",
    quiet: bool = True,
) -> list[list[str]]:
    no_keyword = [item.path for item in targets if item.keyword is None]
    commands: list[list[str]] = []
    if no_keyword:
        command = [python_executable, "-m", "pytest", *no_keyword]
        if quiet:
            command.append("-q")
        commands.append(command)

    for item in targets:
        if item.keyword is None:
            continue
        command = [python_executable, "-m", "pytest", item.path, "-k", item.keyword]
        if quiet:
            command.append("-q")
        commands.append(command)
    return commands


def changed_files_from_git(base: str = "HEAD") -> list[str]:
    commands = (
        ["git", "diff", "--name-only", base],
        ["git", "diff", "--name-only", "--cached"],
    )
    files: list[str] = []
    for command in commands:
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        if result.returncode != 0:
            continue
        files.extend(line.strip() for line in result.stdout.splitlines() if line.strip())
    return list(dict.fromkeys(files))


def _quote_arg(arg: str) -> str:
    if " " not in arg and "\t" not in arg:
        return arg
    return f'"{arg}"'


def format_command(command: Sequence[str]) -> str:
    return " ".join(_quote_arg(part) for part in command)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*", help="Changed files. Defaults to git diff.")
    parser.add_argument("--base", default="HEAD", help="Git diff base when files are omitted.")
    parser.add_argument(
        "--profile",
        choices=("targeted", "no-test", "trading", "release"),
        default="targeted",
        help="Test profile to render.",
    )
    parser.add_argument("--python", default="python", help="Python executable for rendered commands.")
    parser.add_argument("--no-quiet", action="store_true", help="Do not add -q to pytest commands.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    changed_files = list(args.files) or changed_files_from_git(args.base)

    if args.profile == "targeted":
        targets = select_test_targets(changed_files)
    else:
        targets = list(PROFILE_TARGETS[args.profile])

    print("Changed files:")
    if changed_files:
        for file_path in changed_files:
            print(f"- {file_path}")
    else:
        print("- none detected")

    print(f"\nProfile: {args.profile}")
    if not targets:
        print("No targeted tests selected. Use no-test audit for docs/read-only work, or choose a wider profile.")
        return 0

    print("\nRecommended commands:")
    for command in render_pytest_commands(targets, python_executable=args.python, quiet=not args.no_quiet):
        print(format_command(command))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
