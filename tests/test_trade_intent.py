from __future__ import annotations

import inspect
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from data.trade_intent import (
    BUY_BEHAVIOR_OPTIONS,
    BUY_INTENT_QUESTIONS,
    SELL_INTENT_QUESTIONS,
    SELL_BEHAVIOR_OPTIONS,
    STOCK_STAGE_OPTIONS,
    TradeIntentStore,
    build_trade_intent_review_stats,
    normalize_trade_intent_payload,
)
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
                "stock_stage_self_judgment": STOCK_STAGE_OPTIONS[1],
                "trade_behavior_self_judgment": BUY_BEHAVIOR_OPTIONS[1],
                "core_direction_intent": "是，在加强核心方向",
                "objective_reason_intent": "承接变好 / 回到买区 / 赔率合适",
                "drawdown_plan_intent": "有，已想好持有、加仓或止错计划",
                "tracking_commitment_intent": "愿意，后续会持续跟踪和复盘",
                "portfolio_clarity_intent": "会，更聚焦于核心方向",
            },
        )

        assert saved["symbol"] == "NVDA"
        assert saved["intent_side"] == "buy"
        assert saved["stock_stage_self_judgment"] == "市场重新定价 / 事件催化"
        assert saved["trade_behavior_self_judgment"] == "右侧事件买入：事件确认后，顺着资金重新定价买入"
        assert saved["primary_intent"] == "是，在加强核心方向"
        assert store.get_intent_for_trade(10)["payload"]["objective_reason_intent"] == "承接变好 / 回到买区 / 赔率合适"
        with closing(sqlite3.connect(path)) as conn:
            row = conn.execute(
                "SELECT question_1_answer, question_6_answer, attention_flags_json, stock_stage_self_judgment, trade_behavior_self_judgment FROM trade_intent_reviews WHERE trade_id = 10"
            ).fetchone()
        assert row[0] == "是，在加强核心方向"
        assert row[1] is None
        assert row[2] == "[]"
        assert row[3] == "市场重新定价 / 事件催化"
        assert row[4].startswith("右侧事件买入")


def test_trade_intent_stats_count_review_attention_and_snapshots() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "intent.sqlite"
        store = TradeIntentStore(path)
        buy_payload = {
            "intent_side": "buy",
            "stock_stage_self_judgment": STOCK_STAGE_OPTIONS[-1],
            "trade_behavior_self_judgment": BUY_BEHAVIOR_OPTIONS[3],
            BUY_INTENT_QUESTIONS[0]["field"]: BUY_INTENT_QUESTIONS[0]["options"][1],
            BUY_INTENT_QUESTIONS[1]["field"]: BUY_INTENT_QUESTIONS[1]["options"][1],
            BUY_INTENT_QUESTIONS[2]["field"]: BUY_INTENT_QUESTIONS[2]["options"][2],
            BUY_INTENT_QUESTIONS[3]["field"]: BUY_INTENT_QUESTIONS[3]["options"][0],
            BUY_INTENT_QUESTIONS[4]["field"]: BUY_INTENT_QUESTIONS[4]["options"][0],
        }
        sell_payload = {
            "intent_side": "sell",
            "stock_stage_self_judgment": STOCK_STAGE_OPTIONS[4],
            "trade_behavior_self_judgment": SELL_BEHAVIOR_OPTIONS[4],
            SELL_INTENT_QUESTIONS[0]["field"]: SELL_INTENT_QUESTIONS[0]["options"][1],
            SELL_INTENT_QUESTIONS[1]["field"]: SELL_INTENT_QUESTIONS[1]["options"][0],
            SELL_INTENT_QUESTIONS[2]["field"]: SELL_INTENT_QUESTIONS[2]["options"][2],
            SELL_INTENT_QUESTIONS[3]["field"]: SELL_INTENT_QUESTIONS[3]["options"][2],
            SELL_INTENT_QUESTIONS[4]["field"]: SELL_INTENT_QUESTIONS[4]["options"][1],
            SELL_INTENT_QUESTIONS[5]["field"]: SELL_INTENT_QUESTIONS[5]["options"][0],
        }
        store.save_intent(
            20,
            "NVDA",
            "buy",
            buy_payload,
            snapshots={"setup_score": 64, "volume_acceptance_score": 45},
        )
        store.save_intent(21, "ADBE", "sell", sell_payload)
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("UPDATE trade_intent_reviews SET created_at = ?, updated_at = ?", ("2026-06-15T10:00:00+08:00", "2026-06-15T10:00:00+08:00"))
            conn.commit()

        stats = build_trade_intent_review_stats(
            [
                {"id": 20, "trade_date": "2026-06-15", "action_type": "buy"},
                {"id": 21, "trade_date": "2026-06-15", "action_type": "sell"},
                {"id": 22, "trade_date": "2026-05-01", "action_type": "buy"},
            ],
            store.list_intents(),
            current_date="2026-06-16",
        )

        thirty = stats["thirty_days"]
        flags = thirty["attention_flag_counts"]
        assert stats["seven_days"]["trade_count"] == 2
        assert thirty["trade_count"] == 2
        assert thirty["attention_trade_count"] == 2
        assert thirty["buy_review_count"] == 1
        assert thirty["sell_review_count"] == 1
        assert flags["新增小仓风险"] == 1
        assert flags["股票阶段不清"] == 1
        assert flags["追涨 / 怕错过风险"] == 1
        assert flags["情绪卖出风险"] == 1
        assert flags["怕错过风险"] == 1
        assert flags["无下跌预案"] == 1
        assert flags["临时卖出风险"] == 1
        assert flags["卖出比例未想清楚"] == 1
        assert flags["资金安排不清"] == 1
        assert flags["无回补预案"] == 1
        assert thirty["low_setup_buy_count"] == 1
        assert thirty["low_volume_acceptance_buy_count"] == 1
        assert thirty["stock_stage_counts"]["还没想清楚"] == 1
        assert thirty["stock_stage_counts"]["破位退潮 / 逻辑受损"] == 1
        assert thirty["buy_behavior_counts"][BUY_BEHAVIOR_OPTIONS[3]] == 1
        assert thirty["sell_behavior_counts"][SELL_BEHAVIOR_OPTIONS[4]] == 1


def test_trade_intent_store_persists_sell_pre_trade_choices() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "intent.sqlite"
        store = TradeIntentStore(path)

        saved = store.save_intent(
            11,
            "adbe",
            "sell",
            {
                "intent_side": "sell",
                "stock_stage_self_judgment": STOCK_STAGE_OPTIONS[2],
                "trade_behavior_self_judgment": SELL_BEHAVIOR_OPTIONS[0],
                "sell_reason_intent": "计划内止盈 / 止错 / 减仓 / 仓位控制",
                "sell_basis_intent": "基本面变差 / 技术破位 / 估值极端 / 仓位过重",
                "sell_size_intent": "只减一部分，保留核心仓或观察仓",
                "capital_plan_intent": "提高现金 / 降低风险 / 等更好买点",
                "rebound_plan_intent": "有明确回补条件，或者明确接受不回补",
                "portfolio_clarity_after_sell_intent": "会，减少噪音、降低风险或让仓位更聚焦",
            },
        )

        assert saved["symbol"] == "ADBE"
        assert saved["intent_side"] == "sell"
        assert saved["stock_stage_self_judgment"] == "快速重估 / 主升加速"
        assert saved["trade_behavior_self_judgment"].startswith("计划止盈")
        assert saved["primary_intent"] == "计划内止盈 / 止错 / 减仓 / 仓位控制"
        assert store.get_intent_for_trade(11)["payload"]["rebound_plan_intent"] == "有明确回补条件，或者明确接受不回补"
        with closing(sqlite3.connect(path)) as conn:
            row = conn.execute(
                "SELECT question_3_answer, question_6_answer, attention_flags_json FROM trade_intent_reviews WHERE trade_id = 11"
            ).fetchone()
        assert row[0] == "只减一部分，保留核心仓或观察仓"
        assert row[1] == "会，减少噪音、降低风险或让仓位更聚焦"
        assert row[2] == "[]"


def test_trade_intent_normalization_uses_chinese_choice_defaults() -> None:
    payload = normalize_trade_intent_payload({"intent_side": "sell", "primary_intent": "invalid"})

    assert payload["intent_side"] == "sell"
    assert payload["primary_intent"] == "还没想清楚"
    assert payload["position_intent"] == "还没想清楚卖多少"
    assert payload["sell_reason_intent"] == "还没想清楚"


def test_sell_intent_defaults_to_not_clear_and_flags_attention_points() -> None:
    payload = normalize_trade_intent_payload({"intent_side": "sell"})

    assert payload["sell_reason_intent"] == "还没想清楚"
    assert payload["sell_basis_intent"] == "还没想清楚"
    assert payload["sell_size_intent"] == "还没想清楚卖多少"
    assert payload["capital_plan_intent"] == "还没想清楚"
    assert payload["rebound_plan_intent"] == "还没想清楚"
    assert payload["portfolio_clarity_after_sell_intent"] == "还没想清楚"
    assert "临时卖出风险" in payload["attention_points"]
    assert "卖出依据不清" in payload["attention_points"]
    assert "卖出比例未想清楚" in payload["attention_points"]
    assert "资金安排不清" in payload["attention_points"]
    assert "无回补预案" in payload["attention_points"]
    assert "卖出后组合不清晰" in payload["attention_points"]


def test_sell_full_exit_is_not_automatically_attention_flagged() -> None:
    payload = normalize_trade_intent_payload(
        {
            "intent_side": "sell",
            "sell_reason_intent": "计划内止盈 / 止错 / 减仓 / 仓位控制",
            "sell_basis_intent": "基本面变差 / 技术破位 / 估值极端 / 仓位过重",
            "sell_size_intent": "全部卖出，暂时退出这只股票",
            "capital_plan_intent": "提高现金 / 降低风险 / 等更好买点",
            "rebound_plan_intent": "有明确回补条件，或者明确接受不回补",
            "portfolio_clarity_after_sell_intent": "会，减少噪音、降低风险或让仓位更聚焦",
        }
    )

    assert payload["attention_points"] == "[]"


def test_buy_intent_defaults_to_not_clear_and_flags_attention_points() -> None:
    payload = normalize_trade_intent_payload(
        {
            "intent_side": "buy",
            "stock_stage_self_judgment": STOCK_STAGE_OPTIONS[-1],
            "trade_behavior_self_judgment": BUY_BEHAVIOR_OPTIONS[-1],
        }
    )

    assert payload["stock_stage_self_judgment"] == "还没想清楚"
    assert payload["trade_behavior_self_judgment"] == "还没想清楚"
    assert payload["core_direction_intent"] == "还没想清楚"
    assert payload["objective_reason_intent"] == "还没想清楚"
    assert payload["drawdown_plan_intent"] == "还没想清楚"
    assert payload["tracking_commitment_intent"] == "还没想清楚"
    assert payload["portfolio_clarity_intent"] == "还没想清楚"
    assert "新增小仓风险" in payload["attention_points"]
    assert "股票阶段不清" in payload["attention_points"]
    assert "买入行为不清" in payload["attention_points"]
    assert "怕错过风险" in payload["attention_points"]
    assert "无下跌预案" in payload["attention_points"]
    assert "长期跟踪不足" in payload["attention_points"]
    assert "组合碎片化风险" in payload["attention_points"]


def test_trade_intent_bodies_use_only_radio_choices_with_not_clear_default() -> None:
    buy_source = inspect.getsource(trade_intent._render_buy_intent_body)
    sell_source = inspect.getsource(trade_intent._render_sell_intent_body)
    label_source = inspect.getsource(trade_intent._render_trade_label_section)

    for source in (buy_source, sell_source):
        assert "st.radio" in source
        assert "index=2" in source
        assert "text_input" not in source
        assert "text_area" not in source
        assert "checkbox" not in source
        assert "selectbox" not in source
        assert "multiselect" not in source
    assert "stock_stage_self_judgment" in label_source
    assert "trade_behavior_self_judgment" in label_source
    assert "index=len(STOCK_STAGE_OPTIONS) - 1" in label_source


def test_trade_intent_dialog_copy_has_no_gate_or_pass_fail_wording() -> None:
    source = inspect.getsource(trade_intent.render_trade_intent_dialog)

    assert trade_intent.intent_title("buy") == "买入前记录"
    assert trade_intent.intent_title("sell") == "卖出前记录"
    assert "买入前先记录这笔交易的原因" in source
    assert "卖出前先记录这笔交易的原因" in source
    assert "本次买入意图将随交易记录保存，用于日后复盘" in inspect.getsource(trade_intent._render_buy_intent_body)
    assert "本次卖出意图将随交易记录保存，用于日后复盘" in inspect.getsource(trade_intent._render_sell_intent_body)
    assert "本次记录存在复盘关注点" in inspect.getsource(trade_intent._render_buy_intent_body)
    assert "本次记录存在复盘关注点" in inspect.getsource(trade_intent._render_sell_intent_body)
    assert "自我判断：股票当前阶段" in inspect.getsource(trade_intent._render_trade_label_section)
    assert "自我判断：本次买入行为类型" in inspect.getsource(trade_intent._render_buy_intent_body)
    assert "自我判断：本次卖出行为类型" in inspect.getsource(trade_intent._render_sell_intent_body)
    assert "确认并记录" in inspect.getsource(trade_intent._render_buy_intent_body)
    assert "返回修改" in inspect.getsource(trade_intent._render_sell_intent_body)
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


def test_trade_intent_record_html_shows_review_snapshots_and_attention_points() -> None:
    intent = {
        "intent_side": "buy",
        "stock_stage_self_judgment": "市场重新定价 / 事件催化",
        "trade_behavior_self_judgment": "右侧事件买入：事件确认后，顺着资金重新定价买入",
        "payload": {
            "stock_stage_self_judgment": "市场重新定价 / 事件催化",
            "trade_behavior_self_judgment": "右侧事件买入：事件确认后，顺着资金重新定价买入",
            "core_direction_intent": BUY_INTENT_QUESTIONS[0]["options"][1],
            "objective_reason_intent": BUY_INTENT_QUESTIONS[1]["options"][1],
            "drawdown_plan_intent": BUY_INTENT_QUESTIONS[2]["options"][0],
            "tracking_commitment_intent": BUY_INTENT_QUESTIONS[3]["options"][0],
            "portfolio_clarity_intent": BUY_INTENT_QUESTIONS[4]["options"][0],
        },
        "attention_flags": ["新增小仓风险", "怕错过风险"],
        "setup_score_snapshot": 64,
        "technical_structure_score_snapshot": 70,
        "volume_acceptance_score_snapshot": 48,
        "risk_reward_score_snapshot": 72,
    }

    html = trade_intent.intent_record_html(intent)

    assert "交易意图记录" in html
    assert "买入前记录" in html
    assert "股票当前阶段" in html
    assert "市场重新定价 / 事件催化" in html
    assert "本次交易行为" in html
    assert "右侧事件买入" in html
    assert "复盘关注点" in html
    assert "新增小仓风险" in html
    assert "当时 Setup 评分" in html
    assert "技术结构" in html
    assert "量能承接" in html
    assert "风险收益" in html
    assert "\u901a\u8fc7" not in html
    assert "\u672a\u901a\u8fc7" not in html
