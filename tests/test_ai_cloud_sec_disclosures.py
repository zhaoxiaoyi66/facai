from __future__ import annotations

from data.ai_cloud_sec_disclosures import _sec_log_error_message
from data.ai_cloud_sec_disclosures import refresh_ai_cloud_sec_disclosures


def test_ai_cloud_sec_refresh_early_errors_are_localized() -> None:
    empty = refresh_ai_cloud_sec_disclosures("")
    unsupported = refresh_ai_cloud_sec_disclosures("AAPL")

    assert empty["status"] == "failed"
    assert empty["statusLabel"] == "失败"
    assert empty["errors"] == ["缺少股票代码"]
    assert unsupported["status"] == "failed"
    assert unsupported["statusLabel"] == "失败"
    assert unsupported["errors"] == ["仅支持 CRWV / NBIS 的 AI 云 SEC 披露刷新"]


def test_ai_cloud_sec_failed_log_fallback_is_localized() -> None:
    message = _sec_log_error_message({"sourceType": "SEC_10Q"})

    assert message == "SEC_10Q: 请求失败"
    assert "failed" not in message
