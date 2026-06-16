from __future__ import annotations

from data.buy_plan_alerts import (
    ALERT_ACTIVE,
    ALERT_CANCELLED,
    ALERT_TRIGGERED,
    BuyPlanAlertStore,
    TRIGGER_AFTER_HOURS,
    TRIGGER_LAST_CLOSE,
    TRIGGER_PRE_MARKET,
    buy_plan_alert_message,
    buy_plan_alert_table_label,
)


def test_buy_plan_alert_save_and_overwrite_keeps_single_active_alert(tmp_path) -> None:
    store = BuyPlanAlertStore(tmp_path / "cache.sqlite")

    first = store.save_alert("orcl", 185, 50, "观察区下沿")
    second = store.save_alert("ORCL", 180, 40, "更低价提醒")

    alerts = store.list_alerts(["ORCL"])
    assert len(alerts) == 1
    assert first["symbol"] == "ORCL"
    assert second["planned_buy_price"] == 180
    assert second["planned_buy_shares"] == 40
    assert second["note"] == "更低价提醒"
    assert second["status"] == ALERT_ACTIVE
    assert buy_plan_alert_table_label(second) == "买入提醒 $180.00"


def test_buy_plan_alert_triggers_once(tmp_path) -> None:
    store = BuyPlanAlertStore(tmp_path / "cache.sqlite")
    store.save_alert("ORCL", 185, 50)

    triggered = store.check_and_update("ORCL", 184.9)
    again = store.check_and_update("ORCL", 184.5)

    assert triggered is not None
    assert triggered["status"] == ALERT_TRIGGERED
    assert triggered["just_triggered"] is True
    assert again is not None
    assert again["status"] == ALERT_TRIGGERED
    assert again["just_triggered"] is False
    assert buy_plan_alert_message(triggered, 184.9) == "ORCL 已到达计划买入价：当前 $184.90，计划 $185.00，提醒买入 50 股。"


def test_buy_plan_alert_records_trigger_source_and_uses_session_copy(tmp_path) -> None:
    store = BuyPlanAlertStore(tmp_path / "cache.sqlite")
    store.save_alert("ORCL", 185, 50)

    triggered = store.check_and_update("ORCL", 184.9, trigger_source=TRIGGER_LAST_CLOSE)

    assert triggered is not None
    assert triggered["status"] == ALERT_TRIGGERED
    assert triggered["trigger_source"] == TRIGGER_LAST_CLOSE
    assert buy_plan_alert_message(triggered, 184.9) == "ORCL 昨夜收盘已到达计划买入价：收盘 $184.90，计划 $185.00，今晚开盘重点观察。"


def test_buy_plan_alert_pre_and_after_hours_messages_are_reference_only(tmp_path) -> None:
    store = BuyPlanAlertStore(tmp_path / "cache.sqlite")
    pre = store.save_alert("ORCL", 185, 50, current_price=184.9, trigger_source=TRIGGER_PRE_MARKET)
    after = store.save_alert("NVDA", 190, 10, current_price=189.5, trigger_source=TRIGGER_AFTER_HOURS)

    assert buy_plan_alert_message(pre, 184.9) == "ORCL 盘前价格已到计划价，但盘前流动性较低，建议等待开盘确认。"
    assert buy_plan_alert_message(after, 189.5) == "NVDA 盘后价格已到计划价，建议次日开盘确认。"


def test_buy_plan_alert_cancel_hides_from_active_lookup(tmp_path) -> None:
    store = BuyPlanAlertStore(tmp_path / "cache.sqlite")
    store.save_alert("ORCL", 185, 50)

    cancelled = store.cancel_alert("ORCL")

    assert cancelled["status"] == ALERT_CANCELLED
    assert store.get_alert("ORCL") is None
    assert store.get_alert("ORCL", include_cancelled=True)["status"] == ALERT_CANCELLED
