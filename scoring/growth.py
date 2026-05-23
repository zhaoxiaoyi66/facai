from __future__ import annotations

def calculate_growth_score(snapshot: dict) -> float:
    score = 0.0

    # Growth rule: revenue growth above 15% gets full credit; negative growth gets none.
    revenue_growth = snapshot.get("forward_revenue_growth")
    if revenue_growth is None:
        revenue_growth = snapshot.get("revenue_growth")
    score += _score_range(revenue_growth, good=0.15, weak=0.0, max_points=10)

    # Growth rule: earnings growth supports multiple durability, with conservative missing-data credit.
    score += _score_range(snapshot.get("earnings_growth"), good=0.15, weak=0.0, max_points=6)

    # Growth rule: free-cash-flow growth matters because buy-zone sizing is cash-flow sensitive.
    score += _score_range(snapshot.get("free_cash_flow_growth"), good=0.15, weak=0.0, max_points=4)

    return round(min(score, 20), 1)


def _score_range(value: float | None, good: float, weak: float, max_points: float) -> float:
    if value is None:
        return max_points * 0.4
    if value <= weak:
        return 0.0
    if value >= good:
        return max_points
    return max_points * ((value - weak) / (good - weak))
