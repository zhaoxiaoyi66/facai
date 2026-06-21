from __future__ import annotations

from ui.display_labels import display_label, replace_display_terms


def test_display_label_localizes_known_internal_fields() -> None:
    assert display_label("anchor_source") == "锚点来源"
    assert display_label("candidate") == "自动匹配"
    assert display_label("confirmed") == "人工锁定"
    assert display_label("DATA_MISSING") == "数据缺失"
    assert display_label("DATA_INSUFFICIENT") == "数据不足"
    assert display_label("N/A") == "缺少数据"
    assert display_label("unknown") == "待确认"
    assert display_label("UNKNOWN") == "待确认"
    assert display_label(None) == "缺少数据"


def test_replace_display_terms_replaces_whole_internal_tokens_only() -> None:
    text = "AI Stock Radar Research / anchor_source / DATA_MISSING / None / N/A / unknown / candidate / confirmed"

    localized = replace_display_terms(text)

    assert "研报中心" in localized
    assert "锚点来源" in localized
    assert "数据缺失" in localized
    assert "缺少数据" in localized
    assert "待确认" in localized
    assert "自动匹配" in localized
    assert "人工锁定" in localized


def test_replace_display_terms_does_not_corrupt_compound_words() -> None:
    text = "NoneType error in candidate_scan_status and confirmed_value"

    localized = replace_display_terms(text)

    assert "NoneType" in localized
    assert "candidate_scan_status" in localized
    assert "confirmed_value" in localized
    assert "缺少数据Type" not in localized
    assert "自动匹配_scan_status" not in localized
    assert "人工锁定_value" not in localized
