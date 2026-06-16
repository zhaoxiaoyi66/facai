from __future__ import annotations

import inspect
from pathlib import Path
from tempfile import TemporaryDirectory

from data.trade_intent import TradeIntentStore, normalize_trade_intent_payload
from ui import trade_intent


def test_trade_intent_store_persists_pre_trade_choices() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "intent.sqlite"
        store = TradeIntentStore(path)

        saved = store.save_intent(
            10,
            "nvda",
            "buy",
            {
                "intent_side": "buy",
                "primary_intent": "计划内买入",
                "position_intent": "让组合更集中",
                "timing_intent": "量价承接改善",
                "risk_intent": "按计划执行",
            },
        )

        assert saved["symbol"] == "NVDA"
        assert saved["intent_side"] == "buy"
        assert saved["primary_intent"] == "计划内买入"
        assert store.get_intent_for_trade(10)["timing_intent"] == "量价承接改善"


def test_trade_intent_normalization_uses_chinese_choice_defaults() -> None:
    payload = normalize_trade_intent_payload({"intent_side": "sell", "primary_intent": "invalid"})

    assert payload["intent_side"] == "sell"
    assert payload["primary_intent"] == "计划内止盈"
    assert payload["position_intent"] == "降低集中度"


def test_trade_intent_dialog_copy_has_no_gate_or_pass_fail_wording() -> None:
    source = inspect.getsource(trade_intent.render_trade_intent_dialog)

    assert trade_intent.intent_title("buy") == "买入前记录"
    assert trade_intent.intent_title("sell") == "卖出前记录"
    assert "不评价对错" in source
    forbidden_terms = (
        "\u901a\u8fc7",
        "\u672a\u901a\u8fc7",
        "\u7981\u6b62\u4e70\u5165",
        "\u7981\u6b62\u5356\u51fa",
        "\u4e0d\u5141\u8bb8\u4ea4\u6613",
        "\u95e8\u7981",
    )
    for forbidden in forbidden_terms:
        assert forbidden not in source
