from __future__ import annotations

import pandas as pd

from ui.price_source_display import price_source_label, price_source_label_from_row


def test_price_source_label_uses_explicit_last_close_session() -> None:
    label, detail = price_source_label(
        {
            "price_session": "LAST_CLOSE",
            "price_as_of": "2026-06-16",
            "last_close_synced_at": "2026-06-17T12:00:00+00:00",
        }
    )

    assert label == "昨夜收盘 06/16"
    assert "数据日期：2026-06-16" in detail
    assert "刷新时间：06/17 20:00 HKT" in detail


def test_price_source_label_uses_history_close_fallback_date() -> None:
    label, detail = price_source_label(
        {
            "priceSource": "price_history",
            "historyLatestDate": "2026-06-16",
            "fetchedAt": "2026-06-17T02:15:00+00:00",
        }
    )

    assert label == "收盘价 06/16"
    assert "数据日期：2026-06-16" in detail
    assert "刷新时间：06/17 10:15 HKT" in detail


def test_price_source_label_uses_price_only_refresh_mode() -> None:
    label, detail = price_source_label(
        {
            "refresh_mode": "PRICE_ONLY",
            "quote_updated_at": "2026-06-17T12:00:00+00:00",
            "market_session_at_refresh": "AFTER_HOURS",
        }
    )

    assert label == "最新报价 06/17 20:00"
    assert "刷新时段：美股盘后" in detail
    assert "刷新时间：06/17 20:00 HKT" in detail


def test_price_source_label_from_row_reads_raw_snapshot_first() -> None:
    row = pd.Series(
        {
            "price_session": "REGULAR",
            "rawSnapshot": {
                "price_session": "PRE_MARKET",
                "quote_updated_at": "2026-06-17T11:00:00+00:00",
            },
        }
    )

    label, detail = price_source_label_from_row(row)

    assert label == "盘前参考 06/17 19:00"
    assert "刷新时间：06/17 19:00 HKT" in detail
