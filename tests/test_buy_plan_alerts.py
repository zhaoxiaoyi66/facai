from __future__ import annotations

from data.buy_plan_alerts import (
    ALERT_ACTIVE,
    ALERT_CANCELLED,
    ALERT_TRIGGERED,
    BuyPlanAlertStore,
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


def test_buy_plan_alert_cancel_hides_from_active_lookup(tmp_path) -> None:
    store = BuyPlanAlertStore(tmp_path / "cache.sqlite")
    store.save_alert("ORCL", 185, 50)

    cancelled = store.cancel_alert("ORCL")

    assert cancelled["status"] == ALERT_CANCELLED
    assert store.get_alert("ORCL") is None
    assert store.get_alert("ORCL", include_cancelled=True)["status"] == ALERT_CANCELLED
