from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from data.review_center_view_model import build_review_center_view_model
from data.review_queue_builder import ReviewQueueStore


REQUIRED_ITEM_FIELDS = {
    "symbol",
    "metric",
    "currentValue",
    "proposedValue",
    "source",
    "confidence",
    "impactLevel",
    "reviewStatus",
    "suggestedAction",
    "reasonSummary",
    "evidenceSummary",
    "canAutoConfirm",
    "canAutoArchive",
}


class ReviewCenterViewModelTests(unittest.TestCase):
    def test_review_center_groups_queue_rows_into_workbench_lanes(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = ReviewQueueStore(Path(tmpdir) / "review.sqlite")
            _insert_review_item(
                store,
                metric_key="revenueGrowth",
                confidence="low",
                affects="Quality",
                source_type=None,
                source_url=None,
                evidence_text="",
            )
            _insert_review_item(
                store,
                metric_key="netRetentionRate",
                confidence="high",
                affects="Quality",
                source_type="SEC_10Q",
                evidence_text="net retention rate was 120%.",
            )
            _insert_review_item(
                store,
                metric_key="minorDisclosureGap",
                item_type="manual_override_needed",
                value=None,
                confidence="low",
                affects="ConfidenceOnly",
                source_type="SYSTEM",
                source_url=None,
                evidence_text="",
                review_status="needs_data",
            )
            correction = _insert_review_item(
                store,
                metric_key="rpoGrowth",
                confidence="high",
                affects="Quality",
                source_type="IR_RELEASE",
                evidence_text="RPO grew 20%.",
            )
            store.set_ai_triage(
                int(correction["id"]),
                "ai_recommend_correct",
                correction_candidate={"correctedValue": 22, "correctedUnit": "percent", "correctedPeriod": "Q1 2026"},
            )
            handled = _insert_review_item(
                store,
                metric_key="oldMetric",
                confidence="medium",
                affects="Quality",
                source_type="IR_RELEASE",
                evidence_text="old metric was confirmed.",
            )
            store.update_review_status(int(handled["id"]), "approved")

            view = build_review_center_view_model(store=store)

            groups = {group["key"]: group for group in view["groups"]}
            self.assertEqual(
                set(groups),
                {
                    "highPriorityPending",
                    "scoringImpactNeedsHuman",
                    "autoConfirmCandidates",
                    "autoArchiveCandidates",
                    "aiSuggestedCorrections",
                    "insufficientEvidence",
                    "recentlyHandled",
                },
            )
            for item in view["items"]:
                self.assertTrue(REQUIRED_ITEM_FIELDS <= set(item))

            high_priority_metrics = [item["metricKey"] for item in groups["highPriorityPending"]["items"]]
            self.assertEqual(high_priority_metrics[0], "revenueGrowth")
            self.assertIn("revenueGrowth", [item["metricKey"] for item in groups["scoringImpactNeedsHuman"]["items"]])
            self.assertIn("netRetentionRate", [item["metricKey"] for item in groups["autoConfirmCandidates"]["items"]])
            self.assertIn("minorDisclosureGap", [item["metricKey"] for item in groups["autoArchiveCandidates"]["items"]])
            self.assertIn("revenueGrowth", [item["metricKey"] for item in groups["insufficientEvidence"]["items"]])
            self.assertIn("oldMetric", [item["metricKey"] for item in groups["recentlyHandled"]["items"]])

            correction_items = groups["aiSuggestedCorrections"]["items"]
            self.assertEqual(correction_items[0]["metricKey"], "rpoGrowth")
            self.assertEqual(correction_items[0]["proposedValue"], 22)
            self.assertEqual(correction_items[0]["suggestedAction"], "review_ai_correction")

    def test_review_center_does_not_auto_confirm_ai_speculation(self) -> None:
        with TemporaryDirectory() as tmpdir:
            store = ReviewQueueStore(Path(tmpdir) / "review.sqlite")
            _insert_review_item(
                store,
                metric_key="aiOnlyMetric",
                confidence="high",
                affects="Quality",
                source_type="AI_MODEL",
                source_url=None,
                evidence_text="model inferred the value",
            )

            view = build_review_center_view_model(store=store)
            groups = {group["key"]: group for group in view["groups"]}

            self.assertEqual(groups["autoConfirmCandidates"]["items"], [])
            item = view["items"][0]
            self.assertFalse(item["canAutoConfirm"])
            self.assertEqual(item["suggestedAction"], "manual_confirm_after_evidence_review")


def _insert_review_item(
    store: ReviewQueueStore,
    metric_key: str,
    item_type: str = "extracted_value",
    value: float | None = 20,
    confidence: str = "medium",
    affects: str = "Quality",
    source_type: str | None = "IR_RELEASE",
    source_url: str | None = "https://example.com/source",
    evidence_text: str = "subscription revenue grew 20% in Q1 2026.",
    review_status: str = "pending_review",
) -> dict:
    store.upsert_item(
        {
            "symbol": "NOW",
            "metricKey": metric_key,
            "displayName": metric_key,
            "itemType": item_type,
            "value": value,
            "unit": "percent" if value is not None else None,
            "period": "Q1 2026",
            "sourceType": source_type,
            "sourceUrl": source_url,
            "sourceDocumentTitle": "Source Document" if source_url else None,
            "extractedText": evidence_text,
            "evidenceText": evidence_text,
            "confidence": confidence,
            "affects": affects,
            "reviewStatus": review_status,
            "recommendedAction": "Review this queue item",
            "resolutionStatus": "available",
            "sourceKind": "metric_resolution",
            "modelType": "SAAS_SOFTWARE",
            "explanation": "test row",
        }
    )
    return store.list_items(symbol="NOW", metric_key=metric_key)[0]
