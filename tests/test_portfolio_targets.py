from __future__ import annotations

from pathlib import Path

from data.portfolio_targets import apply_portfolio_target, get_portfolio_target, load_portfolio_targets


def test_load_portfolio_targets_converts_fraction_weights_to_percent(tmp_path: Path) -> None:
    path = tmp_path / "portfolio_targets.yaml"
    path.write_text(
        """
NVDA:
  role: core
  target_weight: 0.45
  max_weight: 0.50
  max_shares: 500
  notes: Core AI platform
""",
        encoding="utf-8",
    )

    target = load_portfolio_targets(path)["NVDA"]

    assert target.target_weight == 45.0
    assert target.max_weight == 50.0
    assert target.role == "core"
    assert target.max_shares == 500


def test_missing_portfolio_target_uses_conservative_default(tmp_path: Path) -> None:
    target = get_portfolio_target("ZZZ", tmp_path / "missing.yaml")

    assert target.target_weight == 2.0
    assert target.max_weight == 4.0
    assert target.role == "watch_only"


def test_now_default_target_is_ai_software_core() -> None:
    target = get_portfolio_target("NOW")

    assert target.target_weight == 12.0
    assert target.max_weight == 16.0
    assert target.role == "ai_software_core"


def test_nvda_default_target_is_ai_core() -> None:
    target = get_portfolio_target("NVDA")

    assert target.target_weight == 45.0
    assert target.max_weight == 52.0
    assert target.role == "ai_core"


def test_apply_portfolio_target_uses_config_targets_over_existing_limits(tmp_path: Path) -> None:
    path = tmp_path / "portfolio_targets.yaml"
    path.write_text(
        """
NVDA:
  role: core
  target_weight: 0.45
  max_weight: 0.50
""",
        encoding="utf-8",
    )

    context = apply_portfolio_target(
        "NVDA",
        {"portfolio_weight": 42.2, "target_weight": 40.0},
        config_path=path,
    )

    assert context["portfolio_weight"] == 42.2
    assert context["target_weight"] == 45.0
    assert context["max_weight"] == 50.0
    assert context["role"] == "core"
