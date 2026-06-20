from __future__ import annotations

import inspect

from ui import manual_review


def test_manual_review_missing_placeholders_are_localized() -> None:
    source = inspect.getsource(manual_review)

    assert '"N/A"' not in source
    assert "'N/A'" not in source
    assert manual_review._review_display_text(None) == "待补"
    assert manual_review._review_display_text("N/A") == "待补"
    assert manual_review._review_display_text("none", "未记录") == "未记录"
    assert manual_review._review_has_display_text("N/A") is False
    assert manual_review._review_row_source_meta({}) == "待补 · 待补"
    assert manual_review._review_source_display({}) == "待补"
