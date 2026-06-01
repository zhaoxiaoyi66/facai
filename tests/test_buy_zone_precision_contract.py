from __future__ import annotations

from buy_zone_engine import BuyZoneEstimate, attach_combined_entry, validate_buy_zone_estimate


def _zone(current_price: float, current_zone: str = "tranche_buy", validation_errors: list[str] | None = None) -> BuyZoneEstimate:
    return BuyZoneEstimate(
        "TEST",
        "GENERIC",
        current_price,
        130,
        105,
        120,
        90,
        100,
        70,
        current_zone,
        "high",
        "blended",
        ["P/FCF", "P/S"],
        [],
        [],
        "now",
        validationErrors=validation_errors or [],
    )


def test_precision_contract_allows_precise_fields_for_valid_tranche_zone() -> None:
    zone = validate_buy_zone_estimate(_zone(95))

    contract = zone.precisionContract

    assert contract["canShowPreciseBuyZone"] is True
    assert contract["blockedReasons"] == []
    assert "trancheBuyHigh" in contract["allowedPriceFields"]
    assert "nextTriggerPrice" in contract["allowedPriceFields"]
    assert contract["blockedPriceFields"] == []


def test_precision_contract_blocks_no_chase_even_when_reference_prices_exist() -> None:
    zone = validate_buy_zone_estimate(_zone(140, "no_chase"))

    contract = zone.precisionContract

    assert zone.currentZone == "no_chase"
    assert contract["canShowPreciseBuyZone"] is False
    assert "zone:no_chase" in contract["blockedReasons"]
    assert "trancheBuyHigh" in contract["blockedPriceFields"]


def test_precision_contract_keeps_fair_observation_separate_from_entry_prices() -> None:
    zone = validate_buy_zone_estimate(_zone(110, "fair_observation"))

    contract = zone.precisionContract

    assert zone.currentZone == "fair_observation"
    assert contract["canShowPreciseBuyZone"] is False
    assert contract["canShowObservationRange"] is True
    assert contract["allowedPriceFields"] == ["noChaseAbove", "fairValueLow", "fairValueHigh"]
    assert "trancheBuyHigh" in contract["blockedPriceFields"]
    assert "fair_observation_not_entry" in contract["blockedReasons"]


def test_combined_entry_hides_blocked_precise_prices_for_fair_observation() -> None:
    zone = attach_combined_entry(validate_buy_zone_estimate(_zone(110, "fair_observation")))

    combined = zone.combinedEntry

    assert combined["valuationEntryPrice"] is None
    assert combined["valuationDiscountPrice"] is None
    assert combined["combinedTriggerPrice"] is None
    assert combined["deepDiscountPrice"] is None


def test_precision_contract_blocks_all_prices_for_data_insufficient() -> None:
    zone = validate_buy_zone_estimate(
        BuyZoneEstimate(
            "TEST",
            "GENERIC",
            None,
            130,
            105,
            120,
            90,
            100,
            70,
            "fair_observation",
            "high",
            "blended",
            ["P/FCF", "P/S"],
            [],
            [],
            "now",
        )
    )

    contract = zone.precisionContract

    assert zone.currentZone == "data_insufficient"
    assert contract["canShowPreciseBuyZone"] is False
    assert "noChaseAbove" in contract["blockedPriceFields"]
    assert zone.trancheBuyHigh is None


def test_precision_contract_can_block_heavy_buy_without_blocking_tranche_reference() -> None:
    zone = validate_buy_zone_estimate(
        _zone(
            95,
            validation_errors=["ai_cloud_infra_no_heavy_buy_without_positive_fcf_and_capex_discipline"],
        )
    )

    contract = zone.precisionContract

    assert zone.currentZone == "tranche_buy"
    assert contract["canShowPreciseBuyZone"] is True
    assert "trancheBuyHigh" in contract["allowedPriceFields"]
    assert "heavyBuyBelow" not in contract["allowedPriceFields"]
    assert "heavyBuyBelow" in contract["blockedPriceFields"]
    assert contract["heavyBuyBlockedReasons"]


def test_combined_entry_keeps_tranche_reference_when_only_heavy_is_blocked() -> None:
    zone = attach_combined_entry(
        validate_buy_zone_estimate(
            _zone(
                95,
                validation_errors=["ai_cloud_infra_no_heavy_buy_without_positive_fcf_and_capex_discipline"],
            )
        )
    )

    combined = zone.combinedEntry

    assert combined["valuationEntryPrice"] == 100
    assert combined["valuationDiscountPrice"] == 100
    assert combined["combinedTriggerPrice"] == 100
    assert combined["deepDiscountPrice"] is None
