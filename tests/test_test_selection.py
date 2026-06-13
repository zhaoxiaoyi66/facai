from scripts.select_tests import PytestTarget, format_command, render_pytest_commands, select_test_targets


def test_dashboard_change_selects_dashboard_macro_and_core_dashboard_tests() -> None:
    targets = select_test_targets(["ui/dashboard.py"])

    assert PytestTarget("tests/test_dashboard_freshness.py") in targets
    assert PytestTarget("tests/test_core_logic.py", "dashboard") in targets
    assert PytestTarget("tests/test_macro_regime.py") not in targets


def test_macro_change_selects_macro_and_core_macro_tests() -> None:
    targets = select_test_targets(["data/macro_regime.py"])

    assert targets == [
        PytestTarget("tests/test_macro_regime.py"),
        PytestTarget("tests/test_core_logic.py", "macro"),
    ]


def test_portfolio_ui_change_selects_entry_model_and_core_portfolio_tests() -> None:
    targets = select_test_targets(["ui/portfolio.py"])

    assert PytestTarget("tests/test_portfolio_trade_entry.py") in targets
    assert PytestTarget("tests/test_portfolio_model.py") in targets
    assert PytestTarget("tests/test_core_logic.py", "portfolio") not in targets


def test_structure_and_acceptance_changes_do_not_force_trading_workflow_tests() -> None:
    assert select_test_targets(["data/structure_entry.py"]) == [
        PytestTarget("tests/test_structure_entry.py")
    ]
    assert select_test_targets(["data/pullback_acceptance.py"]) == [
        PytestTarget("tests/test_pullback_acceptance.py")
    ]


def test_changed_test_file_selects_itself() -> None:
    assert select_test_targets(["tests/test_refresh_policy.py"]) == [
        PytestTarget("tests/test_refresh_policy.py")
    ]


def test_optional_docs_change_does_not_force_tests() -> None:
    assert select_test_targets(["docs/testing_policy.md"]) == []


def test_keyword_targets_render_as_separate_pytest_commands() -> None:
    commands = render_pytest_commands(
        [
            PytestTarget("tests/test_dashboard_freshness.py"),
            PytestTarget("tests/test_core_logic.py", "dashboard"),
        ],
        python_executable="python",
    )

    assert commands == [
        ["python", "-m", "pytest", "tests/test_dashboard_freshness.py", "-q"],
        ["python", "-m", "pytest", "tests/test_core_logic.py", "-k", "dashboard", "-q"],
    ]


def test_format_command_keeps_keyword_expression_readable() -> None:
    command = ["python", "-m", "pytest", "tests/test_core_logic.py", "-k", "DashboardLayoutTests"]

    assert format_command(command) == "python -m pytest tests/test_core_logic.py -k DashboardLayoutTests"
