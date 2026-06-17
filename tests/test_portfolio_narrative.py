from pathlib import Path

from data.portfolio_narrative import DEFAULT_PORTFOLIO_NARRATIVE, PortfolioNarrativeStore


def test_portfolio_narrative_defaults(tmp_path: Path) -> None:
    store = PortfolioNarrativeStore(tmp_path / "portfolio.sqlite")

    narrative = store.get_narrative()

    assert narrative["main_thesis"] == DEFAULT_PORTFOLIO_NARRATIVE["main_thesis"]
    assert narrative["portfolio_mapping"] == DEFAULT_PORTFOLIO_NARRATIVE["portfolio_mapping"]


def test_portfolio_narrative_save_and_reset(tmp_path: Path) -> None:
    store = PortfolioNarrativeStore(tmp_path / "portfolio.sqlite")

    saved = store.save_narrative(
        {
            "main_thesis": "自定义主判断",
            "first_half_title": "上半场",
            "first_half_body": "上半场说明",
            "second_half_title": "下半场",
            "second_half_body": "下半场说明",
            "portfolio_mapping": "NVDA / NOW",
        }
    )

    assert saved["main_thesis"] == "自定义主判断"
    assert store.get_narrative()["portfolio_mapping"] == "NVDA / NOW"

    reset = store.reset_default()

    assert reset["main_thesis"] == DEFAULT_PORTFOLIO_NARRATIVE["main_thesis"]
