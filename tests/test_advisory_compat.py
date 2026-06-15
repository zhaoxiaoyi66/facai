from __future__ import annotations

from types import SimpleNamespace

from data.advisory_compat import (
    advisory_reason_list,
    legacy_block_reason_list,
    review_reason_list,
)


def test_advisory_reasons_prefer_new_contract_over_legacy_block_fields() -> None:
    source = {"advisoryReasons": ["new advisory"], "blockReasons": ["legacy block"]}

    assert advisory_reason_list(source) == ["new advisory"]


def test_advisory_reasons_fall_back_to_legacy_block_fields() -> None:
    source = {"radar_block_reasons": '["legacy radar warning"]'}

    assert advisory_reason_list(source) == ["legacy radar warning"]
    assert legacy_block_reason_list(source) == ["legacy radar warning"]


def test_advisory_reasons_can_ignore_legacy_fallback() -> None:
    source = {"blockReasons": ["legacy block"]}

    assert advisory_reason_list(source, include_legacy=False) == []


def test_reason_helpers_support_object_sources() -> None:
    source = SimpleNamespace(reviewReasons=["data confidence"], block_reasons=["old block"])

    assert review_reason_list(source) == ["data confidence"]
    assert advisory_reason_list(source) == ["old block"]
