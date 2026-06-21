from __future__ import annotations

from ui.metric_labels import confidence_label, model_type_label, resolution_status_label, source_type_label


def test_metric_status_labels_hide_unknown_internal_codes() -> None:
    assert resolution_status_label("NEW_INTERNAL_STATUS") == "未映射状态"
    assert source_type_label("NEW_INTERNAL_SOURCE") == "未映射来源"
    assert confidence_label("NEW_INTERNAL_CONFIDENCE") == "未映射置信度"
    assert model_type_label("NEW_INTERNAL_MODEL") == "未映射模型"


def test_metric_status_labels_preserve_chinese_custom_labels() -> None:
    assert resolution_status_label("人工状态") == "人工状态"
    assert source_type_label("人工来源") == "人工来源"
    assert confidence_label("人工置信度") == "人工置信度"
    assert model_type_label("人工模型") == "人工模型"
