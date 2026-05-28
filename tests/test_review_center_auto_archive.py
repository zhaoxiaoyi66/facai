from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from data.review_center_auto_archive import auto_archive_low_priority_review_items
from data.review_queue_builder import ReviewQueueStore


class ReviewCenterAutoArchiveTests(unittest.TestCase):
    def test_dry_run_identifies_stale_and_historical_archive_candidates(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = ReviewQueueStore(Path(tmpdir) / "review.sqlite")
            _insert_item(store, "minorDisclosureGap", review_status="stale", affects="ExplanationOnly", value=None, source_type="SYSTEM")
            _insert_item(
                store,
                "oldNarrativeMetric",
                affects="ExplanationOnly",
                value=None,
                source_type="SYSTEM",
                freshness_status="historical_value",
            )

            result = auto_archive_low_priority_review_items(store=store)

            self.assertTrue(result["dryRun"])
            self.assertEqual(result["eligibleCount"], 2)
            self.assertEqual(result["archivedCount"], 0)
            self.assertEqual({item["reason"] for item in result["items"]}, {"stale", "historical_value"})
            self.assertEqual({row["reviewStatus"] for row in store.list_items()}, {"stale", "pending_review"})

    def test_duplicate_archived_items_are_recognized_as_already_archived(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = ReviewQueueStore(Path(tmpdir) / "review.sqlite")
            _insert_item(store, "duplicateMetric", review_status="duplicate_archived", affects="ExplanationOnly", value=None)

            result = auto_archive_low_priority_review_items(store=store)

            self.assertEqual(result["eligibleCount"], 0)
            self.assertEqual(len(result["alreadyArchived"]), 1)
            self.assertEqual(result["alreadyArchived"][0]["reason"], "duplicate_archived")

    def test_scoring_impact_values_are_not_archived_even_when_view_model_marks_candidate(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = ReviewQueueStore(Path(tmpdir) / "review.sqlite")
            _insert_item(store, "revenueGrowth", review_status="stale", affects="Quality", value=0.2, source_type="SYSTEM")

            result = auto_archive_low_priority_review_items(store=store, dry_run=False)

            self.assertEqual(result["eligibleCount"], 0)
            self.assertEqual(result["archivedCount"], 0)
            self.assertEqual(result["skipped"][0]["reason"], "scoring_impact_value")
            self.assertEqual(store.list_items(metric_key="revenueGrowth")[0]["reviewStatus"], "stale")

    def test_hood_operating_fields_are_not_archived(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = ReviewQueueStore(Path(tmpdir) / "review.sqlite")
            _insert_item(
                store,
                "hoodAuc",
                symbol="HOOD",
                review_status="stale",
                affects="Entry",
                value=None,
                source_type="SYSTEM",
            )

            result = auto_archive_low_priority_review_items(store=store, symbol="HOOD", dry_run=False)

            self.assertEqual(result["eligibleCount"], 0)
            self.assertEqual(result["archivedCount"], 0)
            self.assertEqual(result["skipped"][0]["reason"], "protected_hood_operating_field")
            self.assertEqual(store.list_items(symbol="HOOD")[0]["reviewStatus"], "stale")


def _insert_item(
    store: ReviewQueueStore,
    metric_key: str,
    *,
    symbol: str = "NOW",
    item_type: str = "missing_kpi",
    value: float | None = None,
    confidence: str = "low",
    affects: str = "ExplanationOnly",
    source_type: str | None = "SYSTEM",
    review_status: str = "pending_review",
    freshness_status: str | None = None,
) -> dict:
    store.upsert_item(
        {
            "symbol": symbol,
            "metricKey": metric_key,
            "displayName": metric_key,
            "itemType": item_type,
            "value": value,
            "unit": "percent" if value is not None else None,
            "period": "Q1 2026",
            "sourceType": source_type,
            "sourceUrl": None,
            "sourceDocumentTitle": None,
            "extractedText": "",
            "evidenceText": "",
            "confidence": confidence,
            "affects": affects,
            "reviewStatus": review_status,
            "recommendedAction": "Review this queue item",
            "resolutionStatus": "requires_review",
            "sourceKind": "metric_resolution",
            "modelType": "SAAS_SOFTWARE",
            "explanation": "test row",
            "freshnessStatus": freshness_status,
        }
    )
    return store.list_items(symbol=symbol, metric_key=metric_key)[0]


if __name__ == "__main__":
    unittest.main()
