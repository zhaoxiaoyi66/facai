from __future__ import annotations

from types import SimpleNamespace

from data.decision_readiness import build_decision_readiness


def test_decision_readiness_blocks_when_price_or_final_decision_health_is_bad() -> None:
    result = build_decision_readiness(
        "NVDA",
        data_health={
            "topIssues": [
                {"category": "missing_price", "symbol": "NVDA", "message": "缺少价格"},
                {"category": "missing_history", "symbol": "MSFT", "message": "其他股票缺历史"},
            ]
        },
        final_decision=SimpleNamespace(finalAction="observe", blockReasons=[], reviewReasons=[]),
        buy_zone=SimpleNamespace(currentZone="tranche_buy", confidence="high", validationErrors=[]),
    )

    assert not result["canDecide"]
    assert not result["canShowPreciseBuyZone"]
    assert result["status"] == "blocked"
    assert [item["category"] for item in result["blockingDataReasons"]] == ["missing_price"]


def test_decision_readiness_allows_precise_buy_zone_only_for_valid_buy_zone_states() -> None:
    result = build_decision_readiness(
        "NOW",
        data_health={"topIssues": []},
        final_decision={
            "finalAction": "plan_add",
            "blockReasons": [],
            "reviewReasons": [],
        },
        buy_zone={
            "currentZone": "tranche_buy",
            "confidence": "high",
            "validationErrors": [],
        },
    )

    assert result["status"] == "ready"
    assert result["canDecide"]
    assert result["canShowPreciseBuyZone"]


def test_decision_readiness_blocks_fake_precision_for_no_chase_or_data_insufficient() -> None:
    result = build_decision_readiness(
        "CRWV",
        data_health={"topIssues": []},
        final_decision=SimpleNamespace(finalAction="watch", blockReasons=[], reviewReasons=["data_confidence"]),
        buy_zone=SimpleNamespace(
            currentZone="data_insufficient",
            confidence="low",
            validationErrors=["ai_cloud_infra_missing_core_inputs"],
        ),
    )

    assert result["canDecide"]
    assert not result["canShowPreciseBuyZone"]
    assert result["status"] == "review_required"
    assert result["precisionBlockedReasons"][0]["category"] == "buy_zone_precision_blocked"
    assert any(item["category"] == "ai_cloud_infra_missing_core_inputs" for item in result["reviewRequiredReasons"])


def test_decision_readiness_includes_trade_sync_policy_without_recomputing_it() -> None:
    result = build_decision_readiness(
        "NVDA",
        data_health={"topIssues": []},
        final_decision=SimpleNamespace(finalAction="record_violation", blockReasons=[], reviewReasons=[]),
        buy_zone=SimpleNamespace(currentZone="tranche_buy", confidence="high", validationErrors=[]),
        sync_policy={"canSync": False, "reason": "纪律门禁 BLOCK，禁止同步到组合持仓。"},
    )

    assert not result["canSyncTrade"]
    assert any(item["category"] == "trade_sync_blocked" for item in result["blockingDataReasons"])
