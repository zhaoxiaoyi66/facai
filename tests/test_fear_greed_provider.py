from __future__ import annotations

from datetime import datetime, timezone

from data.fear_greed_provider import CNN_FEAR_GREED_GRAPH_URL, fetch_cnn_fear_greed


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
