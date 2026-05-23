from __future__ import annotations


def calculate_quality_score(snapshot: dict) -> float:
    score = 0.0

    # Quality rule: positive free cash flow supports self-funded growth.
    fcf = snapshot.get("free_cash_flow")
    score += _score_boolean(fcf is not None and fcf > 0, 6, missing=3 if fcf is None else 0)

    # Quality rule: higher operating margin indicates pricing power and operating leverage.
    score += _score_range(snapshot.get("operating_margin"), good=0.20, weak=0.05, max_points=5)

    # Quality rule: profit margin rewards profitable business models without inventing missing earnings.
    score += _score_range(snapshot.get("profit_margin"), good=0.20, weak=0.03, max_points=5)

    # Quality rule: return on equity is a compact proxy for capital efficiency when available.
    score += _score_range(snapshot.get("return_on_equity"), good=0.20, weak=0.05, max_points=5)

    # Quality rule: ROIC is better than accounting earnings for capital-heavy businesses when available.
    score += _score_range(snapshot.get("return_on_invested_capital"), good=0.15, weak=0.04, max_points=4)

    return round(min(score, 25), 1)


def _score_boolean(condition: bool, max_points: float, missing: float = 0.0) -> float:
    if condition:
        return max_points
    return missing


def _score_range(value: float | None, good: float, weak: float, max_points: float) -> float:
    if value is None:
        return max_points * 0.4
    if value <= weak:
        return 0.0
    if value >= good:
        return max_points
    return max_points * ((value - weak) / (good - weak))
