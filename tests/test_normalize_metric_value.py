from __future__ import annotations

from data.normalize_metric_value import normalize_metric_value
from data.review_queue_builder import _present_queue_review_value


def test_missing_metric_display_value_is_localized() -> None:
    for unit in ("percent", "x", "usd", None):
        normalized = normalize_metric_value(None, unit)

        assert normalized.normalizedValue is None
        assert normalized.displayValue == "待补"
        assert normalized.displayValue != "N/A"


def test_review_queue_treats_localized_missing_value_as_missing() -> None:
    for value in ("待补", "暂缺", "暂无"):
        assert _present_queue_review_value(value) is False
