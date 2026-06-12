from __future__ import annotations

from datetime import datetime, timezone

from data.fear_greed_provider import CNN_FEAR_GREED_GRAPH_URL, FEAR_GREED_PACKAGE_MODULES, fetch_cnn_fear_greed


def test_cnn_fear_greed_graphdata_payload_parses_current_reading() -> None:
    reading = fetch_cnn_fear_greed(
        fetcher=lambda url: {
            "fear_and_greed": {
                "score": 34,
                "rating": "fear",
                "timestamp": "2026-06-10T20:00:00+00:00",
            }
        },
        now=datetime(2026, 6, 10, 21, tzinfo=timezone.utc),
    )

    assert reading.value == 34
    assert reading.rating == "fear"
    assert reading.observation_date == "2026-06-10"
    assert reading.source == "CNN Fear & Greed graphdata"


def test_cnn_fear_greed_graphdata_payload_uses_latest_historical_row() -> None:
    reading = fetch_cnn_fear_greed(
        fetcher=lambda url: {
            "fear_and_greed_historical": {
                "data": [
                    {"x": 1781131200000, "y": 29, "rating": "fear"},
                    {"x": 1781217600000, "y": 41, "rating": "neutral"},
                ]
            }
        },
        now=datetime(2026, 6, 11, 21, tzinfo=timezone.utc),
    )

    assert reading.value == 41
    assert reading.observation_date == "2026-06-11"
    assert "graphdata" in CNN_FEAR_GREED_GRAPH_URL
    assert "crypto" not in CNN_FEAR_GREED_GRAPH_URL.lower()


def test_cnn_fear_greed_graphdata_failure_uses_optional_package() -> None:
    def failing_graphdata(_url: str):
        raise RuntimeError("CNN HTTP 418")

    reading = fetch_cnn_fear_greed(
        fetcher=failing_graphdata,
        package_reader=lambda: {
            "value": 36,
            "rating": "fear",
            "timestamp": "2026-06-12T20:00:00+00:00",
        },
        now=datetime(2026, 6, 12, 21, tzinfo=timezone.utc),
    )

    assert reading.value == 36
    assert reading.rating == "fear"
    assert reading.observation_date == "2026-06-12"
    assert reading.source == "CNN Fear & Greed package"


def test_fear_greed_optional_package_list_never_uses_crypto_provider() -> None:
    assert FEAR_GREED_PACKAGE_MODULES
    assert all("crypto" not in module.lower() for module in FEAR_GREED_PACKAGE_MODULES)
