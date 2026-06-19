from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from html import escape
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from data.afterhours_provider import AfterhoursReference
from data.equity_afterhours_provider import CachedAfterhoursProvider, MultiProviderAfterhoursProvider, NullAfterhoursProvider, default_afterhours_provider
from data.binance_equity_scan import (
    DEFAULT_BINANCE_EQUITY_SCAN_CACHE_PATH,
    MAPPING_ANCHOR_MISSING,
    MAPPING_AUTO_USABLE,
    MAPPING_AVAILABLE,
    MAPPING_ETF_VERIFIED,
    MAPPING_IGNORED,
    MAPPING_INVALID,
    MAPPING_MANUAL_LOCKED,
    MAPPING_OTHER_TRADFI,
    MAPPING_PENDING_VERIFICATION,
    MAPPING_PRICE_ANOMALY,
    MAPPING_PRICE_UNVERIFIED,
    MAPPING_REVIEW,
    MAPPING_UNAVAILABLE,
    MAPPING_US_EQUITY_VERIFIED,
    read_binance_equity_scan_cache,
    scan_binance_equity_mapped_symbols,
    scan_records_to_mapping,
    write_binance_equity_scan_cache,
)
from data.binance_provider import DEFAULT_BINANCE_CACHE_PATH, CachedBinancePriceProvider, BinanceHTTPPriceProvider, normalize_market_type
from data.cache_read_model import CacheReadModel
from data.portfolio import PortfolioPositionStore
from data.weekend_basis_mapping_audit import (
    audit_weekend_basis_mappings,
    confirm_weekend_basis_mapping,
    reject_weekend_basis_mapping,
)
from data.weekend_spread_backtest import (
    build_weekend_backtest_preflight,
    clear_backtest_view_state,
    get_last_us_trading_day_of_week,
    load_backtest_results,
    recent_weekend_windows,
    run_weekend_basis_backfill_audit,
    run_weekend_basis_backtest,
    save_backtest_results,
    summarize_backfill_audit_results,
    summarize_backtest_results,
)
from data.weekend_spread import (
    DEFAULT_IGNORE_PATH,
    DEFAULT_LOCAL_MAPPING_PATH,
    build_mapping_diagnostics,
    build_weekend_spread_rows,
    ignore_binance_symbol,
    is_binance_symbol_ignored,
    load_binance_symbol_mapping,
    load_binance_symbol_ignore,
    restore_ignored_binance_symbol,
    upsert_default_usdm_futures_mappings,
    upsert_local_binance_symbol_mapping,
)
from data.weekend_spread_cache import (
    annotate_cached_rows,
    has_successful_price,
    is_provider_failure,
    read_weekend_spread_snapshot,
    write_weekend_spread_failure,
    write_weekend_spread_snapshot,
)
from data.weekend_spread_log import (
    build_history_stats,
    generate_weekly_summary,
    get_weekly_log_snapshot,
    record_spread_samples,
    update_monday_outcome,
)
from data.weekend_spread_monitor import (
    DEFAULT_MONITOR_INTERVAL_MINUTES,
    DEFAULT_MONITOR_SNAPSHOT_PATH,
    latest_monitor_run,
    monitor_history_rows,
    read_monitor_state,
    recent_monitor_runs,
    run_monitor_scan,
)
from data.overnight_price_provider import build_overnight_provider_self_check, default_overnight_price_provider
from data.tradingview_price_cache import (
    DEFAULT_TRADINGVIEW_CSV_DIR,
    EVENT_FRIDAY_AFTERHOURS_CLOSE,
    EVENT_OVERNIGHT_FIRST_1M_CLOSE,
    import_tradingview_csv_dir,
    scan_tradingview_csv_dir,
    upsert_manual_overnight_price,
    webhook_status_summary,
)
from settings import load_watchlist


RISK_NOTICE = "Binance 映射价格不等于真实美股可成交价格，本页仅用于观察价差，不构成套利建议。"
LARGE_WEEKEND_PREMIUM_PCT = 1.5
STRICT_P2_MISSING_TEXT = "夜盘首分钟无有效 1m K线，不适合开盘第一时间平单"
OPENING_WINDOW_P2_MISSING_TEXT = "夜盘开盘窗口内无有效 1m K线"
STRICT_P2_FLOW_TEXT = "无首分钟价格"
STRICT_P2_STRATEGY_TEXT = "该标的夜盘开盘首分钟无成交 / 无 1m bar，本策略不可用。"

TAB_REALTIME = "实时观察"
TAB_MONITOR = "周末监控"
TAB_BACKTEST = "历史回测"
TAB_MAPPING = "映射管理"
HKT = ZoneInfo("Asia/Hong_Kong")
ET = ZoneInfo("America/New_York")
MONITOR_PROCESS_PATH = Path(__file__).resolve().parents[1] / "data" / "cache" / "weekend_spread_monitor_process.json"
MONITOR_LOG_PATH = Path(__file__).resolve().parents[1] / ".cache" / "weekend_spread_monitor.log"
VERIFIED_MAPPING_LABELS = {MAPPING_AVAILABLE, MAPPING_US_EQUITY_VERIFIED, MAPPING_ETF_VERIFIED}
PENDING_MAPPING_LABELS = {MAPPING_PENDING_VERIFICATION, MAPPING_PRICE_UNVERIFIED, MAPPING_ANCHOR_MISSING, "自动可用，价格校验不足", "锚点缺失"}
REVIEW_MAPPING_LABELS = {MAPPING_REVIEW, "需确认", "异常复核"}
INVALID_MAPPING_LABELS = {MAPPING_INVALID, "无效映射", "无映射"}
MAPPING_AVAILABLE_LABEL = "映射可用"
MAPPING_ANOMALY_LABEL = "价格异常"
MAPPING_UNAVAILABLE_LABEL = "不可用"
MAPPING_IGNORED_LABEL = "已忽略"


def _apply_weekend_spread_layout_css() -> None:
    st.markdown(
        """
        <style>
        .block-container {
          max-width: 1440px;
          padding-left: 2rem;
          padding-right: 2rem;
        }
        .weekend-core-card {
          border: 1px solid #dbeafe;
          border-left: 5px solid #2563eb;
          border-radius: 14px;
          padding: 20px 22px;
          margin: 10px 0 14px;
          background: linear-gradient(180deg, #f8fbff 0%, #ffffff 100%);
          box-shadow: 0 10px 26px rgba(15, 23, 42, 0.06);
        }
        .weekend-core-title {
          color: #0f172a;
          font-size: 18px;
          font-weight: 800;
          margin-bottom: 6px;
        }
        .weekend-core-flow-label {
          color: #64748b;
          font-size: 13px;
          margin-bottom: 8px;
        }
        .weekend-core-flow {
          color: #0f172a;
          font-size: 30px;
          line-height: 1.2;
          font-weight: 850;
          letter-spacing: 0;
          margin-bottom: 16px;
        }
        .weekend-core-metrics {
          display: grid;
          grid-template-columns: repeat(4, minmax(0, 1fr));
          gap: 10px;
          margin-bottom: 14px;
        }
        .weekend-core-metric {
          border: 1px solid #e2e8f0;
          border-radius: 10px;
          padding: 10px 12px;
          background: #fff;
        }
        .weekend-core-metric-label {
          color: #64748b;
          font-size: 12px;
          margin-bottom: 4px;
        }
        .weekend-core-metric-value {
          color: #0f172a;
          font-size: 20px;
          font-weight: 800;
        }
        .weekend-core-sources,
        .weekend-status-strip {
          color: #475569;
          font-size: 13px;
        }
        .weekend-status-strip {
          border: 1px solid #e2e8f0;
          border-radius: 10px;
          padding: 9px 12px;
          margin: 6px 0 16px;
          background: #f8fafc;
        }
        .weekend-realtime-summary {
          display: grid;
          grid-template-columns: repeat(5, minmax(0, 1fr));
          gap: 10px;
          margin: 8px 0 12px;
        }
        .weekend-realtime-kpi {
          border: 1px solid #e2e8f0;
          border-radius: 10px;
          padding: 12px 14px;
          background: #ffffff;
          box-shadow: 0 8px 20px rgba(15, 23, 42, 0.04);
        }
        .weekend-realtime-kpi-label {
          color: #64748b;
          font-size: 12px;
          margin-bottom: 5px;
        }
        .weekend-realtime-kpi-value {
          color: #0f172a;
          font-size: 22px;
          line-height: 1.15;
          font-weight: 850;
          letter-spacing: 0;
        }
        .weekend-core-observation {
          border: 1px solid #dbeafe;
          border-left: 4px solid #2563eb;
          border-radius: 12px;
          padding: 14px 16px;
          margin: 6px 0 10px;
          background: #f8fbff;
        }
        .weekend-core-observation strong {
          color: #0f172a;
        }
        .weekend-detail-card {
          border: 1px solid #e2e8f0;
          border-radius: 12px;
          padding: 14px 16px;
          margin-top: 10px;
          background: #ffffff;
        }
        @media (max-width: 860px) {
          .weekend-core-metrics {
            grid-template-columns: repeat(2, minmax(0, 1fr));
          }
          .weekend-core-flow {
            font-size: 23px;
          }
          .weekend-realtime-summary {
            grid-template-columns: repeat(2, minmax(0, 1fr));
          }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_monitor_tab_safe(rows: list[dict], ignored: dict[str, dict] | None = None) -> None:
    renderer = globals().get("_render_monitor_tab")
    if callable(renderer):
        renderer(rows, ignored)
        return
    st.warning("周末监控模块正在加载。请刷新页面后重试。")


def render() -> None:
    _apply_weekend_spread_layout_css()
    st.markdown(
        """
        <section class="zhx-page-head">
          <div>
            <span class="zhx-eyebrow">ZHX RESEARCH</span>
            <h1>周末价差观察台</h1>
            <p>观察 Binance 美股映射价格与美股盘后锚点、夜盘价格之间的差异。</p>
          </div>
        </section>
        """,
        unsafe_allow_html=True,
    )
    st.warning(RISK_NOTICE)

    mapping = load_binance_symbol_mapping()
    ignored = load_binance_symbol_ignore()
    active_mapping = _filter_ignored_mapping(mapping, ignored)
    watchlist = load_watchlist()

    realtime_tab, monitor_tab, backtest_tab, mapping_tab = st.tabs([TAB_REALTIME, TAB_MONITOR, TAB_BACKTEST, TAB_MAPPING])

    with realtime_tab:
        rows, mapping_counts = _render_realtime_tab(watchlist, active_mapping, ignored)
    with monitor_tab:
        _render_monitor_tab_safe(rows, ignored)
    with backtest_tab:
        _render_backtest_tab(watchlist, active_mapping)
    with mapping_tab:
        _render_mapping_tab(rows, mapping, mapping_counts, ignored, watchlist=watchlist)


def _filter_ignored_mapping(mapping: dict[str, dict], ignored: dict[str, dict] | None = None) -> dict[str, dict]:
    ignored = ignored or {}
    result: dict[str, dict] = {}
    for ticker, config in (mapping or {}).items():
        symbol = str((config or {}).get("binance_symbol") or "").strip().upper()
        if is_binance_symbol_ignored(ticker, symbol, ignored):
            continue
        result[str(ticker or "").strip().upper()] = dict(config or {})
    return result


def _filter_ignored_records(records: list[dict], ignored: dict[str, dict] | None = None) -> list[dict]:
    ignored = ignored or {}
    return [
        dict(record)
        for record in records or []
        if not is_binance_symbol_ignored(record.get("ticker"), record.get("binance_symbol"), ignored)
    ]


def _weekend_scope_tickers(watchlist: list[str], mapping: dict[str, dict] | None = None) -> list[str]:
    tickers: list[str] = []
    seen: set[str] = set()
    scan_cache = read_binance_equity_scan_cache()
    ignored = load_binance_symbol_ignore()
    sources: list[object] = []
    sources.extend(
        str(record.get("ticker") or "")
        for record in scan_cache.get("records") or []
        if isinstance(record, dict) and not is_binance_symbol_ignored(record.get("ticker"), record.get("binance_symbol"), ignored)
    )
    sources.extend((mapping or {}).keys())
    sources.extend(_portfolio_symbols())
    sources.extend(watchlist or [])
    for item in sources:
        ticker = str(item or "").strip().upper()
        if ticker and ticker not in seen:
            tickers.append(ticker)
            seen.add(ticker)
    return tickers


def _render_realtime_tab(
    watchlist: list[str],
    mapping: dict[str, dict],
    ignored: dict[str, dict] | None = None,
) -> tuple[list[dict], dict[str, int]]:
    st.subheader("Binance 美股映射全市场扫描")
    st.caption("观察 Binance 美股映射价格相对最近美股盘后锚点的偏离。")
    ignored = ignored or {}
    action_slot = st.empty()
    summary_slot = st.empty()
    observation_slot = st.empty()
    status_slot = st.empty()
    filter_slot = st.empty()
    table_slot = st.empty()
    detail_slot = st.empty()
    advanced_slot = st.empty()
    with action_slot.container():
        refresh_options = _render_realtime_action_bar()
    refresh_options["ignored_count"] = len(ignored)
    scan_records, scan_status = _load_realtime_scan_records(watchlist, mapping, ignored, refresh_options=refresh_options)
    scan_mapping = scan_records_to_mapping(scan_records, mapping)
    scan_tickers = [str(record.get("ticker") or "").strip().upper() for record in scan_records if record.get("ticker")]
    rows, cache_status = _build_weekend_spread_rows_with_feedback(scan_tickers, mapping=scan_mapping, refresh_options=refresh_options)
    rows = _merge_scan_metadata(rows, scan_records, watchlist)
    st.session_state["weekend_spread_realtime_rows"] = rows
    st.session_state["weekend_spread_realtime_cache_status"] = cache_status
    st.session_state["weekend_spread_realtime_scan_status"] = scan_status

    flash_message = st.session_state.pop("weekend_spread_realtime_flash", "")
    if flash_message:
        st.info(str(flash_message))

    mapping_counts = _mapping_counts(rows, scan_mapping)
    mapping_counts.update(
        {
            "scan_record_count": len(scan_records),
            "scan_cache_state": str(scan_status.get("cache_state") or ""),
            "scan_generated_at": str(scan_status.get("generated_at") or ""),
            "ignored_count": len(ignored),
        }
    )

    with summary_slot.container():
        _render_realtime_summary_cards(rows, mapping_counts, cache_status)
    with observation_slot.container():
        _render_core_observation(rows, mapping_counts)
    with status_slot.container():
        _render_realtime_status_strip(rows, mapping_counts, cache_status)
    with filter_slot.container():
        visible_scope = _render_realtime_filters(rows)

    main_rows = _filter_live_rows_by_scope(rows, visible_scope)
    with table_slot.container():
        st.markdown("#### 实时价差表")
        if not scan_records and _should_show_empty_mapping_state(mapping_counts, "重点/有数据"):
            _render_empty_mapping_state(mapping_counts, DEFAULT_LOCAL_MAPPING_PATH)
        elif main_rows:
            st.dataframe(_live_frame(main_rows), width="stretch", hide_index=True)
        else:
            st.info(_realtime_empty_state_text(rows, visible_scope))
    with detail_slot.container():
        if main_rows:
            _render_row_details(main_rows, all_rows=rows, mapping=scan_mapping, tickers=scan_tickers)

    with advanced_slot.container():
        with st.expander("高级设置 / 缓存管理", expanded=False):
            _render_no_mapping_expander(rows)
            _render_refresh_diagnostics(rows, ignored)
    return rows, mapping_counts


def _render_realtime_action_bar() -> dict[str, bool]:
    col_refresh, col_anchor = st.columns(2)
    refresh = col_refresh.button("刷新实时价格", width="stretch", key="weekend_spread_refresh")
    anchor_refresh = col_anchor.button("更新美股盘后锚点", width="stretch", key="weekend_spread_anchor_refresh")
    use_cache = False
    force_anchor = False
    clear_scan_cache = False
    with st.expander("数据源与补数工具", expanded=False):
        col_cache, col_force_anchor, col_clear = st.columns([1, 1, 1])
        use_cache = col_cache.button("使用缓存", width="stretch", key="weekend_spread_use_cache")
        force_anchor = col_force_anchor.button("重新抓取锚点", width="stretch", key="weekend_spread_force_anchor_refresh")
        clear_scan_cache = col_clear.button("清空扫描缓存", width="stretch", key="weekend_spread_clear_binance_equity_scan_cache")
        if clear_scan_cache:
            try:
                DEFAULT_BINANCE_EQUITY_SCAN_CACHE_PATH.unlink(missing_ok=True)
            except OSError as exc:
                st.warning(f"清空扫描缓存失败：{exc}")
            else:
                st.success("已清空 Binance 美股映射扫描缓存。")
        scan_cache = read_binance_equity_scan_cache()
        st.download_button(
            "导出扫描结果",
            data=json.dumps(scan_cache.get("records") or [], ensure_ascii=False, indent=2),
            file_name="binance_equity_scan.json",
            mime="application/json",
            width="stretch",
            key="weekend_spread_export_binance_equity_scan",
        )
        st.caption("Binance 价格和最后交易日盘后锚点已解耦：刷新实时观察不会强制重建盘后锚点。")
    return {
        "scan": False,
        "use_cache": bool(use_cache),
        "refresh": bool(refresh),
        "anchor_refresh": bool(anchor_refresh),
        "force_anchor_refresh": bool(force_anchor),
        "clear_scan_cache": bool(clear_scan_cache),
    }


def _load_realtime_scan_records(
    watchlist: list[str],
    mapping: dict[str, dict],
    ignored: dict[str, dict] | None = None,
    *,
    refresh_options: dict[str, bool] | None = None,
) -> tuple[list[dict], dict[str, object]]:
    options = refresh_options or {}
    ignored = ignored or {}
    force_scan = bool(options.get("scan"))
    use_cache = bool(options.get("use_cache"))
    cached = read_binance_equity_scan_cache()
    if not force_scan and cached.get("records") and (use_cache or cached.get("cache_state") == "FRESH"):
        return _tag_scan_records(_filter_ignored_records(list(cached.get("records") or []), ignored), watchlist), cached
    if not force_scan and cached.get("records"):
        return _tag_scan_records(_filter_ignored_records(list(cached.get("records") or []), ignored), watchlist), cached
    if not force_scan:
        records = _filter_ignored_records(_fallback_scan_records_from_mapping(watchlist, mapping), ignored)
        return records, {"cache_state": "LOCAL_FALLBACK", "records": records, "generated_at": ""}
    provider = CachedBinancePriceProvider(BinanceHTTPPriceProvider(), ttl_seconds=60)
    records = scan_binance_equity_mapped_symbols(
        provider=provider,
        cache=CacheReadModel(),
        watchlist=watchlist,
        position_symbols=_portfolio_symbols(),
        manual_mapping=mapping,
        ignored_mappings=ignored,
        force_refresh=True,
    )
    records = _filter_ignored_records(records, ignored)
    payload = write_binance_equity_scan_cache(records)
    scan_mapping = scan_records_to_mapping(records, mapping)
    if records:
        _write_scan_mapping_local_file(scan_mapping)
    summary = _scan_sync_summary(records, mapping)
    if records:
        st.success(
            "已从 Binance 官方合约信息识别 "
            f"{summary['total']} 个美股 / TradFi 映射，新增 {summary['added']} 个，"
            f"更新 {summary['updated']} 个，映射可用 {summary['available']} 个，"
            f"价格异常 {summary['anomaly']} 个，不可用 {summary['unavailable']} 个。"
        )
    else:
        st.warning("扫描完成，但没有识别到可用的 Binance 美股映射。请检查 Binance 数据源或本地股票缓存。")
    return _tag_scan_records(records, watchlist), {
        "cache_state": "API_LIVE",
        "records": records,
        "generated_at": payload.get("generated_at", ""),
        "sync_summary": summary,
    }


def _write_scan_mapping_local_file(mapping: dict[str, dict]) -> None:
    DEFAULT_LOCAL_MAPPING_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_LOCAL_MAPPING_PATH.write_text(
        json.dumps({"mappings": mapping}, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _scan_sync_summary(records: list[dict], previous_mapping: dict[str, dict]) -> dict[str, int]:
    previous = {str(key or "").strip().upper(): dict(value or {}) for key, value in (previous_mapping or {}).items()}
    total = len(records)
    added = 0
    updated = 0
    available = 0
    anomaly = 0
    unavailable = 0
    for record in records:
        ticker = str(record.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        quality = str(record.get("mapping_quality") or "")
        if quality in {MAPPING_AVAILABLE, MAPPING_US_EQUITY_VERIFIED, MAPPING_ETF_VERIFIED}:
            available += 1
        elif quality in {MAPPING_REVIEW, MAPPING_PRICE_ANOMALY}:
            anomaly += 1
        elif quality in {MAPPING_INVALID, MAPPING_UNAVAILABLE}:
            unavailable += 1
        old = previous.get(ticker)
        if old is None:
            added += 1
            continue
        old_symbol = str(old.get("binance_symbol") or "").strip().upper()
        new_symbol = str(record.get("binance_symbol") or "").strip().upper()
        old_status = str(old.get("mapping_status") or old.get("mapping_confidence") or "")
        if old_symbol != new_symbol or old_status != quality:
            updated += 1
    return {
        "total": total,
        "added": added,
        "updated": updated,
        "available": available,
        "anomaly": anomaly,
        "unavailable": unavailable,
    }


def _fallback_scan_records_from_mapping(watchlist: list[str], mapping: dict[str, dict]) -> list[dict]:
    records: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()
    watchlist_set = {str(item or "").strip().upper() for item in watchlist if str(item or "").strip()}
    position_set = set(_portfolio_symbols())
    for ticker, config in sorted((mapping or {}).items()):
        symbol = str((config or {}).get("binance_symbol") or "").strip().upper()
        if not symbol or not (config or {}).get("enabled", True):
            continue
        confidence = str((config or {}).get("mapping_confidence") or "").strip().lower()
        quality = str((config or {}).get("mapping_status") or "").strip()
        if confidence == "confirmed" or (config or {}).get("manually_locked"):
            quality = MAPPING_MANUAL_LOCKED
        elif quality not in {
            MAPPING_AVAILABLE,
            MAPPING_PRICE_ANOMALY,
            MAPPING_US_EQUITY_VERIFIED,
            MAPPING_ETF_VERIFIED,
            MAPPING_PENDING_VERIFICATION,
            MAPPING_OTHER_TRADFI,
            MAPPING_REVIEW,
            MAPPING_INVALID,
        }:
            quality = MAPPING_AVAILABLE if confidence == "auto_available" else MAPPING_INVALID
        records.append(
            {
                "ticker": str(ticker or "").strip().upper(),
                "binance_symbol": symbol,
                "market_type": "usdm_futures",
                "detected_by": "local_mapping",
                "underlying_type": (config or {}).get("underlying_type", ""),
                "underlying_sub_type": (config or {}).get("underlying_sub_type", ""),
                "binance_category": (config or {}).get("binance_category", ""),
                "tradfi_bucket": (config or {}).get("tradfi_bucket", ""),
                "mapping_quality": quality,
                "reason": "来自本地映射缓存；点击扫描可刷新 Binance 全市场候选。",
                "is_watchlist": str(ticker or "").strip().upper() in watchlist_set,
                "is_position": str(ticker or "").strip().upper() in position_set,
                "updated_at": now,
            }
        )
    return records


def _tag_scan_records(records: list[dict], watchlist: list[str]) -> list[dict]:
    watchlist_set = {str(item or "").strip().upper() for item in watchlist if str(item or "").strip()}
    position_set = set(_portfolio_symbols())
    tagged: list[dict] = []
    for record in records:
        item = dict(record)
        ticker = str(item.get("ticker") or "").strip().upper()
        item["is_watchlist"] = ticker in watchlist_set
        item["is_position"] = ticker in position_set
        tagged.append(item)
    return tagged


def _portfolio_symbols() -> list[str]:
    try:
        rows = PortfolioPositionStore().list_active_positions()
    except Exception:
        return []
    return [str(row.get("symbol") or "").strip().upper() for row in rows if str(row.get("symbol") or "").strip()]


def _merge_scan_metadata(rows: list[dict], scan_records: list[dict], watchlist: list[str]) -> list[dict]:
    scan_by_ticker = {str(record.get("ticker") or "").strip().upper(): dict(record) for record in scan_records}
    tagged_records = _tag_scan_records(scan_records, watchlist)
    scan_by_ticker.update({str(record.get("ticker") or "").strip().upper(): dict(record) for record in tagged_records})
    merged: list[dict] = []
    for row in rows:
        item = dict(row)
        ticker = str(item.get("ticker") or "").strip().upper()
        record = scan_by_ticker.get(ticker, {})
        if record:
            item["scan_detected_by"] = record.get("detected_by") or ""
            item["underlying_type"] = record.get("underlying_type") or ""
            item["underlying_sub_type"] = record.get("underlying_sub_type") or ""
            item["binance_category"] = record.get("binance_category") or ""
            item["mapping_quality"] = record.get("mapping_quality") or ""
            item["mapping_quality_reason"] = record.get("reason") or ""
            item["is_watchlist"] = bool(record.get("is_watchlist"))
            item["is_position"] = bool(record.get("is_position"))
            if record.get("binance_price") is not None and item.get("binance_last_price") is None:
                item["binance_last_price"] = record.get("binance_price")
            if record.get("price_diff_pct") is not None:
                item["mapping_price_diff_pct"] = record.get("price_diff_pct")
        merged.append(item)
    return merged


def _render_realtime_filters(rows: list[dict]) -> str:
    range_options = ["全部可计算", "我的观察池", "我的持仓", "核心仓"]
    status_options = ["全部状态", "异常偏离", "锚点缺失", "Binance 价格失败", "已忽略"]
    main_rows = [row for row in rows if _is_realtime_main_row(row)]
    counts = {
        "全部可计算": len(main_rows),
        "我的观察池": len([row for row in main_rows if row.get("is_watchlist")]),
        "我的持仓": len([row for row in main_rows if row.get("is_position")]),
        "核心仓": len([row for row in main_rows if row.get("is_core") or row.get("is_core_position")]),
        "异常偏离": len([row for row in main_rows if _realtime_row_status_key(row) == "review"]),
        "锚点缺失": len([row for row in rows if _realtime_row_status_key(row) == "anchor_missing"]),
        "Binance 价格失败": len([row for row in rows if _realtime_row_status_key(row) == "binance_failed"]),
        "已忽略": len([row for row in rows if _mapping_display_label_for_row(row) == MAPPING_IGNORED_LABEL]),
    }
    preferred_range, preferred_status = _default_realtime_filter_pair(counts)
    range_key = "weekend_spread_realtime_range_filter"
    status_key = "weekend_spread_realtime_status_filter"
    _sync_realtime_filter_state(range_key, range_options, preferred_range, counts)
    _sync_realtime_filter_state(status_key, status_options, preferred_status, counts)
    range_labels = [f"{option} {counts.get(option, 0)}" for option in range_options]
    status_labels = [f"{option} {counts.get(option, counts.get('全部可计算', 0))}" for option in status_options]
    selected_range = st.radio(
        "范围筛选",
        range_labels,
        horizontal=True,
        label_visibility="visible",
        key=range_key,
        index=_label_index(range_labels, st.session_state.get(range_key), f"{preferred_range} {counts.get(preferred_range, 0)}"),
    )
    selected_status = st.radio(
        "状态筛选",
        status_labels,
        horizontal=True,
        label_visibility="visible",
        key=status_key,
        index=_label_index(status_labels, st.session_state.get(status_key), f"{preferred_status} {counts.get(preferred_status, counts.get('全部可计算', 0))}"),
    )
    return f"{_scope_from_realtime_filter_label(selected_range, range_options)}|{_scope_from_realtime_filter_label(selected_status, status_options)}"


def _sync_realtime_filter_state(widget_key: str, options: list[str], preferred: str, counts: dict[str, int]) -> None:
    current_scope = _scope_from_realtime_filter_label(st.session_state.get(widget_key), options)
    current_count = counts.get(current_scope, counts.get("全部可计算", 0) if current_scope == "全部状态" else 0)
    preferred_count = counts.get(preferred, counts.get("全部可计算", 0) if preferred == "全部状态" else 0)
    if not current_scope or (current_scope != preferred and current_count <= 0 and preferred_count > 0):
        st.session_state[widget_key] = f"{preferred} {counts.get(preferred, counts.get('全部可计算', 0))}"


def _filter_live_rows_by_scope(rows: list[dict], scope: str) -> list[dict]:
    if "|" in str(scope or ""):
        range_scope, status_scope = str(scope).split("|", 1)
        selected = [
            row
            for row in rows
            if _row_matches_realtime_range(row, range_scope)
            and _row_matches_realtime_status(row, status_scope)
        ]
        return sorted(selected, key=_realtime_sort_key)
    if scope == "全部 Binance 美股映射":
        scope = "全部可计算"
    if scope == "全部可计算":
        selected = [row for row in rows if _is_realtime_main_row(row)]
    elif scope == "价格可用但锚点缺失":
        selected = [row for row in rows if _realtime_row_status_key(row) == "anchor_missing"]
    elif scope == "Binance 价格失败":
        selected = [row for row in rows if _realtime_row_status_key(row) == "binance_failed"]
    elif scope == "已忽略":
        selected = [row for row in rows if _mapping_display_label_for_row(row) == MAPPING_IGNORED_LABEL]
    elif scope == "我的观察池":
        selected = [row for row in rows if row.get("is_watchlist") and _is_realtime_main_row(row)]
    elif scope == "我的持仓":
        selected = [row for row in rows if row.get("is_position") and _is_realtime_main_row(row)]
    elif scope == "核心仓":
        selected = [row for row in rows if (row.get("is_core") or row.get("is_core_position")) and _is_realtime_main_row(row)]
    else:
        selected = [row for row in rows if _is_realtime_main_row(row) and _realtime_row_status_key(row) == "review"]
    return sorted(selected, key=_realtime_sort_key)


def _row_matches_realtime_range(row: dict, scope: str) -> bool:
    if scope in {"", "全部可计算"}:
        return True
    if scope == "我的观察池":
        return bool(row.get("is_watchlist"))
    if scope == "我的持仓":
        return bool(row.get("is_position"))
    if scope == "核心仓":
        return bool(row.get("is_core") or row.get("is_core_position"))
    return True


def _row_matches_realtime_status(row: dict, scope: str) -> bool:
    if scope in {"", "全部状态"}:
        return _is_realtime_main_row(row)
    if scope == "异常偏离":
        return _is_realtime_main_row(row) and _realtime_row_status_key(row) == "review"
    if scope in {"锚点缺失", "价格可用但锚点缺失"}:
        return _realtime_row_status_key(row) == "anchor_missing"
    if scope == "Binance 价格失败":
        return _realtime_row_status_key(row) == "binance_failed"
    if scope == "已忽略":
        return _mapping_display_label_for_row(row) == MAPPING_IGNORED_LABEL
    return _is_realtime_main_row(row)


def _realtime_empty_state_text(rows: list[dict], scope: str) -> str:
    counts = _realtime_observation_counts(rows)
    status_scope = str(scope or "").split("|", 1)[1] if "|" in str(scope or "") else str(scope or "")
    if status_scope == "异常偏离" and counts.get("review", 0) <= 0:
        return "当前没有异常偏离，可切换到“全部状态”查看全部可计算价差。"
    if status_scope in {"锚点缺失", "价格可用但锚点缺失"} and counts.get("anchor_missing", 0) <= 0:
        return "当前没有锚点缺失标的。"
    if status_scope == "Binance 价格失败" and counts.get("unavailable", 0) <= 0:
        return "当前没有 Binance 价格失败标的。"
    if counts.get("binance_price_available", 0) > 0 and counts.get("computable", 0) <= 0 and counts.get("anchor_missing", 0) > 0:
        return "Binance 价格已读取，但盘后锚点缺失，暂时无法计算价差。请点击“更新美股盘后锚点”。"
    if counts.get("binance_price_available", 0) <= 0 and counts.get("binance_total", 0) > 0:
        return "Binance 价格读取失败，请查看刷新诊断。"
    if counts.get("computable", 0) > 0 and scope != "全部可计算":
        return "当前筛选没有结果。可以切换到“全部可计算”。"
    if counts.get("anchor_missing", 0) > 0 and scope != "价格可用但锚点缺失":
        return "当前筛选没有结果。可以切换到“价格可用但锚点缺失”。"
    return "当前筛选下没有可展示的实时价差。可以切换筛选，或到“映射管理”里点击“一键同步 Binance 美股映射”。"


def _default_realtime_filter_scope(counts: dict[str, int]) -> str:
    if counts.get("异常偏离", 0) > 0:
        return "异常偏离"
    if counts.get("全部可计算", 0) > 0:
        return "全部可计算"
    if counts.get("锚点缺失", counts.get("价格可用但锚点缺失", 0)) > 0:
        return "锚点缺失"
    if counts.get("Binance 价格失败", 0) > 0:
        return "Binance 价格失败"
    return "全部可计算"


def _default_realtime_filter_pair(counts: dict[str, int]) -> tuple[str, str]:
    if counts.get("异常偏离", 0) > 0:
        return "全部可计算", "异常偏离"
    return "全部可计算", "全部状态"


def _scope_from_realtime_filter_label(label: object, options: list[str]) -> str:
    text = str(label or "")
    for option in options:
        if text.startswith(option):
            return option
    # Compatibility for old persisted radio labels.
    if text.startswith("全部 Binance 美股映射"):
        return "全部可计算"
    if text.startswith("价格可用但锚点缺失"):
        return "锚点缺失"
    if text.startswith("锚点缺失"):
        return "锚点缺失"
    return ""


def _label_index(labels: list[str], current_label: object, fallback_label: str) -> int:
    current = str(current_label or "")
    if current in labels:
        return labels.index(current)
    if fallback_label in labels:
        return labels.index(fallback_label)
    return 0


def _expected_realtime_anchor_date(now: datetime | None = None) -> str:
    current_et = (now or datetime.now(timezone.utc)).astimezone(ET)
    week_start = current_et.date() - timedelta(days=current_et.weekday())
    try:
        last_trading_day = get_last_us_trading_day_of_week(week_start)
    except Exception:
        return ""
    final_cutoff = datetime.combine(last_trading_day, time(20, 5), ET)
    if current_et >= final_cutoff:
        return last_trading_day.isoformat()
    try:
        return get_last_us_trading_day_of_week(week_start - timedelta(days=7)).isoformat()
    except Exception:
        return ""


def _rows_match_expected_anchor_date(rows: list[dict], expected_anchor_date: str) -> bool:
    if not expected_anchor_date:
        return True
    row_dates = [_row_anchor_date(row) for row in rows or [] if row.get("binance_symbol")]
    valid_dates = [item for item in row_dates if item]
    return bool(valid_dates) and max(valid_dates) >= expected_anchor_date


def _row_anchor_date(row: dict) -> str:
    for key in ("regular_close_date", "friday_close_date", "last_trading_day"):
        text = str(row.get(key) or "").strip()
        if len(text) >= 10:
            return text[:10]
    parsed = _parse_et_datetime(row.get("afterhours_reference_time"))
    return parsed.date().isoformat() if parsed is not None else ""


def _mask_stale_afterhours_rows(rows: list[dict], *, expected_anchor_date: str) -> list[dict]:
    masked: list[dict] = []
    for row in rows or []:
        item = dict(row)
        row_date = _row_anchor_date(item)
        if row_date and expected_anchor_date and row_date < expected_anchor_date:
            item["regular_close_price"] = None
            item["regular_close_date"] = ""
            item["friday_close"] = None
            item["friday_close_date"] = ""
            item["afterhours_reference_price"] = None
            item["afterhours_reference_time"] = ""
            item["afterhours_reference_source"] = ""
            item["afterhours_data_quality"] = "MISSING"
            item["afterhours_cache_status"] = "CACHE_DATE_MISMATCH"
            item["afterhours_anchor_status"] = ""
            item["afterhours_missing_reason"] = "CACHE_DATE_MISMATCH"
            item["spread_vs_afterhours_pct"] = None
            item["spread_vs_regular_close_pct"] = None
            item["primary_spread_pct"] = None
            item["primary_spread_anchor"] = "STALE_AFTERHOURS_REFERENCE"
        masked.append(item)
    return masked


def _build_weekend_spread_rows_with_feedback(
    watchlist: list[str],
    *,
    mapping: dict[str, dict],
    refresh_options: dict[str, bool] | None = None,
) -> tuple[list[dict], dict]:
    options = refresh_options or {}
    force_refresh = bool(options.get("refresh"))
    anchor_refresh = bool(options.get("anchor_refresh") or options.get("force_anchor_refresh"))
    force_anchor_refresh = anchor_refresh
    skipped_ignored = int(options.get("ignored_count") or 0)
    expected_anchor_date = _expected_realtime_anchor_date()
    cached = read_weekend_spread_snapshot(
        mapping=mapping,
        tickers=watchlist,
        expected_afterhours_date=expected_anchor_date,
    )
    cached_rows = list(cached.get("rows") or [])
    if not force_refresh and not anchor_refresh and cached.get("cache_state") == "ANCHOR_DATE_STALE":
        rows = build_weekend_spread_rows(
            watchlist,
            mapping=mapping,
            provider=_CachedRowBinanceProvider(cached_rows),
            afterhours_provider=default_afterhours_provider(),
            force_refresh=False,
            afterhours_force_refresh=False,
            expected_close_date=expected_anchor_date,
        )
        generated_at = datetime.now(timezone.utc).isoformat()
        if has_successful_price(rows) and _rows_match_expected_anchor_date(rows, expected_anchor_date):
            write_weekend_spread_snapshot(rows, mapping=mapping, tickers=watchlist, generated_at=datetime.now(timezone.utc))
            live_rows = annotate_cached_rows(rows, cache_state="API_LIVE", generated_at=generated_at)
            return live_rows, {
                "cache_state": "API_LIVE",
                "cache_message": "afterhours anchors refreshed from current cache",
                "rows": live_rows,
                "generated_at": generated_at,
                "last_failure": {},
            }
        masked_rows = _mask_stale_afterhours_rows(cached_rows, expected_anchor_date=expected_anchor_date)
        return (
            annotate_cached_rows(
                masked_rows,
                cache_state="ANCHOR_DATE_STALE",
                generated_at=str(cached.get("generated_at") or ""),
            ),
            cached,
        )
    if not force_refresh and not anchor_refresh and cached.get("rows"):
        masked_rows = _mask_stale_afterhours_rows(cached_rows, expected_anchor_date=expected_anchor_date)
        return (
            annotate_cached_rows(
                masked_rows,
                cache_state=str(cached.get("cache_state") or "FRESH"),
                generated_at=str(cached.get("generated_at") or ""),
            ),
            cached,
        )
    if not force_refresh and not anchor_refresh:
        return (
            build_weekend_spread_rows(
                watchlist,
                mapping=mapping,
                provider=_CacheOnlyBinanceProvider(),
                afterhours_provider=CachedAfterhoursProvider(NullAfterhoursProvider()),
                force_refresh=False,
                expected_close_date=expected_anchor_date,
            ),
            cached,
        )
    if anchor_refresh and not force_refresh:
        total = len([ticker for ticker in watchlist if str(ticker or "").strip()])
        progress_bar = st.progress(0.0)
        status_slot = st.empty()
        status_slot.caption(f"正在更新最后交易日盘后锚点：{total} 只股票")

        def update_anchor_progress(completed: int, total_count: int, ticker: str) -> None:
            ratio = completed / max(total_count, 1)
            progress_bar.progress(min(max(ratio, 0.0), 1.0))
            status_slot.caption(f"正在更新盘后锚点：{ticker}（{completed}/{total_count}）")

        rows = build_weekend_spread_rows(
            watchlist,
            mapping=mapping,
            provider=_CachedRowBinanceProvider(cached_rows),
            afterhours_provider=_fresh_afterhours_provider(),
            force_refresh=False,
            afterhours_force_refresh=force_anchor_refresh,
            progress_callback=update_anchor_progress,
            expected_close_date=expected_anchor_date,
        )
        generated_at = datetime.now(timezone.utc).isoformat()
        progress_bar.progress(1.0)
        if has_successful_price(rows):
            write_weekend_spread_snapshot(rows, mapping=mapping, tickers=watchlist, generated_at=datetime.now(timezone.utc))
        live_rows = annotate_cached_rows(rows, cache_state="API_LIVE", generated_at=generated_at)
        anchor_counts = _afterhours_counts(rows)
        anchor_message = _anchor_refresh_summary_text(anchor_counts)
        if int(anchor_counts.get("available") or 0) > 0 and int(anchor_counts.get("missing") or 0) <= 0:
            status_slot.success(anchor_message)
        elif int(anchor_counts.get("available") or 0) > 0:
            status_slot.warning(anchor_message)
        else:
            status_slot.warning(anchor_message)
        return live_rows, {
            "cache_state": "API_LIVE",
            "cache_message": "afterhours anchors updated",
            "rows": live_rows,
            "generated_at": generated_at,
            "last_failure": {},
        }
    total = len([ticker for ticker in watchlist if str(ticker or "").strip()])
    if total <= 0:
        st.info("当前没有可观察标的；请先扫描 Binance 映射或配置本模块映射。")
        return [], {"cache_state": "MISSING", "cache_message": "empty watchlist", "rows": []}

    progress_bar = st.progress(0.0)
    status_slot = st.empty()
    status_slot.caption(f"正在刷新 Binance 价格：{total} 只股票")

    def update_progress(completed: int, total_count: int, ticker: str) -> None:
        ratio = completed / max(total_count, 1)
        progress_bar.progress(min(max(ratio, 0.0), 1.0))
        status_slot.caption(f"正在刷新 Binance 价格：{ticker}（{completed}/{total_count}）")

    rows = build_weekend_spread_rows(
        watchlist,
        mapping=mapping,
        provider=_BulkRefreshBinanceProvider(),
        afterhours_provider=default_afterhours_provider() if cached.get("cache_state") == "ANCHOR_DATE_STALE" else _CachedRowAfterhoursProvider(cached_rows),
        force_refresh=True,
        afterhours_force_refresh=False,
        progress_callback=update_progress,
        expected_close_date=expected_anchor_date,
    )
    refresh_counts = _refresh_attempt_counts(rows, skipped_ignored=skipped_ignored)
    refresh_message = _refresh_summary_text(refresh_counts)
    if not refresh_message.startswith("刷新完成"):
        refresh_message = f"刷新完成：{refresh_message}"
    generated_at = datetime.now(timezone.utc).isoformat()
    progress_bar.progress(1.0)
    if has_successful_price(rows):
        write_weekend_spread_snapshot(rows, mapping=mapping, tickers=watchlist, generated_at=datetime.now(timezone.utc))
        live_rows = annotate_cached_rows(rows, cache_state="API_LIVE", generated_at=generated_at)
        status_slot.success(refresh_message)
        return live_rows, {
            "cache_state": "API_LIVE",
            "cache_message": "refreshed from Binance API",
            "rows": live_rows,
            "generated_at": generated_at,
            "last_failure": {},
        }
    if is_provider_failure(rows) and cached.get("rows"):
        error_message = _refresh_error_text(rows)
        write_weekend_spread_failure(error_message=error_message)
        stale_cached_rows = _mask_stale_afterhours_rows(cached_rows, expected_anchor_date=expected_anchor_date)
        fallback_rows = annotate_cached_rows(
            stale_cached_rows,
            cache_state="REFRESH_FAILED",
            generated_at=str(cached.get("generated_at") or ""),
        )
        status_slot.warning("刷新失败，已回退到缓存结果。")
        cache_status = dict(cached)
        cache_status["cache_state"] = "REFRESH_FAILED"
        cache_status["cache_message"] = error_message
        return fallback_rows, cache_status
    if is_provider_failure(rows):
        error_message = _refresh_error_text(rows)
        fallback_rows = build_weekend_spread_rows(
            watchlist,
            mapping=mapping,
            provider=_CacheOnlyBinanceProvider(allow_stale=True),
            afterhours_provider=CachedAfterhoursProvider(NullAfterhoursProvider()),
            force_refresh=False,
            expected_close_date=expected_anchor_date,
        )
        if has_successful_price(fallback_rows):
            fallback_rows = annotate_cached_rows(fallback_rows, cache_state="REFRESH_FAILED", generated_at="")
            status_slot.warning("刷新失败，已使用缓存中的 Binance 价格。")
            return fallback_rows, {
                "cache_state": "REFRESH_FAILED",
                "cache_message": error_message,
                "rows": fallback_rows,
                "generated_at": "",
                "last_failure": {"error_message": error_message},
            }
    status_slot.warning(refresh_message)
    return annotate_cached_rows(rows, cache_state="API_LIVE", generated_at=generated_at), {
        "cache_state": "API_LIVE",
        "cache_message": "refreshed without successful price",
        "rows": rows,
        "generated_at": generated_at,
        "last_failure": {},
    }


class _IdleBinanceProvider:
    def get_last_price(self, symbol: str, *, market_type: str = "usdm_futures", force_refresh: bool = False) -> dict:
        return {
            "symbol": str(symbol or "").strip().upper(),
            "last_price": None,
            "bid": None,
            "ask": None,
            "volume_24h": None,
            "funding_rate": None,
            "updated_at": "",
            "source": "not_requested",
            "market_type": market_type,
            "error": "price_not_loaded",
        }


class _CacheOnlyBinanceProvider:
    def __init__(
        self,
        *,
        cache_path: Path = DEFAULT_BINANCE_CACHE_PATH,
        ttl_seconds: int = 86_400,
        allow_stale: bool = False,
    ) -> None:
        self.cache_path = cache_path
        self.ttl_seconds = ttl_seconds
        self.allow_stale = allow_stale

    def get_last_price(self, symbol: str, *, market_type: str = "usdm_futures", force_refresh: bool = False) -> dict:
        normalized_symbol = str(symbol or "").strip().upper()
        normalized_market = normalize_market_type(market_type)
        cached = self._read_cached(f"{normalized_market}:{normalized_symbol}")
        if cached is None:
            return {
                "symbol": normalized_symbol,
                "last_price": None,
                "bid": None,
                "ask": None,
                "volume_24h": None,
                "funding_rate": None,
                "updated_at": "",
                "source": "cache_only_missing",
                "market_type": normalized_market,
                "error": "price_not_loaded",
            }
        cached["source"] = cached.get("source") or "binance_price_cache"
        cached["market_type"] = normalized_market
        cached["error"] = ""
        return cached

    def _read_cached(self, cache_key: str) -> dict | None:
        if not self.cache_path.exists():
            return None
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8") or "{}")
        except (OSError, json.JSONDecodeError):
            return None
        raw = payload.get(cache_key) if isinstance(payload, dict) else None
        if not isinstance(raw, dict):
            return None
        updated_at = _parse_utc_time(raw.get("updated_at"))
        is_stale = updated_at is None or datetime.now(timezone.utc) - updated_at > timedelta(seconds=self.ttl_seconds)
        if is_stale and not self.allow_stale:
            return None
        return {
            "symbol": str(raw.get("symbol") or cache_key.split(":", 1)[-1]),
            "last_price": raw.get("last_price"),
            "bid": raw.get("bid"),
            "ask": raw.get("ask"),
            "volume_24h": raw.get("volume_24h"),
            "funding_rate": raw.get("funding_rate"),
            "updated_at": str(raw.get("updated_at") or ""),
            "source": "stale_binance_price_cache" if is_stale else str(raw.get("source") or "binance_price_cache"),
            "cache_status": "STALE" if is_stale else "FRESH",
        }


class _BulkRefreshBinanceProvider:
    def __init__(self, provider: object | None = None) -> None:
        self.provider = provider or CachedBinancePriceProvider(BinanceHTTPPriceProvider(), ttl_seconds=60)
        self._price_map: dict[str, float] | None = None
        self._load_error = ""
        self._loaded_at = ""

    def get_last_price(self, symbol: str, *, market_type: str = "usdm_futures", force_refresh: bool = False) -> dict:
        normalized_symbol = str(symbol or "").strip().upper()
        normalized_market = normalize_market_type(market_type)
        if not normalized_symbol:
            return self._missing_snapshot(normalized_symbol, normalized_market, "missing_symbol")
        if normalized_market != "usdm_futures":
            return self._missing_snapshot(normalized_symbol, normalized_market, "unsupported_market")
        price_map = self._load_price_map()
        price = price_map.get(normalized_symbol)
        if price is None:
            return self._missing_snapshot(normalized_symbol, normalized_market, "price_not_loaded")
        return {
            "symbol": normalized_symbol,
            "last_price": price,
            "bid": None,
            "ask": None,
            "volume_24h": None,
            "funding_rate": None,
            "updated_at": self._loaded_at,
            "source": "binance_usdm_futures_bulk",
            "market_type": normalized_market,
            "error": "",
        }

    def _load_price_map(self) -> dict[str, float]:
        if self._price_map is not None:
            return self._price_map
        self._loaded_at = datetime.now(timezone.utc).isoformat()
        self._price_map = {}
        for candidate in self._provider_candidates():
            getter = getattr(candidate, "_get_market_payload", None)
            if not callable(getter):
                continue
            try:
                payload = getter("usdm_futures", "price", {})
            except Exception as exc:
                self._load_error = f"{type(exc).__name__}: {exc}"
                continue
            parsed = self._parse_price_payload(payload)
            if parsed:
                self._price_map = parsed
                self._load_error = ""
                return self._price_map
        return self._price_map

    def _provider_candidates(self) -> list[object]:
        candidates = [self.provider]
        wrapped = getattr(self.provider, "provider", None)
        if wrapped is not None:
            candidates.append(wrapped)
        return candidates

    @staticmethod
    def _parse_price_payload(payload: object) -> dict[str, float]:
        if not isinstance(payload, list):
            return {}
        result: dict[str, float] = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "").strip().upper()
            price = _number(item.get("price"))
            if symbol and price is not None:
                result[symbol] = price
        return result

    def _missing_snapshot(self, symbol: str, market_type: str, error: str) -> dict:
        return {
            "symbol": symbol,
            "last_price": None,
            "bid": None,
            "ask": None,
            "volume_24h": None,
            "funding_rate": None,
            "updated_at": self._loaded_at,
            "source": "binance_usdm_futures_bulk",
            "market_type": market_type,
            "error": self._load_error or error,
        }


def _fresh_afterhours_provider() -> CachedAfterhoursProvider:
    return CachedAfterhoursProvider(MultiProviderAfterhoursProvider(), fallback_on_error=False)


def _single_symbol_binance_provider() -> CachedBinancePriceProvider:
    return CachedBinancePriceProvider(BinanceHTTPPriceProvider(), ttl_seconds=0)


class _CachedRowAfterhoursProvider:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = [dict(row) for row in rows or []]

    def get_afterhours_reference(
        self,
        symbol: str,
        *,
        regular_close_date: str = "",
        force_refresh: bool = False,
    ) -> AfterhoursReference:
        normalized_symbol = str(symbol or "").strip().upper()
        row = next(
            (
                item
                for item in self.rows
                if str(item.get("ticker") or "").strip().upper() == normalized_symbol
                and (not regular_close_date or str(item.get("regular_close_date") or item.get("friday_close_date") or "").strip()[:10] == regular_close_date)
            ),
            {},
        )
        price = _number(row.get("afterhours_reference_price"))
        if price is None:
            return AfterhoursReference(
                symbol=normalized_symbol,
                data_quality="MISSING",
                missing_reason=str(row.get("afterhours_missing_reason") or "NO_AFTERHOURS_CACHE"),
                cache_status=str(row.get("afterhours_cache_status") or "CACHE_MISSING"),
            )
        return AfterhoursReference(
            symbol=normalized_symbol,
            reference_price=price,
            reference_time=str(row.get("afterhours_reference_time") or ""),
            reference_source=str(row.get("afterhours_reference_source") or "weekend_spread_snapshot"),
            bid=_number(row.get("afterhours_bid")),
            ask=_number(row.get("afterhours_ask")),
            mid=_number(row.get("afterhours_mid")),
            last_trade=_number(row.get("afterhours_last_trade")),
            volume=_number(row.get("afterhours_volume")),
            data_quality=str(row.get("afterhours_data_quality") or "CACHE"),
            missing_reason=str(row.get("afterhours_missing_reason") or ""),
            cache_status=str(row.get("afterhours_cache_status") or "CACHE_HIT"),
            week_id=str(row.get("afterhours_week_id") or ""),
            friday_date=str(row.get("regular_close_date") or row.get("friday_close_date") or regular_close_date or ""),
            provider_name=str(row.get("afterhours_provider_name") or "weekend_spread_snapshot"),
            anchor_status=str(row.get("afterhours_anchor_status") or ""),
            error_message=str(row.get("afterhours_error_message") or row.get("afterhours_error") or ""),
        )


class _CachedRowBinanceProvider:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = [dict(row) for row in rows or []]

    def get_last_price(self, symbol: str, *, market_type: str = "usdm_futures", force_refresh: bool = False) -> dict:
        normalized_symbol = str(symbol or "").strip().upper()
        row = next(
            (
                item
                for item in self.rows
                if str(item.get("binance_symbol") or "").strip().upper() == normalized_symbol
            ),
            {},
        )
        return {
            "symbol": normalized_symbol,
            "last_price": row.get("binance_last_price"),
            "bid": row.get("binance_bid"),
            "ask": row.get("binance_ask"),
            "volume_24h": row.get("binance_volume_24h"),
            "funding_rate": row.get("funding_rate"),
            "updated_at": row.get("updated_at") or "",
            "source": row.get("source") or "weekend_spread_snapshot",
            "market_type": market_type,
            "error": "" if row.get("binance_last_price") is not None else "price_not_loaded",
        }


def _render_primary_kpis(rows: list[dict], mapping_counts: dict[str, int]) -> None:
    abnormal_count = sum(1 for row in rows if row.get("alert_level") == "ABNORMAL")
    cols = st.columns(3)
    cols[0].metric("可观察标的", mapping_counts.get("price_row_count", 0))
    cols[1].metric("异常偏离", abnormal_count)
    cols[2].metric("Binance 数据", _binance_status_text(rows, mapping_counts.get("universe_mapping_count", 0)))


def _render_data_status_cards(rows: list[dict], mapping_counts: dict[str, int], local_mapping_path: Path, cache_status: dict | None = None) -> None:
    afterhours_counts = _afterhours_counts(rows)
    values = [
        ("可识别映射", f"{mapping_counts.get('universe_mapping_count', 0)} / {mapping_counts.get('universe_total', 0)}"),
        ("USDT-M 数据", _market_price_source_status(rows, "usdm_futures")),
        ("盘后锚点", _afterhours_anchor_status_text(rows, afterhours_counts)),
        ("最近更新", _latest_updated_at(rows) or "暂无"),
        ("缓存时间", _cache_generated_text(cache_status)),
        ("缓存状态", _cache_state_text(cache_status)),
    ]
    cols = st.columns(len(values))
    for col, (label, value) in zip(cols, values):
        col.caption(label)
        col.write(value)
    off_universe_note = _off_universe_mapping_note(mapping_counts)
    if local_mapping_path.exists() or mapping_counts.get("local_mapping_count", 0) > 0:
        st.caption(f"{off_universe_note}；local mapping：{local_mapping_path.as_posix()}")
    else:
        st.caption(f"{off_universe_note}；local mapping 尚未创建")


def _render_realtime_status_strip(rows: list[dict], mapping_counts: dict[str, int], cache_status: dict | None = None) -> None:
    counts = _realtime_observation_counts(rows, ignored_count=int(mapping_counts.get("ignored_count") or 0))
    items = [
        ("Binance 价格可用", f"{counts['binance_price_available']} / {counts['binance_total']}"),
        ("锚点可用", f"{counts['anchor_available']} / {counts['anchor_total']}"),
        ("可计算价差", str(counts["computable"])),
        ("异常偏离", str(counts["review"])),
        ("锚点缺失", str(counts["anchor_missing"])),
        ("已忽略", str(counts["ignored"])),
        ("不可用", str(counts["unavailable"])),
        ("最近更新", _latest_updated_at(rows) or _cache_generated_text(cache_status)),
    ]
    text = " ｜ ".join(f"{label}：{value}" for label, value in items)
    st.markdown(f'<div class="weekend-status-strip">{escape(text)}</div>', unsafe_allow_html=True)


def _render_realtime_summary_cards(rows: list[dict], mapping_counts: dict[str, int], cache_status: dict | None = None) -> None:
    counts = _realtime_observation_counts(rows, ignored_count=int(mapping_counts.get("ignored_count") or 0))
    max_premium = _realtime_extreme_row(rows, direction="premium")
    max_discount = _realtime_extreme_row(rows, direction="discount")
    values = [
        ("当前最大溢价", _summary_deviation_text(max_premium)),
        ("当前最大折价", _summary_deviation_text(max_discount)),
        ("可计算价差", str(counts.get("computable", 0))),
        ("异常偏离", str(counts.get("review", 0))),
        ("最近更新", _latest_updated_at(rows) or _cache_generated_text(cache_status) or "暂无"),
    ]
    columns = st.columns(len(values))
    for column, (label, value) in zip(columns, values):
        column.metric(label, value)


def _summary_deviation_text(row: dict | None) -> str:
    if row is None:
        return "暂无"
    ticker = str(row.get("ticker") or "").strip().upper() or "未识别"
    return f"{ticker} {_afterhours_spread_text(row.get('spread_vs_afterhours_pct'))}"


def _realtime_extreme_row(rows: list[dict], *, direction: str) -> dict | None:
    candidates = [
        row
        for row in rows or []
        if _is_realtime_main_row(row) and _number(row.get("spread_vs_afterhours_pct")) is not None
    ]
    if not candidates:
        return None
    if direction == "discount":
        negatives = [row for row in candidates if float(_number(row.get("spread_vs_afterhours_pct")) or 0) < 0]
        return min(negatives or candidates, key=lambda row: float(_number(row.get("spread_vs_afterhours_pct")) or 0))
    positives = [row for row in candidates if float(_number(row.get("spread_vs_afterhours_pct")) or 0) > 0]
    return max(positives or candidates, key=lambda row: float(_number(row.get("spread_vs_afterhours_pct")) or 0))


def _render_core_observation(rows: list[dict], mapping_counts: dict[str, int]) -> None:
    row = _strongest_signal_row(rows)
    if row is None:
        if mapping_counts.get("universe_mapping_count", 0) <= 0:
            message = "尚未同步 Binance 美股映射。请到“映射管理”里点击“一键同步 Binance 美股映射”后再观察。"
        elif _realtime_observation_counts(rows).get("anchor_missing", 0) > 0:
            message = "Binance 价格已读取，但盘后锚点缺失，暂时无法计算价差。请点击“更新盘后锚点”。"
        elif _realtime_observation_counts(rows).get("binance_price_available", 0) <= 0:
            message = "Binance 价格读取失败，请查看刷新诊断。"
        else:
            message = "当前没有显著异常偏离。可关注全部可计算价差表。"
        st.markdown(
            f'<section class="weekend-core-observation"><strong>核心观察</strong><br>{escape(message)}</section>',
            unsafe_allow_html=True,
        )
        return

    ticker = str(row.get("ticker") or "").strip().upper()
    spread_text = _afterhours_spread_text(row.get("spread_vs_afterhours_pct"))
    status_label = _realtime_row_status_label(row)
    if _realtime_row_status_key(row) == "review":
        message = (
            f"{ticker} 当前相对盘后锚点 {spread_text}，属于{status_label}。"
            "优先复核：Binance 映射、盘后锚点、是否为真实可参考价差。"
        )
    else:
        message = f"{ticker} 当前相对盘后锚点 {spread_text}，是当前最大偏离。继续观察价差是否扩大或收敛。"
    st.markdown(
        f'<section class="weekend-core-observation"><strong>核心观察</strong><br>{escape(message)}</section>',
        unsafe_allow_html=True,
    )


def _render_largest_deviation(rows: list[dict], mapping_counts: dict[str, int]) -> None:
    _render_core_observation(rows, mapping_counts)


def _render_strongest_signal(rows: list[dict], mapping_counts: dict[str, int]) -> None:
    _render_largest_deviation(rows, mapping_counts)


def _render_monitor_tab(rows: list[dict], ignored: dict[str, dict] | None = None) -> None:
    st.subheader("周末价差监控")
    st.caption("每 15 分钟扫描 Binance 美股映射价格，观察其相对美股最后交易日盘后锚点的偏离，以及近 15 分钟价差变化。本页仅用于休市期间观察，不构成交易建议。")
    candidate_rows = _monitor_candidate_rows(rows, ignored)
    source_rows = [row for row in candidate_rows if _row_has_afterhours_anchor(row)]
    latest_run = latest_monitor_run(DEFAULT_MONITOR_SNAPSHOT_PATH)
    snapshot_state = read_monitor_state(DEFAULT_MONITOR_SNAPSHOT_PATH)
    if snapshot_state.get("corrupted"):
        st.warning(str(snapshot_state.get("message") or "监控快照损坏，请重新扫描。"))
    process_state = _monitor_process_state()
    scan_clicked = False
    start_clicked = False
    stop_clicked = False

    cols = st.columns(3)
    scan_clicked = cols[0].button("立即扫描一次", key="weekend_spread_monitor_scan_once", width="stretch")
    start_clicked = cols[1].button("启动 15 分钟监控", key="weekend_spread_monitor_start", width="stretch", disabled=process_state["running"])
    stop_clicked = cols[2].button("停止监控", key="weekend_spread_monitor_stop", width="stretch", disabled=not process_state["running"])

    if scan_clicked:
        if not candidate_rows:
            st.warning("当前没有可监控标的：需要 Binance 合约、盘后锚点，并且未被忽略。")
        else:
            with st.spinner("正在扫描 Binance 美股映射价格..."):
                latest_run = run_monitor_scan(
                    candidate_rows,
                    price_provider=CachedBinancePriceProvider(BinanceHTTPPriceProvider(), ttl_seconds=45),
                    snapshot_path=DEFAULT_MONITOR_SNAPSHOT_PATH,
                )
            st.success(f"已完成本轮扫描：有效标的 {latest_run.get('summary', {}).get('valid_count', 0)} 个。")
    if start_clicked:
        result = _start_monitor_process()
        if result.get("ok"):
            st.success(str(result.get("message") or "已启动 15 分钟周末价差监控。"))
            st.rerun()
        else:
            st.warning(str(result.get("message") or "监控启动失败"))
    if stop_clicked:
        result = _stop_monitor_process()
        if result.get("ok"):
            st.success("已停止周末价差监控。")
            st.rerun()
        else:
            st.warning(str(result.get("message") or "未发现运行中的监控服务"))

    latest_run = latest_monitor_run(DEFAULT_MONITOR_SNAPSHOT_PATH)
    _render_monitor_status_strip(latest_run, candidate_rows, ignored or {}, process_state=_monitor_process_state())
    if latest_run is None:
        st.info("尚未启动周末监控。可以点击“立即扫描一次”查看当前价差，或启动 15 分钟监控。")
        return
    monitor_rows = list(latest_run.get("rows") or [])
    if not monitor_rows:
        st.info("最近一次扫描没有有效样本。请确认映射未被忽略，并且已有盘后锚点。")
        return
    _render_monitor_top_cards(monitor_rows)
    selected_scope = _render_monitor_filters(monitor_rows)
    filtered_rows = _filter_monitor_rows(monitor_rows, selected_scope)
    st.dataframe(_monitor_frame(filtered_rows), width="stretch", hide_index=True)
    _render_monitor_history()


def _monitor_source_rows(rows: list[dict], ignored: dict[str, dict] | None = None) -> list[dict]:
    return [row for row in _monitor_candidate_rows(rows, ignored) if _is_realtime_main_row(row)]


def _monitor_candidate_rows(rows: list[dict], ignored: dict[str, dict] | None = None) -> list[dict]:
    ignored = ignored or {}
    source: list[dict] = []
    for row in rows or []:
        ticker = str(row.get("ticker") or "").strip().upper()
        symbol = str(row.get("binance_symbol") or "").strip().upper()
        if not ticker or not symbol:
            continue
        if is_binance_symbol_ignored(ticker, symbol, ignored):
            continue
        if _is_other_tradfi_mapping(row) and not _is_manual_locked_mapping(row):
            continue
        source.append(dict(row))
    return source


def _render_monitor_status_strip(latest_run: dict | None, source_rows: list[dict], ignored: dict[str, dict], *, process_state: dict[str, object]) -> None:
    summary = dict((latest_run or {}).get("summary") or {})
    scan_time = str((latest_run or {}).get("scan_time") or "")
    next_scan = _next_monitor_scan_text(scan_time)
    status = str(process_state.get("status_label") or "")
    if not status:
        status = "最近一次扫描" if latest_run else "未启动"
    items = [
        ("监控状态", status),
        ("监控间隔", f"{DEFAULT_MONITOR_INTERVAL_MINUTES} 分钟"),
        ("最近扫描", _short_hkt_time(scan_time) if scan_time else "暂无"),
        ("下次预计", next_scan),
        ("本轮有效标的", str(summary.get("valid_count") or len([row for row in source_rows if _row_has_afterhours_anchor(row)]))),
        ("锚点缺失", str(summary.get("anchor_missing_count") or len([row for row in source_rows if not _row_has_afterhours_anchor(row)]))),
        ("已忽略", str(len(ignored))),
    ]
    text = " ｜ ".join(f"{label}：{value}" for label, value in items)
    st.markdown(f'<div class="weekend-status-strip">{escape(text)}</div>', unsafe_allow_html=True)


def _render_monitor_top_cards(rows: list[dict]) -> None:
    top = build_monitor_top_for_ui(rows)
    delta_label = _monitor_delta_label(rows)
    cards = [
        ("当前最大溢价", top.get("max_premium"), "premium_pct", "相对盘后锚点"),
        ("当前最大折价", top.get("max_discount"), "premium_pct", "相对盘后锚点"),
        (f"{delta_label}涨幅最大", top.get("max_binance_change"), "binance_15m_change_pct", "Binance 价格变化"),
        (f"{delta_label}价差扩大最快", top.get("fastest_premium_expand"), "premium_15m_change_pct", "溢价扩大"),
        (f"{delta_label}价差收敛最快", top.get("fastest_premium_converge"), "premium_15m_change_pct", "溢价收敛"),
    ]
    cols = st.columns(len(cards))
    for col, (title, row, metric_key, caption) in zip(cols, cards):
        ticker = str((row or {}).get("ticker") or "暂无")
        metric = _monitor_metric_text((row or {}).get(metric_key))
        col.metric(title, f"{ticker} {metric}")
        col.caption(caption if row else "等待下一轮比较")


def build_monitor_top_for_ui(rows: list[dict]) -> dict[str, dict | None]:
    from data.weekend_spread_monitor import build_monitor_top

    return build_monitor_top(rows)


def _render_monitor_filters(rows: list[dict]) -> str:
    delta_label = _monitor_delta_label(rows)
    options = ["全部可监控", "我的观察池", "我的持仓", "核心仓", "溢价超过 2%", "溢价超过 5%", "折价超过 2%", f"{delta_label}变化超过 1%"]
    labels = [f"{option} {_monitor_filter_count(rows, option)}" for option in options]
    selected = st.radio("监控筛选", labels, horizontal=True, label_visibility="collapsed", key="weekend_spread_monitor_filter")
    return options[labels.index(selected)]


def _monitor_filter_count(rows: list[dict], scope: str) -> int:
    return len(_filter_monitor_rows(rows, scope))


def _filter_monitor_rows(rows: list[dict], scope: str) -> list[dict]:
    if scope == "我的观察池":
        selected = [row for row in rows if row.get("is_watchlist")]
    elif scope == "我的持仓":
        selected = [row for row in rows if row.get("is_position")]
    elif scope == "核心仓":
        selected = [row for row in rows if row.get("is_core")]
    elif scope == "溢价超过 2%":
        selected = [row for row in rows if (_number(row.get("premium_pct")) or 0) >= 2]
    elif scope == "溢价超过 5%":
        selected = [row for row in rows if (_number(row.get("premium_pct")) or 0) >= 5]
    elif scope == "折价超过 2%":
        selected = [row for row in rows if (_number(row.get("premium_pct")) or 0) <= -2]
    elif scope.endswith("变化超过 1%"):
        selected = [row for row in rows if abs(_number(row.get("binance_15m_change_pct")) or 0) >= 1]
    else:
        selected = list(rows)
    return sorted(selected, key=lambda row: (-abs(_number(row.get("premium_pct")) or 0), -abs(_number(row.get("binance_15m_change_pct")) or 0), str(row.get("ticker") or "")))


def _monitor_frame(rows: list[dict]) -> pd.DataFrame:
    delta_label = _monitor_delta_label(rows)
    change_col = f"{delta_label} Binance 涨跌%"
    spread_change_col = f"{delta_label}价差变化"
    columns = ["股票", "Binance 合约", "盘后锚点", "Binance 当前价", "当前价差%", change_col, spread_change_col, "状态", "更新时间"]
    if not rows:
        return pd.DataFrame(columns=columns)
    records = []
    for row in rows:
        records.append(
            {
                "股票": row.get("ticker"),
                "Binance 合约": row.get("binance_symbol"),
                "盘后锚点": _money_text(row.get("anchor_price")),
                "Binance 当前价": _money_text(row.get("binance_price")),
                "当前价差%": _percent_text(row.get("premium_pct")),
                change_col: _monitor_metric_text(row.get("binance_15m_change_pct")),
                spread_change_col: _monitor_pct_point_text(row.get("premium_15m_change_pct")),
                "状态": row.get("status") or "正常",
                "更新时间": _short_hkt_time(row.get("scan_time")),
            }
        )
    return pd.DataFrame(records, columns=columns)


def _render_monitor_history() -> None:
    with st.expander("监控历史", expanded=False):
        history = monitor_history_rows(recent_monitor_runs(DEFAULT_MONITOR_SNAPSHOT_PATH, limit=10))
        if not history:
            st.caption("暂无监控历史。")
            return
        display = pd.DataFrame(history)
        display = display.rename(
            columns={
                "scan_time": "扫描时间",
                "valid_count": "有效标的数",
                "max_premium": "最大溢价",
                "max_discount": "最大折价",
                "max_15m_change": "最大较上一轮涨幅",
                "max_premium_expand": "最大较上一轮价差扩大",
                "attention_count": "异常数量",
            }
        )
        if "扫描时间" in display:
            display["扫描时间"] = display["扫描时间"].map(_short_hkt_time)
        st.dataframe(display, width="stretch", hide_index=True)


def _monitor_process_state() -> dict[str, object]:
    payload = _read_json_file(MONITOR_PROCESS_PATH)
    pid = int(payload.get("pid") or 0) if isinstance(payload, dict) else 0
    running = _is_process_running(pid)
    raw_status = str(payload.get("status") or "").strip() if isinstance(payload, dict) else ""
    if raw_status == "running" and pid and not running:
        payload = dict(payload)
        payload["status"] = "exited"
        payload["exited_at"] = datetime.now(timezone.utc).isoformat()
        _write_json_file(MONITOR_PROCESS_PATH, payload)
        raw_status = "exited"
    status = "running" if running else (raw_status or "not_started")
    status_label = {
        "running": "运行中",
        "stopped": "已停止",
        "exited": "进程已退出",
        "not_started": "未启动",
    }.get(status, "未启动")
    return {
        "pid": pid,
        "running": running,
        "status": status,
        "status_label": status_label,
        "started_at": str(payload.get("started_at") or "") if isinstance(payload, dict) else "",
        "interval_minutes": int(payload.get("interval_minutes") or DEFAULT_MONITOR_INTERVAL_MINUTES) if isinstance(payload, dict) else DEFAULT_MONITOR_INTERVAL_MINUTES,
        "command": str(payload.get("command") or "") if isinstance(payload, dict) else "",
    }


def _start_monitor_process() -> dict[str, object]:
    current = _monitor_process_state()
    if current.get("running"):
        return {"ok": True, "already_running": True, "message": "监控已在运行。"}
    script = Path(__file__).resolve().parents[1] / "tools" / "weekend_spread_monitor.py"
    if not script.exists():
        return {"ok": False, "message": "未找到监控脚本"}
    MONITOR_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_handle = MONITOR_LOG_PATH.open("a", encoding="utf-8")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        command = [sys.executable, str(script), "--interval-minutes", str(DEFAULT_MONITOR_INTERVAL_MINUTES), "--all"]
        process = subprocess.Popen(
            command,
            cwd=str(Path(__file__).resolve().parents[1]),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=log_handle,
            creationflags=creationflags,
        )
    except Exception as exc:
        log_handle.close()
        return {"ok": False, "message": f"监控启动失败：{exc}"}
    log_handle.close()
    _write_json_file(
        MONITOR_PROCESS_PATH,
        {
            "pid": process.pid,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "interval_minutes": DEFAULT_MONITOR_INTERVAL_MINUTES,
            "command": " ".join(command),
            "status": "running",
            "log_path": str(MONITOR_LOG_PATH),
        },
    )
    return {"ok": True, "pid": process.pid, "message": "已启动 15 分钟周末价差监控。"}


def _stop_monitor_process() -> dict[str, object]:
    state = _monitor_process_state()
    pid = int(state.get("pid") or 0)
    if not pid or not state.get("running"):
        _write_json_file(
            MONITOR_PROCESS_PATH,
            {
                "pid": pid,
                "started_at": str(state.get("started_at") or ""),
                "interval_minutes": state.get("interval_minutes") or DEFAULT_MONITOR_INTERVAL_MINUTES,
                "command": str(state.get("command") or ""),
                "status": "stopped" if state.get("status") != "exited" else "exited",
                "stopped_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return {"ok": False, "message": "未发现运行中的监控服务"}
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True, timeout=10)
        else:
            os.kill(pid, 15)
    except Exception as exc:
        return {"ok": False, "message": f"停止失败：{exc}"}
    _write_json_file(
        MONITOR_PROCESS_PATH,
        {
            "pid": pid,
            "started_at": str(state.get("started_at") or ""),
            "interval_minutes": state.get("interval_minutes") or DEFAULT_MONITOR_INTERVAL_MINUTES,
            "command": str(state.get("command") or ""),
            "status": "stopped",
            "stopped_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return {"ok": True}


def _is_process_running(pid: int) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception:
            return False
        return f'"{pid}"' in result.stdout or f",{pid}," in result.stdout
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _next_monitor_scan_text(scan_time: object) -> str:
    parsed = _parse_utc_time(scan_time)
    if parsed is None:
        return "暂无"
    return _short_hkt_time((parsed + timedelta(minutes=DEFAULT_MONITOR_INTERVAL_MINUTES)).isoformat())


def _monitor_metric_text(value: object) -> str:
    number = _number(value)
    return "等待下一轮比较" if number is None else f"{number:+.2f}%"


def _monitor_pct_point_text(value: object) -> str:
    number = _number(value)
    return "等待下一轮比较" if number is None else f"{number:+.2f} pct"


def _monitor_delta_label(rows: list[dict]) -> str:
    elapsed_values = [
        elapsed
        for elapsed in (_number(row.get("elapsed_minutes")) for row in rows or [])
        if elapsed is not None and elapsed > 0
    ]
    if not elapsed_values:
        return "较上一轮"
    average_elapsed = sum(elapsed_values) / len(elapsed_values)
    return "近15分钟" if 13 <= average_elapsed <= 17 else "较上一轮"


def _read_json_file(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json_file(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    try:
        os.replace(temp_path, path)
    except PermissionError:
        # Windows can briefly lock the target while Streamlit/tests read it.
        path.write_text(temp_path.read_text(encoding="utf-8"), encoding="utf-8")
        try:
            temp_path.unlink()
        except OSError:
            pass


def _afterhours_counts(rows: list[dict]) -> dict[str, int]:
    counts = {
        "total": 0,
        "available": 0,
        "cache": 0,
        "fallback": 0,
        "missing": 0,
        "provisional": 0,
    }
    for row in rows or []:
        if not str(row.get("binance_symbol") or "").strip():
            continue
        counts["total"] += 1
        afterhours_price = _number(row.get("afterhours_reference_price"))
        regular_close = _number(row.get("regular_close_price") or row.get("friday_close"))
        if afterhours_price is not None:
            counts["available"] += 1
            cache_status = str(row.get("afterhours_cache_status") or "").strip().upper()
            if cache_status in {"CACHE_HIT", "CACHE_FALLBACK"}:
                counts["cache"] += 1
            anchor_status = str(row.get("afterhours_anchor_status") or "").strip().upper()
            if anchor_status == "PROVISIONAL":
                counts["provisional"] += 1
            continue
        if regular_close is not None:
            counts["fallback"] += 1
        counts["missing"] += 1
    return counts


def _afterhours_anchor_status_text(rows: list[dict], afterhours_counts: dict[str, int]) -> str:
    total = int(afterhours_counts.get("total") or 0)
    if total <= 0:
        return "无映射标的"
    available = int(afterhours_counts.get("available") or 0)
    fallback = int(afterhours_counts.get("fallback") or 0)
    missing = int(afterhours_counts.get("missing") or 0)
    if available == total:
        cache = int(afterhours_counts.get("cache") or 0)
        if cache:
            return f"{available}/{total} 已缓存"
        return f"{available}/{total} 已读取"
    parts = [f"{available}/{total} 已读取"]
    if fallback:
        parts.append(f"回退 {fallback}")
    if missing:
        parts.append(f"缺失 {missing}")
    return "，".join(parts)


def _anchor_refresh_summary_text(afterhours_counts: dict[str, int]) -> str:
    total = int(afterhours_counts.get("total") or 0)
    available = int(afterhours_counts.get("available") or 0)
    missing = int(afterhours_counts.get("missing") or 0)
    fallback = int(afterhours_counts.get("fallback") or 0)
    if total <= 0:
        return "没有需要更新盘后锚点的映射。"
    parts = [f"盘后锚点更新完成：成功 {available}/{total}"]
    if missing:
        parts.append(f"缺失 {missing}")
    if fallback:
        parts.append(f"常规收盘回退 {fallback}")
    if available <= 0:
        parts.append("未读取到新的盘后锚点，请查看刷新诊断。")
    return "，".join(parts)


def _realtime_observation_counts(rows: list[dict], *, ignored_count: int = 0) -> dict[str, int]:
    active_rows = [row for row in rows or [] if str(row.get("binance_symbol") or "").strip()]
    counts = {
        "binance_total": len(active_rows),
        "binance_price_available": 0,
        "anchor_total": len(active_rows),
        "anchor_available": 0,
        "computable": 0,
        "anchor_missing": 0,
        "ignored": ignored_count,
        "unavailable": 0,
        "review": 0,
    }
    for row in active_rows:
        has_price = _row_has_binance_price(row)
        has_anchor = _row_has_afterhours_anchor(row)
        if has_price:
            counts["binance_price_available"] += 1
        if has_anchor:
            counts["anchor_available"] += 1
        key = _realtime_row_status_key(row)
        if key == "anchor_missing":
            counts["anchor_missing"] += 1
        elif key == "binance_failed":
            counts["unavailable"] += 1
        elif key == "review":
            counts["review"] += 1
        elif key == "normal":
            counts["computable"] += 1
        elif key == "unavailable":
            counts["unavailable"] += 1
        if _is_realtime_main_row(row):
            counts["computable"] += 0 if key == "normal" else 1
    return counts


def _refresh_attempt_counts(rows: list[dict], *, skipped_ignored: int = 0) -> dict[str, int]:
    attempted_rows = [row for row in rows or [] if str(row.get("binance_symbol") or "").strip()]
    success = sum(1 for row in attempted_rows if _row_has_binance_price(row))
    return {
        "attempted": len(attempted_rows),
        "success": success,
        "failed": max(len(attempted_rows) - success, 0),
        "skipped_ignored": skipped_ignored,
    }


def _refresh_summary_text(counts: dict[str, int]) -> str:
    return (
        "刷新完成：本次刷新尝试 "
        f"{counts.get('attempted', 0)} 个 Binance 合约，成功 {counts.get('success', 0)} 个，"
        f"失败 {counts.get('failed', 0)} 个，跳过已忽略 {counts.get('skipped_ignored', 0)} 个。"
    )


def _realtime_status_counts(rows: list[dict]) -> dict[str, int]:
    counts = {"normal": 0, "review": 0, "anchor_missing": 0, "binance_failed": 0, "unavailable": 0}
    for row in rows:
        key = _realtime_row_status_key(row)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _realtime_sort_key(row: dict) -> tuple[int, float, int, str]:
    priority = {"review": 0, "normal": 1, "unavailable": 2}
    key = _realtime_row_status_key(row)
    spread = _number(row.get("spread_vs_afterhours_pct"))
    spread_abs = abs(spread) if spread is not None else -1.0
    relation_rank = 0 if row.get("is_position") else 1 if row.get("is_watchlist") else 2
    return (priority.get(key, 9), -spread_abs, relation_rank, str(row.get("ticker") or ""))


def _realtime_row_status_key(row: dict) -> str:
    status = str(row.get("status") or "").upper()
    if status in {"NO_MAPPING", "BINANCE_UNAVAILABLE", "INVALID_SYMBOL", "PRICE_NOT_LOADED"}:
        return "binance_failed" if str(row.get("binance_symbol") or "").strip() else "unavailable"
    label = _mapping_display_label_for_row(row)
    if label in {MAPPING_IGNORED_LABEL, MAPPING_UNAVAILABLE_LABEL, MAPPING_INVALID, "无映射"}:
        return "unavailable"
    if not _row_has_binance_price(row):
        return "binance_failed" if str(row.get("binance_symbol") or "").strip() else "unavailable"
    if not _row_has_afterhours_anchor(row):
        return "anchor_missing"
    if status == "UNIT_UNCONFIRMED":
        return "review"
    if label in {MAPPING_ANOMALY_LABEL, MAPPING_REVIEW, "异常复核"}:
        return "review"
    spread = _number(row.get("spread_vs_afterhours_pct"))
    if spread is None:
        return "unavailable"
    if abs(spread) >= 8:
        return "review"
    return "normal"


def _realtime_row_status_label(row: dict) -> str:
    return {
        "normal": "映射可用",
        "review": "价格异常",
        "anchor_missing": "锚点缺失",
        "binance_failed": "Binance 价格失败",
        "unavailable": "不可用",
    }.get(_realtime_row_status_key(row), "不可用")


def _realtime_row_status_reason(row: dict) -> str:
    key = _realtime_row_status_key(row)
    if key == "review":
        return "相对盘后锚点偏离过大，先按价格异常处理。"
    if key == "anchor_missing":
        return "Binance 价格已读取，但盘后锚点缺失，暂时无法计算价差。"
    if key == "binance_failed":
        return _refresh_diagnostic_reason(row)
    if key == "unavailable":
        return "映射不可用、已忽略，或缺少 Binance 合约。"
    return "Binance 价格读取成功，映射可用于价差观察。"


def _mapping_display_label_for_row(row: dict) -> str:
    status = str(row.get("status") or "").upper()
    if status == "NO_MAPPING" or not str(row.get("binance_symbol") or "").strip():
        return "无映射"
    if status in {"INVALID_SYMBOL", "UNIT_UNCONFIRMED"}:
        return MAPPING_UNAVAILABLE_LABEL if status == "INVALID_SYMBOL" else MAPPING_ANOMALY_LABEL
    quality = str(row.get("mapping_quality") or row.get("mapping_status") or "").strip()
    if quality in {MAPPING_IGNORED, MAPPING_IGNORED_LABEL, "ignored"}:
        return MAPPING_IGNORED_LABEL
    if _is_manual_locked_mapping(row):
        return "人工锁定"
    if _is_other_tradfi_mapping(row):
        return MAPPING_UNAVAILABLE_LABEL
    if quality in {MAPPING_AVAILABLE, MAPPING_AVAILABLE_LABEL, MAPPING_US_EQUITY_VERIFIED, MAPPING_ETF_VERIFIED, "自动可用"}:
        return MAPPING_AVAILABLE_LABEL
    if quality in {MAPPING_PRICE_ANOMALY, MAPPING_ANOMALY_LABEL, MAPPING_REVIEW, "异常复核", "需确认"}:
        return MAPPING_ANOMALY_LABEL
    if quality in {MAPPING_UNAVAILABLE, MAPPING_UNAVAILABLE_LABEL, MAPPING_INVALID, "无效映射"}:
        return MAPPING_UNAVAILABLE_LABEL
    if quality in {MAPPING_PENDING_VERIFICATION, MAPPING_PRICE_UNVERIFIED, "自动可用，价格校验不足", MAPPING_ANCHOR_MISSING, "锚点缺失"}:
        return MAPPING_AVAILABLE_LABEL if _row_has_binance_price(row) else MAPPING_UNAVAILABLE_LABEL
    confidence = str(row.get("mapping_confidence") or row.get("mapping_status") or "").strip().lower()
    if confidence == "confirmed" or confidence == "人工锁定":
        return "人工锁定"
    if confidence == "auto_available":
        return MAPPING_AVAILABLE_LABEL
    if _row_has_binance_price(row):
        return MAPPING_AVAILABLE_LABEL
    return MAPPING_UNAVAILABLE_LABEL


def _row_has_afterhours_anchor(row: dict) -> bool:
    return _number(row.get("afterhours_reference_price")) is not None


def _row_has_binance_price(row: dict) -> bool:
    return _number(row.get("adjusted_binance_price") or row.get("binance_last_price")) is not None


def _is_manual_locked_mapping(row: dict) -> bool:
    quality = str(row.get("mapping_quality") or row.get("mapping_status") or "").strip()
    confidence = str(row.get("mapping_confidence") or row.get("mapping_status") or "").strip().lower()
    return bool(row.get("manually_locked")) or quality in {MAPPING_MANUAL_LOCKED, "人工锁定"} or confidence in {"confirmed", "人工锁定"}


def _is_other_tradfi_mapping(row: dict) -> bool:
    quality = str(row.get("mapping_quality") or row.get("mapping_status") or "").strip()
    bucket = str(row.get("tradfi_bucket") or "").strip().upper()
    underlying = str(row.get("underlying_type") or "").strip().upper()
    category = str(row.get("binance_category") or "").strip().upper()
    note = str(row.get("mapping_risk") or row.get("risk_note") or row.get("mapping_quality_reason") or row.get("reason") or "").upper()
    return (
        quality in {MAPPING_OTHER_TRADFI, "其他 TradFi"}
        or bucket == "OTHER_TRADFI"
        or underlying in {"COIN", "COMMODITY", "KR_EQUITY", "INDEX", "PREMARKET"}
        or any(token in category for token in ("其他 TRADFI", "商品", "指数", "RWA", "KR EQUITY"))
        or any(token in note for token in ("其他 TRADFI", "非美股", "商品", "指数", "RWA", "KR EQUITY"))
    )


def _is_realtime_main_row(row: dict) -> bool:
    label = _mapping_display_label_for_row(row)
    if label in {MAPPING_AVAILABLE_LABEL, MAPPING_ANOMALY_LABEL, MAPPING_MANUAL_LOCKED}:
        return _row_has_binance_price(row) and _row_has_afterhours_anchor(row)
    return False


def _is_pending_anomaly_row(row: dict) -> bool:
    if not (_row_has_binance_price(row) and _row_has_afterhours_anchor(row)):
        return False
    spread = _number(row.get("spread_vs_afterhours_pct") if row.get("spread_vs_afterhours_pct") is not None else row.get("spread_pct"))
    return spread is not None and abs(spread) >= 8


def _anchor_display_label_for_row(row: dict) -> str:
    if _number(row.get("afterhours_reference_price")) is not None:
        quality = str(row.get("afterhours_quality") or row.get("afterhours_cache_status") or "").upper()
        if "FALLBACK" in quality or "REGULAR" in quality:
            return "常规收盘回退"
        return "盘后锚点"
    return "锚点缺失"


def _render_history_tab() -> None:
    st.info("历史观察样本只用于数据复核，不写入主交易系统。")


def _render_backtest_tab(watchlist: list[str], mapping: dict[str, dict]) -> None:
    st.subheader("历史回测")
    st.caption("正式回测路径：盘后锚点 → Binance 周末高点 → 夜盘价格。系统统计周末冲高、高点回落、最终传导和高点兑现率。")




    effective_mapping = {str(key or "").strip().upper(): dict(value or {}) for key, value in (mapping or {}).items()}
    all_tickers = _weekend_scope_tickers(watchlist, effective_mapping)
    if "NVDA" in all_tickers:
        effective_mapping.setdefault(
            "NVDA",
            {
                "enabled": True,
                "binance_symbol": "NVDAUSDT",
                "market_type": "usdm_futures",
                "quote_currency": "USDT",
                "unit_multiplier": 1,
                "mapping_confidence": "confirmed",
            },
        )
    if not all_tickers:
        st.info("当前没有可用于周末价差的 Binance 美股映射。请先扫描 Binance 映射或配置本模块映射缓存。")
        _render_tradingview_backfill_tools()
        return
    opening_anchor = "overnight"
    open_window = 15
    weeks = _safe_backtest_weeks(st.session_state.get("weekend_spread_backtest_weeks"))
    include_unconfirmed = bool(st.session_state.get("weekend_spread_backtest_include_unconfirmed") or False)
    preliminary = build_weekend_backtest_preflight(
        all_tickers,
        mapping=effective_mapping,
        anchors=_backtest_anchor_mapping(all_tickers, weeks=weeks),
        include_unconfirmed=include_unconfirmed,
    )
    options = [str(ticker or "").strip().upper() for ticker in preliminary.get("eligible_tickers") or []]
    selected_default = str(st.session_state.get("weekend_spread_backtest_ticker") or (options[0] if options else "")).strip().upper()
    forced_blocked_selection = selected_default if selected_default and selected_default in all_tickers and selected_default not in options else ""
    if not options:
        st.info(f"当前无法运行回测：{_backtest_block_text(str(preliminary.get('primary_block_reason') or 'NO_MAPPING'))}")
        if forced_blocked_selection:
            forced_preflight = build_weekend_backtest_preflight(
                [forced_blocked_selection],
                mapping=effective_mapping,
                anchors=_backtest_anchor_mapping([forced_blocked_selection], weeks=weeks),
                include_unconfirmed=include_unconfirmed,
                ticker_filter=forced_blocked_selection,
            )
            st.warning(_backtest_block_text(str(forced_preflight.get("primary_block_reason") or "MAPPING_NOT_VERIFIED")))
        with st.expander("查看排除原因", expanded=False):
            st.dataframe(_backtest_exclusion_frame(list(preliminary.get("excluded") or [])), width="stretch", hide_index=True)
        _render_tradingview_backfill_tools()
        return
    selected = selected_default if selected_default in options else options[0]
    if st.session_state.get("weekend_spread_backtest_ticker") not in options:
        st.session_state["weekend_spread_backtest_ticker"] = selected
    anchors = _backtest_anchor_mapping([selected], weeks=weeks)
    preflight = build_weekend_backtest_preflight(
        [selected],
        mapping=effective_mapping,
        anchors=anchors,
        include_unconfirmed=include_unconfirmed,
        ticker_filter=selected,
    )
    run_clicked = False
    clear_clicked = False

    cached_result = dict(st.session_state.get("weekend_spread_backtest_cache") or load_backtest_results())
    initial_results = _current_backtest_results(
        st.session_state.get("weekend_spread_backtest_results"),
        cached_result,
        preflight=preflight,
        mapping=effective_mapping,
        include_unconfirmed=include_unconfirmed,
    )
    display_weeks = _backtest_result_weeks(cached_result, weeks)
    if initial_results:
        _render_backtest_result_sections(
            initial_results,
            cached_result=cached_result,
            weeks=display_weeks,
            include_unconfirmed=include_unconfirmed,
        )
    else:
        if cached_result.get("error_message"):
            st.warning(f"上次运行失败：{cached_result.get('error_message')}")
        else:
            st.info(_backtest_empty_prompt(display_weeks))

    with st.expander("回测设置", expanded=False):
        include_unconfirmed = st.checkbox(
            "显示不可用映射排除原因",
            value=include_unconfirmed,
            key="weekend_spread_backtest_include_unconfirmed",
            help="已忽略和不可用映射不会进入正式回测，只在排除原因中展示。",
        )
        cols = st.columns([1.2, 1, 1.35, 1, 1.4])
        selected = cols[0].selectbox("标的", options, key="weekend_spread_backtest_ticker")
        weeks = int(cols[1].number_input("回测周数", min_value=1, max_value=12, value=weeks, step=1, key="weekend_spread_backtest_weeks"))
        window_label = cols[2].selectbox(
            "夜盘开盘窗口",
            ["严格 1 分钟", "2 分钟", "5 分钟", "15 分钟", "30 分钟"],
            index=3,
            key="weekend_spread_backtest_open_window_label",
            help="窗口越大，样本越多；延迟成交样本不能等同于首分钟平单价格。",
        )
        open_window = 1 if window_label == "严格 1 分钟" else int(window_label.split(" ")[0])
        require_exact_p2 = window_label == "严格 1 分钟"
        cols[3].markdown("**夜盘窗口**")
        cols[3].caption("20:00-20:01 ET" if require_exact_p2 else f"20:00-20:{open_window:02d} ET")
        cols[4].markdown("**开盘锚点**")
        cols[4].caption("下周第一个交易日夜盘 / 美东 20:00 ET")
        st.caption("窗口越大，样本越多，但延迟成交样本不能等同于首分钟平单价格。")
        anchors = _backtest_anchor_mapping([selected], weeks=weeks)
        preflight = build_weekend_backtest_preflight(
            [selected],
            mapping=effective_mapping,
            anchors=anchors,
            include_unconfirmed=include_unconfirmed,
            ticker_filter=selected,
        )
        _render_backtest_preflight(preflight)
        if not preflight.get("can_run"):
            st.warning(_backtest_block_text(str(preflight.get("primary_block_reason") or "")))
        op_cols = st.columns([2, 1, 1, 2])
        run_clicked = op_cols[0].button(
            _backtest_run_button_label(weeks),
            width="stretch",
            key="weekend_spread_run_backtest",
            disabled=not bool(preflight.get("can_run")),
        )
        clear_clicked = op_cols[1].button("清空本次结果", width="stretch", key="weekend_spread_clear_backtest_view")
        self_check_clicked = op_cols[2].button("夜盘数据源自检", width="stretch", key="weekend_spread_overnight_provider_self_check")
        with op_cols[3].expander("查看排除原因", expanded=False):
            excluded = list(preflight.get("excluded") or preliminary.get("excluded") or [])
            st.dataframe(_backtest_exclusion_frame(excluded), width="stretch", hide_index=True)
        if self_check_clicked:
            with st.spinner(f"正在检查 {selected} 下周第一个交易日夜盘首分钟 1m bar..."):
                _render_overnight_provider_self_check(build_overnight_provider_self_check(selected))

    if not preflight.get("can_run"):
        st.session_state["weekend_spread_backtest_results"] = []
        st.session_state["weekend_spread_backtest_cache"] = clear_backtest_view_state()
        st.info(f"当前无法运行回测：{_backtest_block_text(str(preflight.get('primary_block_reason') or 'NO_MAPPING'))}")
        _render_tradingview_backfill_tools()
        return
    if clear_clicked:
        st.session_state["weekend_spread_backtest_results"] = []
        st.session_state["weekend_spread_backtest_cache"] = clear_backtest_view_state()
        st.info("已清空本次历史回测缓存。")
        st.rerun()
    if run_clicked:
        tickers = list(preflight.get("eligible_tickers") or [])
        anchors = _backtest_anchor_mapping(
            tickers,
            weeks=weeks,
            afterhours_provider=default_afterhours_provider(),
        )
        preflight = build_weekend_backtest_preflight(
            tickers,
            mapping=effective_mapping,
            anchors=anchors,
            include_unconfirmed=include_unconfirmed,
            ticker_filter=selected,
        )
        progress_bar = st.progress(0.0)
        status_slot = st.empty()
        status_slot.caption(f"正在运行历史回测：{len(tickers)} 只标的，近 {weeks} 周")
        results = run_weekend_basis_backtest(
            tickers,
            mapping=effective_mapping,
            anchors=anchors,
            weeks=weeks,
            open_window_minutes=open_window,
            opening_anchor=opening_anchor,
            overnight_provider=default_overnight_price_provider(),
            allow_anchor_fallback=False,
            require_exact_broker_open=require_exact_p2,
        )
        progress_bar.progress(1.0)
        failed = [
            row
            for row in results
            if str(row.get("transmission_data_quality") or row.get("data_quality") or "")
            in {
                "BINANCE_KLINE_UNAVAILABLE",
                "NO_BROKER_OVERNIGHT_BAR",
                "MISSING_STOCK_FIRST_BAR",
                "MISSING_OVERNIGHT_FIRST_1M",
                "OVERNIGHT_PROVIDER_MISSING",
                "NO_AFTERHOURS_CLOSE",
                "CONTRACT_MISSING",
                "HOLIDAY_OR_NO_SESSION",
                "STALE_OR_MISALIGNED",
                "INVALID",
                "NO_PRICE_ANCHOR",
            }
        ]
        error_message = _backtest_error_message(failed)
        saved = save_backtest_results(
            results,
            preflight=preflight,
            params={
                "ticker": selected,
                "weeks": weeks,
                "open_window": open_window,
                "opening_anchor": opening_anchor,
                "backtest_mode": "严格首分钟" if require_exact_p2 else "首个有效夜盘价",
                "p2_open_window_label": window_label,
                "include_unconfirmed": include_unconfirmed,
            },
            error_message=error_message,
        )
        st.session_state["weekend_spread_backtest_results"] = results
        st.session_state["weekend_spread_backtest_cache"] = saved
        afterhours_result_note = _historical_afterhours_result_summary_text(results)
        if error_message:
            status_slot.warning(f"{error_message}\n\n{afterhours_result_note}")
        else:
            status_slot.success(f"回测完成：{len(results)} 条结果。{afterhours_result_note}")
        st.rerun()

    _render_tradingview_backfill_tools()
    _render_backfill_audit_area_v2(watchlist, mapping, anchors)
    _render_backtest_advanced_records()


def _render_backtest_result_sections(
    results: list[dict],
    *,
    cached_result: dict,
    weeks: int,
    include_unconfirmed: bool = False,
) -> None:
    last_run_at = str(cached_result.get("last_run_at") or "")
    if last_run_at:
        st.caption(f"上次运行：{_short_hkt_time(last_run_at)}")
    if include_unconfirmed:
        st.caption("已忽略和不可用映射不会进入正式回测。")

    review_rows = _weekend_review_rows(results)
    if not review_rows:
        st.info(_backtest_empty_prompt(weeks))
        return

    focus_rows = _focus_weekend_review_rows(review_rows)
    _render_weekend_review_core_card(focus_rows, weeks=weeks)
    _render_weekend_review_kpis(review_rows)
    st.subheader(_weekend_review_detail_title(weeks))
    _render_weekend_review_table(review_rows)
    with st.expander("数据质量 / 排除原因", expanded=False):
        ok_review_rows = _ok_weekend_review_rows(review_rows)
        if not ok_review_rows:
            st.info(_weekend_review_empty_reason(review_rows))
        st.dataframe(_weekend_review_diagnostic_frame(_display_weekend_review_rows(review_rows)), width="stretch", hide_index=True)
        st.dataframe(_backtest_diagnostic_frame(results), width="stretch", hide_index=True)


def _focus_weekend_review_rows(review_rows: list[dict]) -> list[dict]:
    display_rows = _display_weekend_review_rows(review_rows)
    week_options: list[str] = []
    for row in display_rows:
        week_id = str(row.get("week_id") or "").strip()
        if week_id and week_id not in week_options:
            week_options.append(week_id)
    if len(week_options) <= 1:
        return review_rows
    selected_week = st.selectbox(
        "查看周次",
        week_options,
        index=0,
        key="weekend_spread_backtest_focus_week",
    )
    return sorted(
        review_rows,
        key=lambda row: (str(row.get("week_id") or "").strip() != selected_week, -_week_id_sort_key(str(row.get("week_id") or ""))[1]),
    )


def _current_backtest_results(
    session_rows: object,
    cached_result: dict,
    *,
    preflight: dict[str, object],
    mapping: dict[str, dict],
    include_unconfirmed: bool,
) -> list[dict]:
    if not preflight.get("can_run"):
        return []
    source_rows = list(session_rows or cached_result.get("rows") or [])
    allowed_tickers = {str(ticker or "").strip().upper() for ticker in (preflight.get("eligible_tickers") or [])}
    if not allowed_tickers:
        return []
    filtered: list[dict] = []
    for source in source_rows:
        row = dict(source or {})
        ticker = str(row.get("ticker") or "").strip().upper()
        if ticker not in allowed_tickers:
            continue
        config = mapping.get(ticker) or {}
        configured_symbol = str(config.get("binance_symbol") or "").strip().upper()
        row_symbol = str(row.get("binance_symbol") or row.get("symbol") or "").strip().upper()
        if configured_symbol and row_symbol and configured_symbol != row_symbol:
            continue
        confidence = str(config.get("mapping_confidence") or row.get("mapping_confidence") or "").strip().lower()
        if not include_unconfirmed and confidence != "confirmed" and not _is_auto_mapping_config(config):
            continue
        filtered.append(row)
    return filtered


def _is_auto_mapping_config(config: dict | None) -> bool:
    if not isinstance(config, dict):
        return False
    confidence = str(config.get("mapping_confidence") or "").strip().lower()
    status = str(config.get("mapping_status") or "").strip()
    if status in {MAPPING_UNAVAILABLE, MAPPING_INVALID, MAPPING_IGNORED, MAPPING_IGNORED_LABEL, "不可用", "已忽略"}:
        return False
    return bool(str(config.get("binance_symbol") or "").strip()) or (
        confidence == "auto_available"
        and status
        in {
            "",
            MAPPING_AVAILABLE,
            MAPPING_PRICE_ANOMALY,
            MAPPING_US_EQUITY_VERIFIED,
            MAPPING_ETF_VERIFIED,
            MAPPING_REVIEW,
            "自动可用",
        }
    )


def _render_overnight_provider_self_check(result: dict[str, object]) -> None:
    reason = _clean_self_check_text(result.get("reason"), "未返回原因")
    if result.get("ok"):
        st.success("夜盘数据源可用，已读取开盘窗口内首个有效 1m bar。")
    else:
        st.error(f"夜盘数据源自检失败：{reason}")
    rows = [
        ("当前 provider", _clean_self_check_text(result.get("provider_display"), "未配置")),
        ("Alpaca 配置", "已配置" if result.get("alpaca_configured") else "缺少 API key"),
        ("Alpaca feed", _clean_self_check_text(result.get("feed"), "未配置")),
        ("timeframe", _clean_self_check_text(result.get("timeframe"), "未配置")),
        ("IBKR 配置", _ibkr_self_check_status(result)),
        ("请求开始", _weekend_review_short_time(result.get("requested_start")) or "暂无"),
        ("请求结束", _weekend_review_short_time(result.get("requested_end")) or "暂无"),
        ("返回 bar 数量", str(int(result.get("raw_returned_bar_count") or result.get("returned_bar_count") or 0))),
        ("第一根 raw bar 时间", _weekend_review_short_time(result.get("first_raw_bar_time_et") or result.get("first_raw_bar_time") or result.get("raw_first_bar_time")) or "暂无"),
        (
            "第一根 raw bar close",
            _money_text(result.get("first_raw_bar_close") or result.get("raw_first_bar_close"))
            if _number(result.get("first_raw_bar_close") or result.get("raw_first_bar_close")) is not None
            else "暂无",
        ),
        ("选中 P2 时间", _weekend_review_short_time(result.get("selected_bar_time")) or "暂无"),
        (
            "选中 P2 close",
            _money_text(result.get("selected_bar_close"))
            if _number(result.get("selected_bar_close")) is not None
            else "暂无",
        ),
        ("是否命中首分钟", "是" if result.get("first_minute_hit") else "否"),
        ("是否命中开盘窗口", "是" if result.get("opening_window_hit") or result.get("hit_opening_window") else "否"),
        ("延迟分钟", "暂无" if _number(result.get("p2_delay_minutes")) is None else str(int(_number(result.get("p2_delay_minutes")) or 0))),
        ("样本质量", _p2_sample_quality_text(result.get("p2_sample_quality"))),
        ("自检结论", _clean_self_check_text(result.get("strict_p2_conclusion"), reason)),
        ("provider 返回", _clean_self_check_text(result.get("provider"), "未配置")),
        ("数据质量", _data_quality_text(result.get("quality"))),
        ("疑似 15 分钟延迟", "是" if result.get("boats_delay_suspected") else "否"),
        ("失败原因", "" if result.get("ok") else reason),
    ]
    st.dataframe(pd.DataFrame(rows, columns=["项目", "结果"]), width="stretch", hide_index=True)


def _ibkr_self_check_status(result: dict[str, object]) -> str:
    if result.get("ibkr_configured") and result.get("ibkr_path_exists"):
        return "已配置"
    if result.get("ibkr_configured"):
        return "已配置，但路径不可用"
    return "未配置"


def _clean_self_check_text(value: object, fallback: str) -> str:
    text = str(value or "").strip()
    if not text or text.lower() in {"none", "anchor_source"}:
        return fallback
    if any(marker in text for marker in ("鏈", "缂", "鎶", "閰", "锛", "€")):
        return fallback
    return text


def _render_tradingview_backfill_tools() -> None:
    with st.expander("数据源与补数工具", expanded=False):
        status = webhook_status_summary()
        st.caption("TradingView Webhook、CSV 和手动补数只写入周末价差缓存，不写入主交易系统。")
        cols = st.columns(4)
        cols[0].metric("Webhook secret", "已配置" if status.get("secret_configured") else "未配置")
        cols[1].metric("最近 symbol", status.get("latest_symbol") or "尚未收到")
        latest_p0 = dict(status.get("latest_p0") or {})
        latest_p2 = dict(status.get("latest_p2") or {})
        cols[2].metric("?? P0", _tradingview_event_metric(latest_p0))
        cols[3].metric("?? P2", _tradingview_event_metric(latest_p2))
        if not status.get("latest_write_ok"):
            st.info("尚未收到 TradingView 推送")

        st.markdown("**TradingView alert message 示例**")
        example_p0 = {
            "secret": "你的secret",
            "symbol": "{{ticker}}",
            "event_type": EVENT_FRIDAY_AFTERHOURS_CLOSE,
            "timestamp_et": "{{time}}",
            "close": "{{close}}",
            "source": "TradingView",
        }
        example_p2 = {
            "secret": "你的secret",
            "symbol": "{{ticker}}",
            "event_type": EVENT_OVERNIGHT_FIRST_1M_CLOSE,
            "timestamp_et": "{{time}}",
            "close": "{{close}}",
            "source": "TradingView",
        }
        st.code(json.dumps(example_p0, ensure_ascii=False, indent=2), language="json")
        st.code(json.dumps(example_p2, ensure_ascii=False, indent=2), language="json")

        st.markdown("**TradingView CSV 补数**")
        csv_dir = st.text_input(
            "CSV 目录",
            value=str(DEFAULT_TRADINGVIEW_CSV_DIR),
            key="weekend_spread_tradingview_csv_dir",
        )
        csv_cols = st.columns([1, 1, 3])
        if csv_cols[0].button("扫描 CSV", key="weekend_spread_tv_scan_csv"):
            st.session_state["weekend_spread_tv_csv_scan"] = scan_tradingview_csv_dir(csv_dir)
        if csv_cols[1].button("导入全部", key="weekend_spread_tv_import_csv"):
            st.session_state["weekend_spread_tv_csv_import"] = import_tradingview_csv_dir(csv_dir)
            st.success("CSV 已导入周末价差缓存")
        scan_rows = st.session_state.get("weekend_spread_tv_csv_import") or st.session_state.get("weekend_spread_tv_csv_scan")
        if scan_rows:
            st.dataframe(pd.DataFrame(scan_rows), width="stretch", hide_index=True)

        st.markdown("**手动补夜盘价格**")
        manual_cols = st.columns([1, 1, 1, 1])
        manual_symbol = manual_cols[0].text_input("股票", value="NVDA", key="weekend_spread_manual_p2_symbol")
        manual_time = manual_cols[1].text_input("时间 ET", value=_latest_overnight_session_text(), key="weekend_spread_manual_p2_time")
        manual_price = manual_cols[2].number_input("夜盘首分钟收盘价", min_value=0.0, value=0.0, step=0.01, key="weekend_spread_manual_p2_price")
        manual_source = manual_cols[3].selectbox("来源", ["IBKR", "Alpaca", "富途", "老虎", "其他"], key="weekend_spread_manual_p2_source")
        manual_note = st.text_input("备注，可选", key="weekend_spread_manual_p2_note")
        if st.button("保存手动补数", key="weekend_spread_save_manual_p2"):
            if not manual_symbol.strip() or manual_price <= 0:
                st.error("请填写股票，并确保价格大于 0。")
            else:
                upsert_manual_overnight_price(
                    symbol=manual_symbol,
                    timestamp_et=manual_time,
                    close=manual_price,
                    source=manual_source,
                    note=manual_note,
                )
                st.success("已写入周末价差手动补数缓存")


def _tradingview_event_metric(row: dict[str, object]) -> str:
    if not row:
        return "暂无"
    close = _money_text(row.get("close")) if _number(row.get("close")) is not None else "暂无"
    time_text = _weekend_review_short_time(row.get("timestamp_et")) or "暂无"
    return f"{close} / {time_text}"


def _latest_overnight_session_text() -> str:
    window = recent_weekend_windows(weeks=1)[0]
    return window.end_et.strftime("%Y-%m-%d %H:%M:%S")


def _safe_backtest_weeks(value: object, default: int = 4) -> int:
    try:
        weeks = int(value or default)
    except (TypeError, ValueError):
        weeks = default
    return max(1, min(12, weeks))


def _backtest_mode_text(value: object) -> str:
    text = str(value or "").strip().lower().replace(" ", "_")
    return {
        "auto_usable": "已验证映射",
        "verified mapping": "已验证映射",
        "verified_mapping": "已验证映射",
        "confirmed": "\u4eba\u5de5\u9501\u5b9a",
        "manual_locked": "\u4eba\u5de5\u9501\u5b9a",
    }.get(text, str(value or ""))


def _backtest_run_button_label(weeks: object) -> str:
    return f"运行近 {_safe_backtest_weeks(weeks)} 周回测"


def _backtest_empty_prompt(weeks: object) -> str:
    return f"尚未运行历史回测。展开“回测设置”后点击“{_backtest_run_button_label(weeks)}”。"


def _weekend_review_detail_title(weeks: object) -> str:
    return f"近 {_safe_backtest_weeks(weeks)} 周传导明细"


def _backtest_result_weeks(cached_result: dict[str, object] | None, selected_weeks: object) -> int:
    params = (cached_result or {}).get("params")
    if isinstance(params, dict) and params.get("weeks") is not None:
        return _safe_backtest_weeks(params.get("weeks"), default=_safe_backtest_weeks(selected_weeks))
    return _safe_backtest_weeks(selected_weeks)


def _render_backtest_preflight(preflight: dict[str, object]) -> None:
    cols = st.columns(4)
    cols[0].metric("可回测标的", int(preflight.get("eligible_count") or 0))
    cols[1].metric("已排除标的", int(preflight.get("excluded_count") or 0))
    cols[2].metric("当前模式", _backtest_mode_text(preflight.get("mode")))
    cols[3].metric("数据源状态", "USDT-M 合约")


def _render_backfill_audit_area_v2(watchlist: list[str], mapping: dict[str, dict], anchors: dict[str, dict]) -> None:
    with st.expander("高级 / 前瞻记录", expanded=False):
        st.caption("前瞻记录只写入周末价差缓存，不会生成交易信号或修改主系统。")


def _render_backfill_audit_area(watchlist: list[str], mapping: dict[str, dict], anchors: dict[str, dict]) -> None:
    with st.expander("高级 / 前瞻记录", expanded=False):
        st.caption("前瞻记录仅用于周末价差观察，不构成交易建议。")


def _mapping_status_text(value: object) -> str:
    text = str(value or "").strip().lower()
    return {
        "confirmed": "人工锁定",
        "candidate": "映射可用",
        "unverified": "映射可用",
        "no_mapping": "无映射",
        "missing": "无映射",
        "invalid": "不可用",
        "auto_available": "映射可用",
        "us_equity_verified": "映射可用",
        "etf_verified": "映射可用",
        "pending_verification": "映射可用",
        "other_tradfi": "其他 TradFi",
    }.get(text, str(value or "未知"))


def _data_quality_text(value: object) -> str:
    text = str(value or "").strip().upper()
    return {
        "OK": "严格正式样本",
        "MAPPING_MISSING": "映射缺失",
        "STOCK_MISSING": "美股价格缺失",
        "CONTRACT_MISSING": "合约价格缺失",
        "STALE_CACHE": "缓存过期",
        "INVALID_PRICE": "价格无效",
        "DATA_UNAVAILABLE": "数据不可用",
        "UNCONFIRMED_MAPPING": "旧观察样本",
        "OBSERVE_ONLY": "仅观察",
        "OBSERVE_ANCHOR_ONLY": "仅观察锚点",
        "NO_AFTERHOURS_CLOSE": "缺少最后交易日盘后价格",
        "REGULAR_CLOSE_FALLBACK": "常规收盘回退",
        "FALLBACK_REGULAR_CLOSE": "常规收盘回退",
        "P0_UNVERIFIED": "P0 待验证",
        "DELAYED_OVERNIGHT_FIRST_VALID": "延迟成交样本",
        "BINANCE_CONTRACT_NOT_LISTED_YET": "Binance 合约当周未上线",
        "NO_OPENING_WINDOW_BAR": "夜盘流动性不足",
        "NO_FIRST_MINUTE_BAR": "首分钟缺失",
        "MISSING_OVERNIGHT_FIRST_1M": "首分钟缺失",
        "OVERNIGHT_PROVIDER_MISSING": "美股夜盘数据源未配置",
        "TRADINGVIEW_WEBHOOK_SAMPLE": "TradingView Webhook 样本",
        "TRADINGVIEW_CSV_SAMPLE": "TradingView CSV 样本",
        "MANUAL_BROKER_SAMPLE": "人工券商样本",
        "MANUAL_AFTERHOURS_SAMPLE": "人工盘后样本",
        "ALPACA_BOATS_SAMPLE": "Alpaca BOATS 样本",
        "BOATS_DELAY_PENDING": "BOATS 延迟等待",
        "ALPACA_BOATS_PERMISSION": "Alpaca BOATS 权限不足",
        "MISSING_BOATS_FIRST_1M": "首分钟缺失",
        "PROVIDER_ERROR": "数据源错误",
        "DEGRADED": "降级样本",
        "DEGRADED_5M": "5m 降级样本",
        "BINANCE_KLINE_UNAVAILABLE": "Binance K线不可用",
        "NO_BROKER_OVERNIGHT_BAR": "首分钟缺失",
        "MISSING_STOCK_FIRST_BAR": "首分钟缺失",
        "HOLIDAY_OR_NO_SESSION": "假期或无夜盘 session",
        "STALE_OR_MISALIGNED": "缓存日期不匹配",
        "HOLIDAY_SHIFTED_OVERNIGHT_SESSION": "夜盘顺延",
        "MISSING_FRIDAY_AFTERHOURS_CLOSE": "缺少最后交易日盘后价格",
        "MISSING_BINANCE_WEEKEND_MAX": "缺少 Binance 周末高点",
        "MISSING_P0": "缺少 P0",
        "MISSING_P1": "缺少 P1",
        "MISSING_P2": "缺少 P2",
    }.get(text, text or "未知")


def _backfill_mapping_status_text(value: object) -> str:
    text = str(value or "").strip().upper()
    return {
        "CONFIRMED_TRADE_GRADE": "人工锁定 / 可回测",
        "CANDIDATE_OBSERVATION": "映射可用 / 观察",
    }.get(text, str(value or "未知"))


def _basis_status_text(value: object) -> str:
    text = str(value or "").strip().upper()
    overrides = {
        "ALLOW_SHORT": "允许观察",
        "BLOCK_MAPPING": "映射不可用",
        "BLOCK_LIQUIDITY": "流动性不足",
        "BLOCK_DATA": "数据不足",
    }
    if text in overrides:
        return overrides[text]
    return {
        "OBSERVE": "观察",
        "ENTRY_CANDIDATE": "候选",
        "SHORT_OPEN": "已记录",
        "WAIT_BROKER_OPEN": "等待夜盘",
        "HEDGE_DUE": "待复核",
        "HEDGE_LOCKED": "已锁定",
        "EXIT_READY": "待结束",
        "CLOSED": "已关闭",
        "FAILED": "失败",
    }.get(text, str(value or "未知"))


def _exclusion_reason_text(value: object) -> str:
    text = str(value or "").strip().upper()
    return {
        "NO_MAPPING": "无映射",
        "AUTO_CANDIDATE_NOT_ALLOWED": "自动候选未纳入",
        "UNCONFIRMED_EXCLUDED": "不可用映射已排除",
        "MAPPING_NOT_VERIFIED": "映射不可用或已忽略",
        "OTHER_TRADFI_EXCLUDED": "映射不可用或已忽略",
        "NO_AFTERHOURS_ANCHOR": "缺少盘后锚点",
        "SYMBOL_INVALID": "合约无效",
        "BINANCE_KLINE_UNAVAILABLE": "Binance K线不可用",
        "FUTURES_UNAVAILABLE": "USDT-M 合约不可用",
        "NO_PRICE_ANCHOR": "缺少价格锚点",
        "PROVIDER_ERROR": "数据源错误",
    }.get(text, str(value or "未知"))


def _backtest_block_text(value: object) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return "当前没有可进入回测的标的"
    return {
        "NO_MAPPING": "当前没有可用于回测的 Binance 美股映射，请先同步或配置本模块映射。",
        "MAPPING_NOT_VERIFIED": "该映射不可用或已忽略，不能进入正式回测。",
        "NO_AFTERHOURS_ANCHOR": "缺少本周最后交易日盘后锚点，请先更新盘后锚点或补数。",
        "OTHER_TRADFI_EXCLUDED": "该映射不可用或已忽略，不能进入正式回测。",
        "BINANCE_KLINE_UNAVAILABLE": "缺少 Binance 周末 1m K 线，暂时不能运行回测。",
        "FUTURES_UNAVAILABLE": "USDT-M 合约不可用，暂时不能运行回测。",
        "NO_PRICE_ANCHOR": "缺少价格锚点，暂时不能运行回测。",
        "PROVIDER_ERROR": "数据源返回错误，请稍后重试或查看排除原因。",
    }.get(text, _exclusion_reason_text(text))


def _market_type_text(value: object) -> str:
    text = str(value or "").strip().lower()
    return {
        "usdm_futures": "USDT-M 合约",
        "usd_m_futures": "USDT-M 合约",
        "um_futures": "USDT-M 合约",
        "futures": "USDT-M 合约",
        "spot": "现货",
    }.get(text, str(value or "未知"))


def _backtest_exclusion_frame(rows: list[dict]) -> pd.DataFrame:
    columns = [
        ("ticker", "股票"),
        ("symbol", "Binance 合约"),
        ("market_type", "市场类型"),
        ("mapping_status", "映射状态"),
        ("exclusion_reason", "排除原因"),
    ]
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=[label for _, label in columns])
    display = pd.DataFrame()
    for key, label in columns:
        display[label] = frame.get(key)
    display["市场类型"] = display["市场类型"].map(_market_type_text)
    display["映射状态"] = display["映射状态"].map(_mapping_status_text)
    display["排除原因"] = display["排除原因"].map(_exclusion_reason_text)
    return display


def _backtest_error_message(rows: list[dict]) -> str:
    if not rows:
        return ""
    grouped: dict[tuple[str, str], int] = {}
    for row in rows:
        ticker = str(row.get("ticker") or "").strip().upper() or "UNKNOWN"
        reason = _backtest_row_failure_reason(row)
        grouped[(ticker, reason)] = grouped.get((ticker, reason), 0) + 1
    return "\uff1b".join(
        f"{ticker}\uff1a{reason}\uff0c\u5df2\u6392\u9664 {count} \u4e2a\u6837\u672c"
        for (ticker, reason), count in sorted(grouped.items())
    )


def _backtest_row_failure_reason(row: dict) -> str:
    data_quality = str(row.get("data_quality") or "").strip().upper()
    transmission_quality = str(row.get("transmission_data_quality") or "").strip().upper()
    raw_error = str(row.get("error_message") or "").strip().upper()
    if data_quality == "NO_AFTERHOURS_CLOSE" or raw_error in {"MISSING_FRIDAY_AFTERHOURS_CLOSE", "NO_AFTERHOURS_CLOSE"}:
        return "\u7f3a\u5c11\u672c\u5468\u6700\u540e\u4ea4\u6613\u65e5\u76d8\u540e\u6536\u76d8\u4ef7"
    if transmission_quality == "OVERNIGHT_PROVIDER_MISSING" or data_quality == "OVERNIGHT_PROVIDER_MISSING":
        return "\u7f8e\u80a1\u591c\u76d8\u6570\u636e\u6e90\u672a\u914d\u7f6e"
    if transmission_quality in {"NO_BROKER_OVERNIGHT_BAR", "MISSING_STOCK_FIRST_BAR", "MISSING_OVERNIGHT_FIRST_1M"} or raw_error in {
        "NO_BROKER_OVERNIGHT_BAR",
        "MISSING_STOCK_FIRST_BAR",
        "MISSING_OVERNIGHT_FIRST_1M",
    }:
        return OPENING_WINDOW_P2_MISSING_TEXT
    if transmission_quality == "HOLIDAY_OR_NO_SESSION" or data_quality == "HOLIDAY_OR_NO_SESSION":
        return "\u975e\u6b63\u5e38\u4ea4\u6613\u65e5 / \u65e0\u591c\u76d8 session"
    if transmission_quality == "BINANCE_CONTRACT_NOT_LISTED_YET" or data_quality == "BINANCE_CONTRACT_NOT_LISTED_YET":
        return "Binance 合约当周尚未上线"
    if transmission_quality in {"CONTRACT_MISSING", "DATA_UNAVAILABLE", "BINANCE_KLINE_UNAVAILABLE", "MISSING_BINANCE_WEEKEND_MAX"} or data_quality in {
        "CONTRACT_MISSING",
        "DATA_UNAVAILABLE",
        "BINANCE_KLINE_UNAVAILABLE",
        "MISSING_BINANCE_WEEKEND_MAX",
    }:
        return "\u7f3a\u5c11 Binance \u5468\u672b 1m K\u7ebf"
    if transmission_quality == "STALE_OR_MISALIGNED" or data_quality == "STALE_OR_MISALIGNED":
        return "Binance \u6570\u636e\u8fc7\u671f\u6216\u65f6\u95f4\u4e0d\u5bf9"
    if transmission_quality == "INVALID" or data_quality == "INVALID":
        return _data_quality_text(raw_error) if raw_error else "\u6570\u636e\u65e0\u6548"
    quality = transmission_quality or data_quality
    return _weekend_review_failure_reason(row, quality)


def _render_backtest_kpis(rows: list[dict]) -> None:
    summary = summarize_backtest_results(rows)
    cols = st.columns(3)
    cols[0].metric("正式样本", int(summary.get("ok_count") or 0))
    cols[1].metric("观察样本", int(summary.get("observe_count") or 0))
    cols[2].metric("排除样本", int(summary.get("excluded_count") or 0))


def _render_backfill_kpis_v2(rows: list[dict]) -> None:
    cols = st.columns(3)
    cols[0].metric("样本总数", len(rows))
    cols[1].metric("正式样本", sum(1 for row in rows if str(row.get("data_quality") or "").upper() == "OK"))
    cols[2].metric("待复核样本", sum(1 for row in rows if str(row.get("data_quality") or "").upper() != "OK"))


def _render_backfill_kpis(rows: list[dict]) -> None:
    cols = st.columns(3)
    cols[0].metric("样本总数", len(rows))
    cols[1].metric("正式样本", sum(1 for row in rows if str(row.get("data_quality") or "").upper() == "OK"))
    cols[2].metric("待复核样本", sum(1 for row in rows if str(row.get("data_quality") or "").upper() != "OK"))



def _render_weekend_review_kpis(review_rows: list[dict]) -> None:
    summary = _weekend_review_summary(review_rows)
    quality_counts = _weekend_review_quality_counts(review_rows)
    liquidity_label, liquidity_detail = _p2_first_minute_liquidity_label(review_rows)
    window_liquidity_fn = globals().get("_p2_opening_window_liquidity_label")
    if callable(window_liquidity_fn):
        window_label, window_detail = window_liquidity_fn(review_rows)
    else:
        window_label, window_detail = liquidity_label, liquidity_detail
    p2_stats = _weekend_review_p2_stats(review_rows)
    strict_count = int(p2_stats.get("first_minute_count") or 0)
    observation_count = int(quality_counts["observe"] + quality_counts["delayed"] + quality_counts["missing_p2"])
    metrics: list[tuple[str, object, str]] = [
        ("样本数", int(p2_stats.get("eligible_count") or 0), "number"),
        ("严格样本", strict_count, "number"),
        ("观察样本", observation_count, "number"),
        ("平均周末冲高", summary.get("avg_binance_premium_pct"), "percent"),
        ("平均最终传导", summary.get("avg_overnight_vs_afterhours_pct"), "percent"),
        ("首分钟命中率", _hit_rate_value(strict_count, int(p2_stats.get("eligible_count") or 0)), "percent"),
    ]
    cols = st.columns(len(metrics))
    if not int(summary.get("sample_count") or 0):
        for col, (label, _, _) in zip(cols, metrics):
            col.metric(label, "0" if label in {"样本数", "严格样本", "观察样本"} else "无法计算")
        st.caption(
            f"首分钟样本 {quality_counts['ok']} 条｜延迟成交样本 {quality_counts['delayed']} 条｜首分钟/窗口缺失样本 {quality_counts['missing_p2']} 条｜"
            f"仅观察样本 {quality_counts['observe']} 条｜排除样本 {quality_counts['excluded']} 条｜"
            f"首分钟流动性：{liquidity_label}（{liquidity_detail}）｜开盘窗口成交率：{window_label}（{window_detail}）"
        )
        st.info(_weekend_review_empty_reason(review_rows))
        return
    for col, (label, value, kind) in zip(cols, metrics):
        if kind == "percent":
            col.metric(label, _review_percent_metric_text(value))
        elif kind == "delay":
            col.metric(label, "无法计算" if value is None else f"{float(value):.1f}")
        else:
            col.metric(label, value)
    st.caption(
        f"首分钟样本 {quality_counts['ok']} 条｜延迟成交样本 {quality_counts['delayed']} 条｜首分钟/窗口缺失样本 {quality_counts['missing_p2']} 条｜"
        f"仅观察样本 {quality_counts['observe']} 条｜排除样本 {quality_counts['excluded']} 条｜"
        f"首分钟流动性：{liquidity_label}（{liquidity_detail}）｜开盘窗口成交率：{window_label}（{window_detail}）｜"
        f"首分钟样本兑现率：{_review_percent_metric_text(p2_stats.get('first_minute_capture_pct'))}｜"
        f"全部可成交样本兑现率：{_review_percent_metric_text(summary.get('avg_capture_pct'))}"
    )
    if summary.get("summary_quality") == "OBSERVE" or strict_count == 0:
        st.caption("当前样本未全部满足严格正式口径，仅作为观察统计。")


def _hit_rate_value(hit_count: int, total_count: int) -> float | None:
    if total_count <= 0:
        return None
    return hit_count / total_count * 100.0


def _review_percent_metric_text(value: object) -> str:
    return "无法计算" if _number(value) is None else _review_percent_text(value)


def _latest_weekend_review_row(review_rows: list[dict]) -> dict | None:
    display_rows = _display_weekend_review_rows(review_rows)
    return display_rows[0] if display_rows else None


def _p2_first_minute_liquidity_label(review_rows: list[dict]) -> tuple[str, str]:
    eligible = [
        row
        for row in review_rows
        if _number(row.get("friday_afterhours_close")) is not None and _number(row.get("binance_price")) is not None
    ]
    if len(eligible) < 2:
        return "数据不足", f"{len(eligible)} 周可比"
    hit_count = sum(1 for row in eligible if _number(row.get("p2_delay_minutes")) == 0)
    hit_rate = hit_count / len(eligible)
    if hit_rate >= 0.8:
        label = "良好"
    elif hit_rate >= 0.4:
        label = "一般"
    else:
        label = "较差"
    return label, f"{hit_count}/{len(eligible)} 周命中"


def _p2_opening_window_liquidity_label(review_rows: list[dict]) -> tuple[str, str]:
    eligible = [
        row
        for row in review_rows
        if _number(row.get("friday_afterhours_close")) is not None and _number(row.get("binance_price")) is not None
    ]
    if len(eligible) < 2:
        return "数据不足", f"{len(eligible)} 周可比"
    hit_count = sum(1 for row in eligible if _number(row.get("broker_open_close")) is not None)
    hit_rate = hit_count / len(eligible)
    if hit_rate >= 0.8:
        label = "良好"
    elif hit_rate >= 0.4:
        label = "一般"
    else:
        label = "较差"
    return label, f"{hit_count}/{len(eligible)} 周命中"


def _weekend_review_p2_stats(review_rows: list[dict]) -> dict[str, object]:
    eligible = [
        row
        for row in review_rows
        if _number(row.get("friday_afterhours_close")) is not None and _number(row.get("binance_price")) is not None
    ]
    first_minute_rows = [row for row in eligible if _number(row.get("p2_delay_minutes")) == 0]
    delayed_rows = [
        row
        for row in eligible
        if _number(row.get("p2_delay_minutes")) is not None and float(_number(row.get("p2_delay_minutes")) or 0) > 0
    ]
    no_window_rows = [row for row in eligible if _number(row.get("broker_open_close")) is None]
    delays = [float(_number(row.get("p2_delay_minutes")) or 0.0) for row in first_minute_rows + delayed_rows]
    first_captures = [_number(row.get("capture_pct")) for row in first_minute_rows]
    first_captures = [float(value) for value in first_captures if value is not None]
    return {
        "eligible_count": len(eligible),
        "first_minute_count": len(first_minute_rows),
        "delayed_count": len(delayed_rows),
        "no_window_count": len(no_window_rows),
        "avg_delay_minutes": sum(delays) / len(delays) if delays else None,
        "first_minute_capture_pct": sum(first_captures) / len(first_captures) if first_captures else None,
    }


def _money_or_missing(value: object, fallback: str) -> str:
    return _money_text(value) if _number(value) is not None else fallback


def _percent_or_missing(value: object, row: dict) -> str:
    if _number(value) is not None:
        return _review_percent_text(value)
    missing: list[str] = []
    if _number(row.get("friday_afterhours_close")) is None:
        missing.append("P0")
    if _number(row.get("broker_open_close")) is None:
        missing.append("无 P2")
    if missing:
        if missing == ["无 P2"]:
            return "无 P2"
        return "缺 " + " / ".join(missing)
    return "无法计算"


def _row_is_missing_afterhours_close(row: dict) -> bool:
    data_quality = str(row.get("data_quality") or "").strip().upper()
    p0_quality = str(row.get("p0_quality") or row.get("friday_afterhours_quality") or "").strip().upper()
    raw_error = str(row.get("error_message") or row.get("friday_afterhours_reason") or row.get("p0_failure_reason") or "").strip().upper()
    return (
        data_quality in {"NO_AFTERHOURS_CLOSE", "MISSING_FRIDAY_AFTERHOURS_CLOSE", "MISSING_P0"}
        or p0_quality in {"MISSING_AFTERHOURS_CLOSE", "NO_AFTERHOURS_CLOSE", "MISSING_P0"}
        or raw_error in {"NO_AFTERHOURS_CLOSE", "MISSING_FRIDAY_AFTERHOURS_CLOSE"}
        or "\u7f3a\u5c11\u672c\u5468\u6700\u540e\u4ea4\u6613\u65e5\u76d8\u540e\u6536\u76d8\u4ef7" in raw_error
    )


def _actual_afterhours_close(row: dict) -> float | None:
    if _row_is_missing_afterhours_close(row):
        return None
    return _first_number(
        row,
        (
            "last_trading_day_afterhours_close",
            "friday_afterhours_close",
            "p0_selected_bar_close",
            "afterhours_reference_price",
        ),
    )


def _p0_source_summary(row: dict) -> str:
    quality = str(row.get("p0_quality") or row.get("data_quality") or "").upper()
    source = _price_source_text(row.get("friday_afterhours_provider") or row.get("p0_provider"))
    if "FMP" in quality or "FMP" in source.upper():
        if quality in {"FMP_AFTERHOURS_1M_BAR", "FMP_AFTERHOURS_TRADE"}:
            return "FMP 盘后 1m"
        return "FMP 盘后待验证"
    if quality in {"REGULAR_CLOSE_FALLBACK", "FALLBACK_REGULAR_CLOSE"} or row.get("p0_is_fallback"):
        return "常规收盘回退"
    return source or "未知"


def _p1_source_summary(row: dict) -> str:
    provider = str(row.get("binance_provider") or "").strip().upper()
    if not provider or provider == "BINANCE_USDT_M":
        return "Binance USDT-M max(high)"
    return _price_source_text(provider)


def _p2_source_summary(row: dict) -> str:
    if _number(row.get("broker_open_close")) is None:
        reason = str(row.get("failure_reason") or "").strip()
        if reason and reason.upper() not in {"NONE", "ANCHOR_SOURCE"}:
            if "首分钟" in reason:
                return OPENING_WINDOW_P2_MISSING_TEXT
            return reason
        quality = str(row.get("data_quality") or (row.get("raw_row") or {}).get("transmission_data_quality") or "").strip().upper()
        if quality == "OVERNIGHT_PROVIDER_MISSING":
            return "美股夜盘数据源未配置"
        return OPENING_WINDOW_P2_MISSING_TEXT
    source = _price_source_text(row.get("overnight_provider"))
    if not source or source == "未知":
        return _data_quality_text(row.get("data_quality"))
    return source


def _p0_status_text(row: dict) -> str:
    quality = str(row.get("p0_quality") or row.get("data_quality") or "").strip().upper()
    if _number(row.get("friday_afterhours_close")) is None:
        return "缺盘后锚点"
    if quality in {"REGULAR_CLOSE_FALLBACK", "FALLBACK_REGULAR_CLOSE"} or row.get("p0_is_fallback"):
        return "回退"
    if quality in {"P0_UNVERIFIED", "FMP_AFTERHOURS_QUOTE_ANCHOR"}:
        return "待验证"
    return "已验证"


def _p2_status_text(row: dict) -> str:
    if _number(row.get("broker_open_close")) is None:
        return "无开盘窗口价格"
    delay = _number(row.get("p2_delay_minutes"))
    if delay is not None and int(delay) == 0:
        return "首分钟样本"
    if delay is not None and int(delay) > 0:
        return f"延迟成交样本（+{int(delay)} 分钟）"
    return "夜盘首个有效价"


def _p2_flow_text(row: dict) -> str:
    if _number(row.get("broker_open_close")) is not None:
        return _money_text(row.get("broker_open_close"))
    return "无开盘窗口成交"


def _p2_detail_text(row: dict) -> str:
    if _number(row.get("broker_open_close")) is None:
        return "无开盘窗口成交｜样本：夜盘流动性不足"
    time_text = row.get("p2_first_valid_time") or row.get("broker_first_time") or row.get("p2_selected_bar_time")
    delay = _number(row.get("p2_delay_minutes"))
    short_time = _weekend_review_short_time(time_text) or "时间待确认"
    if delay is None:
        return f"{short_time}｜样本：夜盘首个有效价"
    if int(delay) == 0:
        return f"{short_time}，首分钟样本"
    return f"{short_time}，开盘后 +{int(delay)} 分钟｜样本：延迟成交样本"


def _p2_delay_text(row: dict) -> str:
    delay = _number(row.get("p2_delay_minutes"))
    if delay is None:
        return "无 P2"
    delay_int = int(delay)
    return "0" if delay_int == 0 else f"+{delay_int}"


def _p2_sample_quality_text(value: object) -> str:
    text = str(value or "").strip().upper()
    return {
        "FIRST_MINUTE": "首分钟样本",
        "DELAYED_FIRST_VALID": "延迟成交样本",
        "NO_OPENING_WINDOW_BAR": "夜盘流动性不足",
        "NO_FIRST_MINUTE_BAR": "首分钟缺失",
    }.get(text, "数据不足" if not text else _data_quality_text(text))


def _render_weekend_review_core_card(review_rows: list[dict], *, weeks: int = 4) -> None:
    row = _latest_weekend_review_row(review_rows)
    if not row:
        st.info(f"尚未运行历史回测。展开“回测设置”后点击“{_backtest_run_button_label(weeks)}”。")
        return
    liquidity_label, liquidity_detail = _p2_first_minute_liquidity_label(review_rows)
    metrics = [
        ("周末冲高", _percent_or_missing(row.get("binance_premium_pct"), row)),
        ("高点回落", _percent_or_missing(row.get("overnight_vs_binance_pct"), row)),
        ("最终传导", _percent_or_missing(row.get("overnight_vs_afterhours_pct"), row)),
        ("高点兑现率", _percent_or_missing(row.get("capture_pct"), row)),
    ]
    metric_html = "".join(
        f"""
        <div class="weekend-core-metric">
          <div class="weekend-core-metric-label">{escape(label)}</div>
          <div class="weekend-core-metric-value">{escape(value)}</div>
        </div>
        """
        for label, value in metrics
    )
    st.markdown(
        f"""
        <div class="weekend-core-card">
          <div class="weekend-core-title">{escape(str(row.get("ticker") or ""))} · {escape(str(row.get("week_id") or ""))}</div>
          <div class="weekend-core-flow-label">最后交易日盘后 → Binance 周末高点 → 夜盘价格</div>
          <div class="weekend-core-flow">
            {escape(_money_or_missing(row.get("friday_afterhours_close"), "缺 P0"))}
            → {escape(_money_or_missing(row.get("binance_price"), "缺 P1"))}
            → {escape(_p2_flow_text(row))}
          </div>
          <div class="weekend-core-metrics">{metric_html}</div>
          <div class="weekend-core-sources">
            P0：{escape(_p0_source_summary(row))}｜P0 状态：{escape(_p0_status_text(row))}｜
            P1：{escape(_p1_source_summary(row))}｜
            P2：{escape(_p2_source_summary(row))}｜P2 状态：{escape(_p2_status_text(row))}｜
            首分钟流动性：{escape(liquidity_label)}（{escape(liquidity_detail)}）
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if _number(row.get("broker_open_close")) is None:
        st.caption("夜盘开盘窗口内无有效 1m K线，本策略不可用。")
        raw_time = str(row.get("p2_first_raw_bar_time_et") or row.get("p2_first_raw_bar_time") or "").strip()
        raw_close = _number(row.get("p2_first_raw_bar_close"))
        if raw_time and raw_close is not None:
            st.caption(f"Provider 返回 raw bar：{raw_time}，close {_money_text(raw_close)}；未命中当前开盘窗口。")
    elif _number(row.get("p2_delay_minutes")) and float(_number(row.get("p2_delay_minutes")) or 0) > 0:
        st.caption(f"非首分钟样本，开盘后 +{int(float(_number(row.get('p2_delay_minutes')) or 0))} 分钟才出现有效 1m K 线。")
    elif str(row.get("data_quality") or "").strip().upper() in {"REGULAR_CLOSE_FALLBACK", "FALLBACK_REGULAR_CLOSE"}:
        st.caption("P0 使用常规收盘回退，仅作为观察样本。")



def _weekend_review_quality_counts(review_rows: list[dict]) -> dict[str, int]:
    counts = {"ok": 0, "delayed": 0, "missing_p2": 0, "observe": 0, "degraded": 0, "excluded": 0}
    for row in review_rows:
        quality = str(row.get("data_quality") or "").strip().upper()
        if quality == "OK" and not bool(row.get("holiday_rollover")):
            counts["ok"] += 1
        elif quality == "OK" and bool(row.get("holiday_rollover")):
            counts["observe"] += 1
        elif quality == "DELAYED_OVERNIGHT_FIRST_VALID":
            counts["delayed"] += 1
        elif quality in {
            "MISSING_OVERNIGHT_FIRST_1M",
            "OVERNIGHT_PROVIDER_MISSING",
            "MISSING_BOATS_FIRST_1M",
            "BOATS_DELAY_PENDING",
            "ALPACA_BOATS_PERMISSION",
            "PROVIDER_ERROR",
        }:
            counts["missing_p2"] += 1
        elif quality in {
            "OBSERVE_ONLY",
            "REGULAR_CLOSE_FALLBACK",
            "FALLBACK_REGULAR_CLOSE",
            "P0_UNVERIFIED",
            "NO_AFTERHOURS_CLOSE",
            "TRADINGVIEW_WEBHOOK_SAMPLE",
            "TRADINGVIEW_CSV_SAMPLE",
            "MANUAL_BROKER_SAMPLE",
            "MANUAL_AFTERHOURS_SAMPLE",
            "ALPACA_BOATS_SAMPLE",
        }:
            counts["observe"] += 1
        elif quality.startswith("DEGRADED"):
            counts["degraded"] += 1
        else:
            counts["excluded"] += 1
    return counts


def _weekend_review_empty_reason(review_rows: list[dict]) -> str:
    if not review_rows:
        return "\u5c1a\u672a\u8fd0\u884c\u5386\u53f2\u56de\u6d4b\u3002\u5c55\u5f00\u201c\u56de\u6d4b\u8bbe\u7f6e\u201d\u540e\u70b9\u51fb\u8fd0\u884c\u56de\u6d4b\u3002"
    qualities = {str(row.get("data_quality") or "").strip().upper() for row in review_rows}
    qualities.update(
        str((row.get("raw_row") or {}).get("data_quality") or "").strip().upper()
        for row in review_rows
        if isinstance(row.get("raw_row"), dict)
    )
    qualities.discard("")
    if qualities & {"OVERNIGHT_PROVIDER_MISSING"}:
        return "\u7f3a\u5c11\u7f8e\u80a1\u591c\u76d8\u6570\u636e\u6e90\uff0c\u53ea\u80fd\u89c2\u5bdf Binance \u5468\u672b\u51b2\u9ad8"
    if qualities & {"MISSING_OVERNIGHT_FIRST_1M", "NO_BROKER_OVERNIGHT_BAR", "MISSING_STOCK_FIRST_BAR"}:
        return "夜盘开盘窗口内无有效 1m K线"
    if qualities & {"NO_AFTERHOURS_CLOSE", "MISSING_FRIDAY_AFTERHOURS_CLOSE", "MISSING_P0"}:
        return "\u7f3a\u5c11\u672c\u5468\u6700\u540e\u4ea4\u6613\u65e5\u76d8\u540e\u951a\u70b9"
    if qualities & {"BINANCE_CONTRACT_NOT_LISTED_YET"}:
        return "Binance 合约当周尚未上线，不能计算周末高点"
    if qualities & {"MISSING_BINANCE_WEEKEND_MAX", "CONTRACT_MISSING", "BINANCE_KLINE_UNAVAILABLE"}:
        return "缺少 Binance 周末 1m K线，不能计算周末高点"
    if qualities & {"REGULAR_CLOSE_FALLBACK", "FALLBACK_REGULAR_CLOSE"}:
        return "P0 \u4f7f\u7528\u5e38\u89c4\u6536\u76d8\u56de\u9000\uff0c\u53ea\u80fd\u89c2\u5bdf"
    if qualities & {"P0_UNVERIFIED"}:
        return "P0 \u76d8\u540e\u951a\u70b9\u5f85\u9a8c\u8bc1"
    return "\u5f53\u524d\u6ca1\u6709\u53ef\u8ba1\u5165\u6b63\u5f0f\u7edf\u8ba1\u7684\u6837\u672c"


def _weekend_review_rows(rows: list[dict]) -> list[dict]:
    selected: dict[tuple[str, str], dict] = {}
    for source in rows:
        row = dict(source)
        week_id = str(row.get("week_id") or "").strip()
        ticker = str(row.get("ticker") or "").strip().upper()
        if not week_id or not ticker:
            continue
        friday_afterhours_close = _actual_afterhours_close(row)
        binance_price = _first_number(row, ("binance_equivalent_max", "binance_weekend_max", "binance_weekend_max_price"))
        broker_open_close = _first_number(row, ("p2_first_valid_close", "overnight_first_1m_close", "broker_first_1m_close"))
        binance_premium_pct = _first_number(row, ("binance_premium_pct",))
        if binance_premium_pct is None and friday_afterhours_close and binance_price is not None:
            binance_premium_pct = (binance_price / friday_afterhours_close - 1.0) * 100.0
        overnight_vs_binance_pct = _first_number(row, ("overnight_vs_binance_pct",))
        if overnight_vs_binance_pct is None and binance_price and broker_open_close is not None:
            overnight_vs_binance_pct = (broker_open_close / binance_price - 1.0) * 100.0
        overnight_vs_afterhours_pct = _first_number(row, ("overnight_vs_afterhours_pct",))
        if overnight_vs_afterhours_pct is None and friday_afterhours_close and broker_open_close is not None:
            overnight_vs_afterhours_pct = (broker_open_close / friday_afterhours_close - 1.0) * 100.0
        capture_pct = _first_number(row, ("capture_pct",))
        if capture_pct is None and friday_afterhours_close is not None and binance_price is not None and broker_open_close is not None:
            denominator = binance_price - friday_afterhours_close
            if denominator:
                capture_pct = (broker_open_close - friday_afterhours_close) / denominator * 100.0
        data_quality = _weekend_review_data_quality(row, friday_afterhours_close, binance_price, binance_premium_pct, broker_open_close)
        p2_delay_minutes = _p2_delay_minutes_from_row(row, broker_open_close)
        p2_sample_quality = str(row.get("p2_sample_quality") or "").strip().upper()
        if not p2_sample_quality:
            if broker_open_close is None:
                p2_sample_quality = "NO_OPENING_WINDOW_BAR"
            elif p2_delay_minutes == 0:
                p2_sample_quality = "FIRST_MINUTE"
            else:
                p2_sample_quality = "DELAYED_FIRST_VALID"
        record = {
            "week_id": week_id,
            "ticker": ticker,
            "binance_symbol": str(row.get("binance_symbol") or row.get("symbol") or "").strip().upper(),
            "last_trading_day": str(row.get("last_trading_day") or "").strip(),
            "last_trading_day_is_friday": bool(row.get("last_trading_day_is_friday", True)),
            "last_trading_day_early_close": bool(row.get("last_trading_day_early_close") or False),
            "holiday_shifted_overnight_session": bool(row.get("holiday_shifted_overnight_session") or row.get("holiday_rollover") or False),
            "p2_session_start_et": _weekend_review_short_time(row.get("p2_session_start_et") or row.get("monday_reference_time_et")),
            "friday_afterhours_close": friday_afterhours_close,
            "friday_afterhours_time": _weekend_review_afterhours_time(row),
            "friday_afterhours_provider": str(row.get("friday_afterhours_provider") or row.get("friday_afterhours_source") or ""),
            "regular_close_price": _first_number(row, ("regular_close_price", "friday_close", "friday_close_price")),
            "p0_vs_regular_close_pct": _first_number(row, ("p0_vs_regular_close_pct",)),
            "p0_request_window": _weekend_review_time_range(row.get("p0_request_start_et"), row.get("p0_request_end_et")),
            "p0_provider": str(row.get("p0_provider") or row.get("friday_afterhours_provider") or ""),
            "p0_endpoint": str(row.get("p0_endpoint") or ""),
            "p0_returned_bar_count": int(_first_number(row, ("p0_returned_bar_count",)) or 0),
            "p0_selected_bar_time": _weekend_review_short_time(row.get("p0_selected_bar_time")) or _weekend_review_afterhours_time(row),
            "p0_selected_bar_close": _first_number(row, ("p0_selected_bar_close",)),
            "p0_selected_bar_volume": _first_number(row, ("p0_selected_bar_volume",)),
            "p0_quality": str(row.get("p0_quality") or row.get("friday_afterhours_quality") or ""),
            "p0_is_fallback": bool(row.get("p0_is_fallback") or False),
            "p0_failure_reason": str(row.get("p0_failure_reason") or row.get("friday_afterhours_reason") or ""),
            "holiday_rollover": bool(row.get("holiday_rollover") or False),
            "broker_open_close": broker_open_close,
            "broker_first_time": _weekend_review_broker_first_time(row),
            "p2_raw_returned_bar_count": int(_first_number(row, ("stock_bar_raw_returned_count", "stock_bar_returned_count", "overnight_returned_bar_count")) or 0),
            "p2_first_raw_bar_time": _weekend_review_short_time(row.get("stock_bar_first_raw_time") or row.get("stock_bar_raw_first_time")),
            "p2_first_raw_bar_time_et": _weekend_review_short_time(row.get("stock_bar_first_raw_time_et")),
            "p2_first_raw_bar_close": _first_number(row, ("stock_bar_first_raw_close", "stock_bar_raw_first_close")),
            "p2_selected_bar_time": _weekend_review_short_time(row.get("stock_bar_selected_time")),
            "p2_selected_bar_close": _first_number(row, ("stock_bar_selected_close",)),
            "p2_strict_first_minute_close": _first_number(row, ("p2_strict_first_minute_close",)),
            "p2_first_valid_close": _first_number(row, ("p2_first_valid_close", "overnight_first_1m_close", "broker_first_1m_close")),
            "p2_first_valid_time": _weekend_review_short_time(row.get("p2_first_valid_time") or row.get("broker_first_1m_time") or row.get("overnight_first_1m_time")),
            "p2_delay_minutes": p2_delay_minutes,
            "p2_sample_quality": p2_sample_quality,
            "p2_opening_window_minutes": _first_number(row, ("p2_opening_window_minutes",)),
            "p2_hit_first_minute": bool(row.get("stock_bar_hit_first_minute") or p2_sample_quality == "FIRST_MINUTE" or p2_delay_minutes == 0),
            "p2_hit_opening_window": bool(row.get("stock_bar_hit_opening_window") or broker_open_close is not None),
            "overnight_provider": _weekend_review_overnight_provider(row),
            "contract_sample_time": _weekend_review_contract_sample_time(row),
            "binance_price": binance_price,
            "binance_window": _weekend_review_binance_window(row),
            "binance_quote_count": _first_number(row, ("binance_kline_count", "binance_quote_count", "kline_count", "returned_kline_count")),
            "binance_provider": str(row.get("binance_provider") or "BINANCE_USDT_M"),
            "binance_premium_pct": binance_premium_pct,
            "overnight_vs_binance_pct": overnight_vs_binance_pct,
            "overnight_vs_afterhours_pct": overnight_vs_afterhours_pct,
            "capture_pct": capture_pct,
            "data_quality": data_quality,
            "sample_status": _weekend_review_sample_status(data_quality, row),
            "status": _weekend_review_status(data_quality, capture_pct),
            "failure_reason": _weekend_review_failure_reason(row, data_quality),
            "raw_row": row,
        }
        key = (week_id, ticker)
        current = selected.get(key)
        if current is None or _weekend_review_rank(record) > _weekend_review_rank(current):
            selected[key] = record
    return sorted(
        selected.values(),
        key=lambda item: (_week_id_sort_key(str(item.get("week_id") or "")), str(item.get("ticker") or "")),
        reverse=True,
    )


def _weekend_review_summary(review_rows: list[dict]) -> dict[str, object]:
    ok_rows = _tradable_weekend_review_rows(review_rows)
    source_rows = ok_rows
    summary_quality = "OK"
    if not source_rows:
        source_rows = [
            row
            for row in _display_weekend_review_rows(review_rows)
            if _number(row.get("friday_afterhours_close")) is not None and _number(row.get("binance_price")) is not None
        ]
        summary_quality = "OBSERVE" if source_rows else "NONE"
    latest_weeks = set(_latest_week_ids(source_rows, limit=4))
    scoped = [row for row in source_rows if row.get("week_id") in latest_weeks] if latest_weeks else list(source_rows)
    premium_rows = [
        row
        for row in scoped
        if _number(row.get("binance_premium_pct")) is not None
    ]
    if not premium_rows:
        return {
            "summary_quality": summary_quality,
            "sample_count": 0,
            "avg_binance_premium_pct": None,
            "avg_overnight_vs_binance_pct": None,
            "avg_capture_pct": None,
            "latest_week_capture_pct": None,
        }
    overnight_rows = [
        row
        for row in premium_rows
        if _number(row.get("overnight_vs_binance_pct")) is not None
    ]
    premiums = [float(_number(row.get("binance_premium_pct")) or 0.0) for row in premium_rows]
    overnight_vs_binance = [float(_number(row.get("overnight_vs_binance_pct")) or 0.0) for row in overnight_rows]
    captures = [_number(row.get("capture_pct")) for row in overnight_rows]
    captures = [float(value) for value in captures if value is not None]
    latest_week = _latest_week_ids(premium_rows, limit=1)
    latest_rows = [row for row in overnight_rows if latest_week and row.get("week_id") == latest_week[0]]
    latest_captures = [_number(row.get("capture_pct")) for row in latest_rows]
    latest_captures = [float(value) for value in latest_captures if value is not None]
    return {
        "summary_quality": summary_quality,
        "sample_count": len(premium_rows),
        "avg_binance_premium_pct": sum(premiums) / len(premiums),
        "avg_overnight_vs_binance_pct": sum(overnight_vs_binance) / len(overnight_vs_binance) if overnight_vs_binance else None,
        "avg_capture_pct": sum(captures) / len(captures) if captures else None,
        "latest_week_capture_pct": sum(latest_captures) / len(latest_captures) if latest_captures else None,
    }


def _weekend_review_diagnostic_frame(review_rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(review_rows or [])
    if frame.empty:
        return pd.DataFrame(columns=["股票", "周次", "数据质量", "P2 来源", "失败原因"])
    display = pd.DataFrame()
    display["股票"] = frame.get("ticker")
    display["周次"] = frame.get("week_id")
    display["数据质量"] = frame.get("data_quality").map(_data_quality_text)
    display["P2 来源"] = frame.apply(lambda row: _weekend_review_overnight_provider(row.to_dict()), axis=1)
    display["失败原因"] = frame.apply(lambda row: _weekend_review_failure_reason(row.to_dict(), str(row.get("data_quality") or "")), axis=1)
    display["P2 选中时间"] = frame.get("p2_first_valid_time")
    display["P2 延迟分钟"] = frame.get("p2_delay_minutes")
    display["P2 样本质量"] = frame.get("sample_status")
    display["P0 请求区间"] = frame.get("p0_request_window")
    display["P0 返回bars"] = frame.get("p0_returned_bar_count")
    display["P0 选中时间"] = frame.get("p0_selected_bar_time")
    display["P0 选中close"] = frame.get("p0_selected_bar_close")
    display["P0 volume"] = frame.get("p0_selected_bar_volume")
    display["Binance 合约"] = frame.get("binance_symbol")
    display["Binance 高点时间"] = frame.get("contract_sample_time")
    return display


def _backtest_diagnostic_frame(rows: list[dict]) -> pd.DataFrame:
    columns = [
        "周次",
        "股票",
        "数据质量",
        "失败原因",
        "P0 盘后锚点",
        "P1 周末高点",
        "P2 夜盘价格",
        "P0 请求区间",
        "P0 返回bars",
        "P2 请求区间",
        "P2 返回bars",
        "P2 第一根raw时间",
        "P2 第一根raw close",
        "P2 选中时间",
        "P2 选中close",
        "P2 延迟分钟",
        "P2 样本质量",
        "Binance 窗口",
        "Binance 合约",
    ]
    if not rows:
        return pd.DataFrame(columns=columns)
    records: list[dict[str, object]] = []
    for row in rows:
        p0 = _actual_afterhours_close(row)
        p1 = _first_number(row, ("binance_equivalent_max", "binance_weekend_max", "binance_weekend_max_price"))
        p2 = _first_number(row, ("overnight_first_1m_close", "broker_first_1m_close", "broker_open_close"))
        p2_delay = _first_number(row, ("p2_delay_minutes",))
        data_quality = str(row.get("transmission_data_quality") or row.get("data_quality") or "").strip().upper()
        p2_window = _weekend_review_time_range(
            row.get("stock_bar_requested_start") or row.get("overnight_bar_start_et") or row.get("p2_session_start_et"),
            row.get("stock_bar_requested_end") or row.get("overnight_bar_end_et"),
        )
        records.append(
            {
                "周次": str(row.get("week_id") or ""),
                "股票": str(row.get("ticker") or "").strip().upper(),
                "数据质量": _data_quality_text(data_quality),
                "失败原因": _backtest_row_failure_reason(row),
                "P0 盘后锚点": _money_or_missing(p0, "缺 P0"),
                "P1 周末高点": _money_or_missing(p1, "缺 P1"),
                "P2 夜盘价格": _money_or_missing(p2, "无 P2"),
                "P0 请求区间": _weekend_review_time_range(row.get("p0_request_start_et"), row.get("p0_request_end_et")),
                "P0 返回bars": int(_first_number(row, ("p0_returned_bar_count",)) or 0),
                "P2 请求区间": p2_window,
                "P2 返回bars": int(_first_number(row, ("stock_bar_raw_returned_count", "stock_bar_returned_count", "overnight_returned_bar_count")) or 0),
                "P2 第一根raw时间": _weekend_review_short_time(row.get("stock_bar_first_raw_time_et") or row.get("stock_bar_first_raw_time") or row.get("stock_bar_raw_first_time")) or "",
                "P2 第一根raw close": _money_or_missing(_first_number(row, ("stock_bar_first_raw_close", "stock_bar_raw_first_close")), ""),
                "P2 选中时间": _weekend_review_short_time(row.get("p2_first_valid_time") or row.get("stock_bar_selected_time") or row.get("broker_first_1m_time")) or "",
                "P2 选中close": _money_or_missing(_first_number(row, ("p2_first_valid_close", "stock_bar_selected_close", "broker_first_1m_close")), ""),
                "P2 延迟分钟": "" if p2_delay is None else int(p2_delay),
                "P2 样本质量": _data_quality_text(data_quality),
                "Binance 窗口": _weekend_review_time_range(row.get("binance_window_start_et"), row.get("binance_window_end_et")),
                "Binance 合约": str(row.get("binance_symbol") or row.get("symbol") or "").strip().upper(),
            }
        )
    return pd.DataFrame(records, columns=columns).fillna("")


def _weekend_review_frame(review_rows: list[dict]) -> pd.DataFrame:
    columns = [
        "周次",
        "股票",
        "P0 盘后锚点",
        "P1 周末高点",
        "P2 夜盘价格",
        "周末冲高",
        "高点回落",
        "最终传导",
        "高点兑现率",
        "样本质量",
    ]
    if not review_rows:
        return pd.DataFrame(columns=columns)
    records: list[dict] = []
    for row in review_rows:
        records.append(
            {
                "周次": row.get("week_id"),
                "股票": row.get("ticker"),
                "P0 盘后锚点": _money_or_missing(row.get("friday_afterhours_close"), "缺 P0"),
                "P1 周末高点": _money_or_missing(row.get("binance_price"), "缺 P1"),
                "P2 夜盘价格": _money_or_missing(row.get("broker_open_close"), "无 P2"),
                "周末冲高": _percent_or_missing(row.get("binance_premium_pct"), row),
                "高点回落": _percent_or_missing(row.get("overnight_vs_binance_pct"), row),
                "最终传导": _percent_or_missing(row.get("overnight_vs_afterhours_pct"), row),
                "高点兑现率": _percent_or_missing(row.get("capture_pct"), row),
                "样本质量": _weekend_review_short_sample_quality(row),
            }
        )
    return pd.DataFrame(records, columns=columns)


def _weekend_review_short_sample_quality(row: dict) -> str:
    quality = str(row.get("data_quality") or "").strip().upper()
    if quality == "OK":
        return "严格样本" if _number(row.get("p2_delay_minutes")) in {None, 0} else "首分钟样本"
    if quality == "DELAYED_OVERNIGHT_FIRST_VALID":
        return "延迟成交"
    if quality == "P0_UNVERIFIED":
        return "P0待验证"
    if quality in {"REGULAR_CLOSE_FALLBACK", "FALLBACK_REGULAR_CLOSE", "OBSERVE_ONLY"}:
        return "仅观察"
    if quality in {"MISSING_OVERNIGHT_FIRST_1M", "OVERNIGHT_PROVIDER_MISSING", "MISSING_BOATS_FIRST_1M", "PROVIDER_ERROR"}:
        return "不适合开盘平单"
    if _number(row.get("friday_afterhours_close")) is None or _number(row.get("binance_price")) is None:
        return "排除"
    return "仅观察"


def _style_weekend_review_frame(frame: pd.DataFrame):
    def color_value(value: object) -> str:
        text = str(value or "").replace("%", "").replace("+", "").strip()
        try:
            number = float(text) / 100 if "%" in str(value or "") else float(text)
        except (TypeError, ValueError):
            return "color: #64748b;"
        if number > 0.0005:
            return "color: #c2410c; font-weight: 800;"
        if number < -0.0005:
            return "color: #047857; font-weight: 800;"
        return "color: #64748b; font-weight: 700;"

    percent_columns = ["周末冲高", "高点回落", "最终传导", "高点兑现率"]
    color_subset = [column for column in percent_columns if column in frame.columns]
    styler = frame.style
    if not color_subset:
        return styler
    if hasattr(styler, "map"):
        return styler.map(color_value, subset=color_subset)
    return styler.applymap(color_value, subset=color_subset)


def _render_weekend_review_table(review_rows: list[dict]) -> None:
    frame = _weekend_review_frame(_display_weekend_review_rows(review_rows))
    if frame.empty:
        st.info("当前没有可展示的传导明细。")
        return
    st.dataframe(_style_weekend_review_frame(frame), width="stretch", hide_index=True)


def _ok_weekend_review_rows(review_rows: list[dict]) -> list[dict]:
    return [
        row
        for row in review_rows
        if str(row.get("data_quality") or "").strip().upper() == "OK"
        and not bool(row.get("holiday_rollover"))
        and not bool(row.get("holiday_shifted_overnight_session"))
    ]


def _tradable_weekend_review_rows(review_rows: list[dict]) -> list[dict]:
    return [
        row
        for row in review_rows
        if str(row.get("data_quality") or "").strip().upper() in {"OK", "DELAYED_OVERNIGHT_FIRST_VALID"}
        and not bool(row.get("holiday_rollover"))
        and not bool(row.get("holiday_shifted_overnight_session"))
    ]


def _observation_weekend_review_rows(review_rows: list[dict]) -> list[dict]:
    return [
        row
        for row in review_rows
        if str(row.get("data_quality") or "").strip().upper()
        in {
            "OBSERVE_ONLY",
            "OBSERVE_ANCHOR_ONLY",
            "MISSING_OVERNIGHT_FIRST_1M",
            "OVERNIGHT_PROVIDER_MISSING",
            "REGULAR_CLOSE_FALLBACK",
            "FALLBACK_REGULAR_CLOSE",
            "P0_UNVERIFIED",
            "TRADINGVIEW_WEBHOOK_SAMPLE",
            "TRADINGVIEW_CSV_SAMPLE",
            "MANUAL_BROKER_SAMPLE",
            "MANUAL_AFTERHOURS_SAMPLE",
            "ALPACA_BOATS_SAMPLE",
            "BOATS_DELAY_PENDING",
            "ALPACA_BOATS_PERMISSION",
            "MISSING_BOATS_FIRST_1M",
            "PROVIDER_ERROR",
        }
    ]


def _display_weekend_review_rows(review_rows: list[dict]) -> list[dict]:
    return [
        row
        for row in review_rows
        if str(row.get("data_quality") or "").strip().upper()
        in {
            "OK",
            "DELAYED_OVERNIGHT_FIRST_VALID",
            "OBSERVE_ONLY",
            "NO_AFTERHOURS_CLOSE",
            "MISSING_OVERNIGHT_FIRST_1M",
            "OVERNIGHT_PROVIDER_MISSING",
            "REGULAR_CLOSE_FALLBACK",
            "FALLBACK_REGULAR_CLOSE",
            "P0_UNVERIFIED",
            "TRADINGVIEW_WEBHOOK_SAMPLE",
            "TRADINGVIEW_CSV_SAMPLE",
            "MANUAL_BROKER_SAMPLE",
            "MANUAL_AFTERHOURS_SAMPLE",
            "ALPACA_BOATS_SAMPLE",
            "BOATS_DELAY_PENDING",
            "ALPACA_BOATS_PERMISSION",
            "MISSING_BOATS_FIRST_1M",
            "PROVIDER_ERROR",
        }
        or str(row.get("data_quality") or "").strip().upper().startswith("DEGRADED")
    ]


def _weekend_review_data_quality(
    row: dict,
    anchor_price: float | None,
    binance_price: float | None,
    premium_pct: float | None,
    broker_open_close: float | None = None,
) -> str:
    quality = str(row.get("transmission_data_quality") or row.get("data_quality") or "").strip().upper()
    raw_data_quality = str(row.get("data_quality") or "").strip().upper()
    status = str(row.get("status") or "").strip().upper()
    mapping_status = str(row.get("mapping_status") or "").strip().upper()
    cache_status = str(row.get("kline_cache_status") or row.get("cache_status") or "").strip().upper()
    if quality in {"OBSERVE_ONLY", "UNCONFIRMED_MAPPING"} or mapping_status == "CANDIDATE_OBSERVATION":
        return "OBSERVE_ONLY"
    if quality.startswith("DEGRADED"):
        return "DEGRADED"
    if quality in {"BLOCK_MAPPING", "NO_MAPPING", "MAPPING_MISSING"} or status == "BLOCK_MAPPING":
        return "MAPPING_MISSING"
    if quality == "OVERNIGHT_PROVIDER_MISSING":
        return "OVERNIGHT_PROVIDER_MISSING"
    if quality in {"REGULAR_CLOSE_FALLBACK", "FALLBACK_REGULAR_CLOSE"}:
        return "FALLBACK_REGULAR_CLOSE"
    if quality == "P0_UNVERIFIED":
        return "P0_UNVERIFIED"
    if quality == "DELAYED_OVERNIGHT_FIRST_VALID":
        return "DELAYED_OVERNIGHT_FIRST_VALID"
    if quality in {"TRADINGVIEW_WEBHOOK_SAMPLE", "TRADINGVIEW_CSV_SAMPLE", "MANUAL_BROKER_SAMPLE", "MANUAL_AFTERHOURS_SAMPLE"}:
        return quality
    if quality == "BINANCE_CONTRACT_NOT_LISTED_YET":
        return "BINANCE_CONTRACT_NOT_LISTED_YET"
    if quality in {"BOATS_DELAY_PENDING", "ALPACA_BOATS_PERMISSION", "MISSING_BOATS_FIRST_1M", "PROVIDER_ERROR"}:
        return quality
    if anchor_price is None or anchor_price <= 0 or quality in {"NO_AFTERHOURS_CLOSE", "NO_PRICE_ANCHOR"} or raw_data_quality == "NO_AFTERHOURS_CLOSE":
        return "NO_AFTERHOURS_CLOSE"
    if binance_price is None or binance_price <= 0 or quality in {
        "BINANCE_KLINE_UNAVAILABLE",
        "BINANCE_CONTRACT_NOT_LISTED_YET",
        "CONTRACT_MISSING",
        "DATA_UNAVAILABLE",
        "MISSING_BINANCE_WEEKEND_MAX",
    }:
        return "BINANCE_CONTRACT_NOT_LISTED_YET" if quality == "BINANCE_CONTRACT_NOT_LISTED_YET" else "MISSING_BINANCE_WEEKEND_MAX"
    if broker_open_close is None or broker_open_close <= 0 or quality in {"MISSING_OVERNIGHT_FIRST_1M", "MISSING_STOCK_FIRST_BAR", "NO_BROKER_OVERNIGHT_BAR", "HOLIDAY_OR_NO_SESSION"}:
        return "MISSING_OVERNIGHT_FIRST_1M"
    if cache_status in {"STALE", "STALE_CACHE", "CACHE_FALLBACK"} or quality in {"STALE_CACHE", "STALE_OR_MISALIGNED"}:
        return "STALE_CACHE"
    if quality in {"", "OK", "ESTIMATED_EXECUTION", "DELAYED_OVERNIGHT_FIRST_VALID"} and anchor_price is not None and anchor_price > 0 and binance_price is not None and binance_price > 0 and broker_open_close is not None and broker_open_close > 0:
        if quality == "DELAYED_OVERNIGHT_FIRST_VALID":
            return "DELAYED_OVERNIGHT_FIRST_VALID"
        return "OK"
    if quality in {"INVALID", "MISSING", "DATA_INSUFFICIENT", "INVALID_PRICE"}:
        return "INVALID_PRICE"
    if quality in {"", "OK"}:
        return "OK"
    return "INVALID_PRICE"


def _weekend_review_status(data_quality: str, premium_pct: float | None) -> str:
    if data_quality == "OBSERVE_ONLY":
        return "仅观察"
    if data_quality == "NO_AFTERHOURS_CLOSE":
        return "缺盘后锚点"
    if data_quality in {"MISSING_OVERNIGHT_FIRST_1M", "OVERNIGHT_PROVIDER_MISSING", "BOATS_DELAY_PENDING", "ALPACA_BOATS_PERMISSION", "MISSING_BOATS_FIRST_1M", "PROVIDER_ERROR"}:
        return "不适合开盘平单"
    if data_quality in {"MISSING_OVERNIGHT_FIRST_1M", "OVERNIGHT_PROVIDER_MISSING", "REGULAR_CLOSE_FALLBACK", "FALLBACK_REGULAR_CLOSE", "P0_UNVERIFIED"}:
        return "仅观察"
    if data_quality in {"BOATS_DELAY_PENDING", "ALPACA_BOATS_PERMISSION", "MISSING_BOATS_FIRST_1M", "PROVIDER_ERROR"}:
        return "仅观察"
    if data_quality in {"TRADINGVIEW_WEBHOOK_SAMPLE", "TRADINGVIEW_CSV_SAMPLE", "MANUAL_BROKER_SAMPLE", "MANUAL_AFTERHOURS_SAMPLE"}:
        return _weekend_review_sample_status(data_quality)
    if data_quality == "DELAYED_OVERNIGHT_FIRST_VALID":
        return "延迟成交样本"
    if str(data_quality or "").startswith("DEGRADED"):
        return "降级样本"
    if data_quality != "OK" or premium_pct is None:
        return "排除"
    return "严格正式样本"


def _weekend_review_row_status(data_quality: str, premium_pct: float | None, row: dict) -> str:
    labels: list[str] = []
    if not bool(row.get("last_trading_day_is_friday", True)):
        labels.append("周五休市，使用本周最后交易日")
    if bool(row.get("holiday_shifted_overnight_session") or row.get("holiday_rollover")):
        labels.append("夜盘顺延")
    base = _weekend_review_status(data_quality, premium_pct)
    if labels:
        labels.append(base)
        return " ? ".join(labels)
    return base


def _weekend_review_sample_status(data_quality: str, row: dict | None = None) -> str:
    quality = str(data_quality or "").strip().upper()
    raw = dict(row or {})
    if quality == "OK":
        if bool(raw.get("holiday_shifted_overnight_session") or raw.get("holiday_rollover")):
            return _weekend_review_sample_status_with_context("假期顺延样本", raw)
        overnight_provider = str(raw.get("overnight_provider") or raw.get("broker_provider") or raw.get("stock_bar_provider") or "").strip().upper()
        overnight_quality = str(raw.get("overnight_quality") or raw.get("broker_quality") or raw.get("stock_bar_quality") or "").strip().upper()
        if overnight_provider == "ALPACA_BOATS" or overnight_quality == "ALPACA_BOATS_SAMPLE":
            return _weekend_review_sample_status_with_context("Alpaca BOATS 样本", raw)
        return _weekend_review_sample_status_with_context("严格正式样本", raw)
    if quality == "ALPACA_BOATS_SAMPLE":
        return "Alpaca BOATS 样本"
    if quality == "DELAYED_OVERNIGHT_FIRST_VALID":
        delay = _number(raw.get("p2_delay_minutes"))
        label = f"延迟成交样本（+{int(delay)} 分钟）" if delay is not None else "延迟成交样本"
        return _weekend_review_sample_status_with_context(label, raw)
    if quality == "P0_UNVERIFIED":
        return "P0 待验证样本"
    if quality == "TRADINGVIEW_WEBHOOK_SAMPLE":
        return "TradingView Webhook 样本"
    if quality == "TRADINGVIEW_CSV_SAMPLE":
        return "TradingView CSV 样本"
    if quality == "MANUAL_BROKER_SAMPLE":
        return "人工券商样本"
    if quality == "MANUAL_AFTERHOURS_SAMPLE":
        return "人工盘后样本"
    if quality in {"MISSING_OVERNIGHT_FIRST_1M", "OVERNIGHT_PROVIDER_MISSING", "BOATS_DELAY_PENDING", "ALPACA_BOATS_PERMISSION", "MISSING_BOATS_FIRST_1M", "PROVIDER_ERROR"}:
        return _weekend_review_sample_status_with_context("不适合开盘平单", raw)
    if quality in {"REGULAR_CLOSE_FALLBACK", "FALLBACK_REGULAR_CLOSE", "NO_AFTERHOURS_CLOSE", "OBSERVE_ONLY"}:
        return _weekend_review_sample_status_with_context("观察样本", raw)
    if quality.startswith("DEGRADED"):
        return _weekend_review_sample_status_with_context("降级样本", raw)
    return _weekend_review_sample_status_with_context("观察样本", raw)


def _weekend_review_sample_status_with_context(base: str, row: dict) -> str:
    labels: list[str] = []
    if not bool(row.get("last_trading_day_is_friday", True)):
        labels.append("周五休市，使用本周最后交易日")
    if bool(row.get("holiday_shifted_overnight_session") or row.get("holiday_rollover")) and "顺延" not in base:
        labels.append("夜盘顺延")
    if not labels:
        return base
    labels.append(base)
    return " / ".join(labels)


def _weekend_review_rank(row: dict) -> tuple[int, float]:
    premium = _number(row.get("capture_pct") or row.get("binance_premium_pct"))
    binance = _number(row.get("binance_price"))
    broker = _number(row.get("broker_open_close"))
    quality = str(row.get("data_quality") or "").strip().upper()
    quality_rank = {
        "OK": 4,
        "DELAYED_OVERNIGHT_FIRST_VALID": 3,
        "TRADINGVIEW_WEBHOOK_SAMPLE": 3,
        "TRADINGVIEW_CSV_SAMPLE": 3,
        "MANUAL_BROKER_SAMPLE": 3,
        "MANUAL_AFTERHOURS_SAMPLE": 2,
        "OBSERVE_ANCHOR_ONLY": 2,
        "OBSERVE_ONLY": 2,
        "DEGRADED": 1,
    }.get(
        "DEGRADED" if quality.startswith("DEGRADED") else quality,
        0,
    )
    valid = quality_rank if binance is not None and (quality != "OK" or broker is not None) else 0
    return (valid, abs(float(premium or 0.0)))


def _weekend_review_stock_reference_date(row: dict) -> str:
    for key in ("regular_close_date", "friday_close_date", "anchor_ts", "weekend_spread_window_start"):
        value = str(row.get(key) or "").strip()
        if value:
            return _stock_reference_date_text(value)
    return ""


def _stock_reference_date_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
        return text[:10]
    if text.isdigit():
        try:
            timestamp = int(text)
            if timestamp > 10_000_000_000:
                timestamp = timestamp / 1000
            return datetime.fromtimestamp(timestamp, timezone.utc).astimezone(ET).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return text[:10]
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return text[:10]
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ET)
    return parsed.astimezone(ET).date().isoformat()


def _weekend_review_contract_sample_time(row: dict) -> str:
    for key in (
        "binance_weekend_max_time",
        "oracle_weekend_high_time",
        "weekend_spread_peak_time",
        "binance_entry_ts",
        "entry_ts",
        "sample_time",
        "updated_at",
    ):
        value = str(row.get(key) or "").strip()
        if value:
            return _weekend_review_short_time(value)
    return ""


def _weekend_review_afterhours_time(row: dict) -> str:
    for key in ("last_trading_day_afterhours_time", "friday_afterhours_bar_start_et", "friday_afterhours_time", "afterhours_reference_time", "anchor_ts"):
        value = str(row.get(key) or "").strip()
        if value:
            return _weekend_review_short_time(value)
    return ""


def _weekend_review_broker_first_time(row: dict) -> str:
    for key in ("p2_first_valid_time", "overnight_first_1m_time", "overnight_bar_start_et", "broker_first_1m_time", "broker_bar_start_time", "stock_bar_timestamp", "broker_overnight_open_ts"):
        value = str(row.get(key) or "").strip()
        if value:
            return _weekend_review_short_time(value)
    return ""


def _parse_et_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        try:
            timestamp = int(text)
            if timestamp > 10_000_000_000:
                timestamp = timestamp / 1000
            return datetime.fromtimestamp(timestamp, timezone.utc).astimezone(ET)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ET)
    return parsed.astimezone(ET)


def _p2_delay_minutes_from_row(row: dict, broker_open_close: float | None) -> float | None:
    explicit = _first_number(row, ("p2_delay_minutes",))
    if explicit is not None:
        return explicit
    if broker_open_close is None:
        return None
    bar_time = None
    for key in ("p2_first_valid_time", "overnight_first_1m_time", "overnight_bar_start_et", "broker_first_1m_time", "broker_bar_start_time", "stock_bar_timestamp", "broker_overnight_open_ts"):
        bar_time = _parse_et_datetime(row.get(key))
        if bar_time is not None:
            break
    session_time = None
    for key in ("p2_session_start_et", "monday_reference_time_et", "stock_bar_requested_start", "overnight_bar_start_et"):
        session_time = _parse_et_datetime(row.get(key))
        if session_time is not None:
            break
    if bar_time is not None and session_time is not None:
        return max(0, int((bar_time - session_time).total_seconds() // 60))
    if bar_time is not None and bar_time.hour == 20:
        return max(0, int(bar_time.minute))
    return 0


def _weekend_review_overnight_provider(row: dict) -> str:
    quality = str(row.get("transmission_data_quality") or row.get("overnight_quality") or row.get("data_quality") or "").strip().upper()
    raw = str(row.get("overnight_provider") or row.get("broker_provider") or row.get("stock_bar_provider") or "").strip()
    if quality == "OVERNIGHT_PROVIDER_MISSING" or not raw or raw.lower() in {"none", "anchor_source"}:
        return "美股夜盘数据源未配置"
    return raw


def _price_source_text(value: object) -> str:
    text = str(value or "").strip()
    if not text or text.lower() in {"none", "anchor_source"}:
        return "未配置"
    upper = text.upper()
    return {
        "FALLBACK_REGULAR_CLOSE": "常规收盘回退",
        "REGULAR_CLOSE_FALLBACK": "常规收盘回退",
        "P0_UNVERIFIED": "P0 待验证",
        "FMP_AFTERHOURS_1M_BAR": "FMP 盘后 1m",
        "FMP_AFTERHOURS_QUOTE_ANCHOR": "FMP quote 盘后锚点",
        "FMP_AFTERHOURS_TRADE": "FMP 盘后成交",
        "FMP_AFTERHOURS_QUOTE_MID": "FMP 盘后中间价",
        "FMP": "FMP 盘后",
        "ALPACA_AFTERHOURS_1M_BAR": "Alpaca 盘后 1m",
        "ALPACA_AFTERHOURS": "Alpaca 盘后",
        "ALPACA_AFTERHOURS_SIP": "Alpaca SIP 盘后",
        "ALPACA_AFTERHOURS_BOATS": "Alpaca BOATS 盘后",
        "ALPACA_BOATS": "Alpaca BOATS",
        "TRADINGVIEW_WEBHOOK": "TradingView Webhook",
        "TRADINGVIEW_CSV": "TradingView CSV",
        "MANUAL_OVERNIGHT_1M": "手动夜盘",
        "MANUAL_AFTERHOURS_1M": "手动盘后",
        "BINANCE_USDT_M": "Binance USDT-M",
    }.get(upper, text)


def _weekend_review_failure_reason(row: dict, data_quality: str) -> str:
    quality = str(data_quality or "").strip().upper()
    if quality == "OK":
        return ""
    if quality == "OBSERVE_ONLY":
        return "仅观察样本，不计入正式统计"
    if quality == "NO_AFTERHOURS_CLOSE":
        return str(row.get("friday_afterhours_reason") or row.get("afterhours_missing_reason") or "缺少最后交易日盘后价格")
    if quality == "OVERNIGHT_PROVIDER_MISSING":
        return "美股夜盘数据源未配置"
    if quality in {"REGULAR_CLOSE_FALLBACK", "FALLBACK_REGULAR_CLOSE"}:
        return "P0 使用常规收盘回退，仅观察"
    if quality == "P0_UNVERIFIED":
        return "P0 缺少原始盘后 1m bar 验证"
    if quality == "BOATS_DELAY_PENDING":
        return "BOATS 历史数据可能延迟，请稍后重试"
    if quality == "ALPACA_BOATS_PERMISSION":
        return "Alpaca BOATS 权限不足"
    if quality == "MISSING_BOATS_FIRST_1M":
        return "夜盘开盘窗口内无有效 1m K线"
    if quality == "PROVIDER_ERROR":
        return str(row.get("overnight_reason") or "夜盘数据源报错")
    if quality == "TRADINGVIEW_WEBHOOK_SAMPLE":
        return "P0/P2 来自 TradingView Webhook，作为观察样本"
    if quality == "TRADINGVIEW_CSV_SAMPLE":
        return "P0/P2 来自 TradingView CSV，作为观察样本"
    if quality == "MANUAL_BROKER_SAMPLE":
        return "P2 来自手动补数"
    if quality == "MANUAL_AFTERHOURS_SAMPLE":
        return "P0 来自手动补数"
    if quality == "MISSING_OVERNIGHT_FIRST_1M":
        return str(row.get("overnight_reason") or "夜盘开盘窗口内无有效 1m K线")
    if quality == "BINANCE_CONTRACT_NOT_LISTED_YET":
        onboard = str(row.get("binance_contract_onboard_time_et") or "").strip()
        return f"Binance 合约当周尚未上线（上线时间：{_weekend_review_short_time(onboard)}）" if onboard else "Binance 合约当周尚未上线"
    if quality in {"CONTRACT_MISSING", "BINANCE_KLINE_UNAVAILABLE", "MISSING_BINANCE_WEEKEND_MAX"}:
        return "缺少 Binance 周末 1m K线"
    if quality == "HOLIDAY_OR_NO_SESSION":
        return "非正常交易日或无夜盘 session"
    return str(row.get("error_message") or _data_quality_text(quality))


def _weekend_review_anchor_source(row: dict) -> str:
    source = str(row.get("anchor_source") or row.get("afterhours_reference_source") or "").strip().upper()
    if source in {"ANCHOR_REGULAR_CLOSE_ONLY", "REGULAR_CLOSE", "REGULAR_CLOSE_FALLBACK"}:
        return "常规收盘回退"
    if row.get("afterhours_reference_price") is not None or "AFTERHOURS" in source or "AFTERMARKET" in source:
        return "盘后锚点"
    if source:
        return _data_quality_text(source)
    return "锚点缺失"


def _weekend_review_binance_window(row: dict) -> str:
    start = _weekend_review_short_time(row.get("binance_window_start_et") or row.get("weekend_spread_window_start"))
    end = _weekend_review_short_time(row.get("binance_window_end_et") or row.get("weekend_spread_window_end"))
    if not start and not end:
        return ""
    return f"{start} - {end}" if start and end else start or end


def _weekend_review_time_range(start_value: object, end_value: object) -> str:
    start = _weekend_review_short_time(start_value)
    end = _weekend_review_short_time(end_value)
    if not start and not end:
        return ""
    return f"{start} - {end}" if start and end else start or end


def _weekend_review_short_time(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.isdigit():
        try:
            timestamp = int(text)
            if timestamp > 10_000_000_000:
                timestamp = timestamp / 1000
            return datetime.fromtimestamp(timestamp, timezone.utc).astimezone(ET).strftime("%Y-%m-%d %H:%M ET")
        except (OverflowError, OSError, ValueError):
            return text[:16]
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return text[:16]
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ET)
    return parsed.astimezone(ET).strftime("%Y-%m-%d %H:%M ET")


def _weekend_review_premium_pct(row: dict, anchor_price: float | None) -> float | None:
    for key in ("sunday_max_premium_bps", "oracle_weekend_high_premium_bps", "entry_premium_bps"):
        bps = _number(row.get(key))
        if bps is not None:
            return bps / 100.0
    for key in ("weekend_spread_peak_premium_pct", "primary_spread_pct", "spread_pct"):
        pct = _number(row.get(key))
        if pct is not None:
            return pct
    binance_price = _first_number(
        row,
        (
            "binance_weekend_max_price",
            "oracle_weekend_high_bid",
            "weekend_spread_peak_binance_price",
            "weekend_spread_peak_price",
            "binance_entry_bid",
            "entry_price",
            "binance_last_price",
        ),
    )
    if anchor_price is None or anchor_price <= 0 or binance_price is None:
        return None
    return (binance_price - anchor_price) / anchor_price * 100.0


def _weekend_review_binance_price(row: dict, anchor_price: float | None, premium_pct: float | None) -> float | None:
    price = _first_number(
        row,
        (
            "oracle_weekend_high_bid",
            "weekend_spread_peak_binance_price",
            "weekend_spread_peak_price",
            "binance_entry_bid",
            "entry_price",
            "binance_last_price",
        ),
    )
    if price is not None:
        return price
    if anchor_price is not None and premium_pct is not None:
        return anchor_price * (1.0 + premium_pct / 100.0)
    return None


def _first_number(row: dict, keys: tuple[str, ...]) -> float | None:
    for key in keys:
        number = _number(row.get(key))
        if number is not None:
            return number
    return None


def _latest_week_ids(rows: list[dict], *, limit: int) -> list[str]:
    week_ids = sorted(
        {str(row.get("week_id") or "").strip() for row in rows if str(row.get("week_id") or "").strip()},
        key=_week_id_sort_key,
        reverse=True,
    )
    return week_ids[:limit]


def _week_id_sort_key(week_id: str) -> tuple[int, int, str]:
    text = str(week_id or "").strip()
    if "-W" in text:
        year, week = text.split("-W", 1)
        try:
            return (int(year), int(week), text)
        except ValueError:
            return (0, 0, text)
    return (0, 0, text)


def _money_text(value: object, *, missing: str = "--") -> str:
    number = _number(value)
    if number is None:
        return missing
    return f"${number:,.2f}"


def _signed_money_text(value: object, *, missing: str = "暂缺") -> str:
    number = _number(value)
    if number is None:
        return missing
    sign = "+" if number >= 0 else "-"
    return f"{sign}${abs(number):,.2f}"


def _review_percent_text(value: object) -> str:
    number = _number(value)
    if number is None:
        return "暂无数据"
    return f"{number:+.2f}%"


def _backtest_anchor_mapping(
    tickers: list[str] | None = None,
    *,
    weeks: int = 4,
    cache: CacheReadModel | None = None,
    afterhours_provider=None,
    now: datetime | None = None,
) -> dict[str, dict]:
    rows = list(st.session_state.get("weekend_spread_realtime_rows") or [])
    result: dict[str, dict] = {}
    for row in rows:
        ticker = str(row.get("ticker") or "").upper()
        if not ticker:
            continue
        result[ticker] = {
            "afterhours_reference_price": row.get("afterhours_reference_price"),
            "regular_close_price": row.get("regular_close_price") or row.get("friday_close"),
            "regular_close_date": row.get("regular_close_date") or row.get("friday_close_date"),
            "friday_close": row.get("friday_close"),
            "friday_close_date": row.get("friday_close_date"),
        }
    _merge_historical_weekly_anchors(
        result,
        tickers or list(result),
        weeks=weeks,
        cache=cache,
        afterhours_provider=afterhours_provider,
        now=now,
    )
    return result


def _merge_historical_weekly_anchors(
    anchors: dict[str, dict],
    tickers: list[str],
    *,
    weeks: int,
    cache: CacheReadModel | None = None,
    afterhours_provider=None,
    now: datetime | None = None,
) -> None:
    if not tickers:
        return
    read_model = cache or CacheReadModel()
    afterhours_data_provider = afterhours_provider or CachedAfterhoursProvider(NullAfterhoursProvider())
    windows = recent_weekend_windows(weeks=max(1, int(weeks or 1)), now=now)
    for ticker in [str(item or "").strip().upper() for item in tickers if str(item or "").strip()]:
        try:
            history = read_model.get_price_history(ticker)
        except Exception:
            history = pd.DataFrame()
        weekly: dict[str, dict] = {}
        for window in windows:
            anchor = _historical_regular_close_anchor(history, window)
            if anchor:
                _merge_historical_afterhours_anchor(ticker, anchor, afterhours_data_provider)
                weekly[window.week_id] = anchor
        if not weekly:
            continue
        current = anchors.setdefault(ticker, {})
        existing_weekly = current.get("weekly_anchors") if isinstance(current.get("weekly_anchors"), dict) else {}
        current["weekly_anchors"] = {**existing_weekly, **weekly}
        latest = weekly.get(windows[0].week_id)
        if latest:
            current["regular_close_price"] = latest.get("regular_close_price")
            current["regular_close_date"] = latest.get("regular_close_date")
            current["anchor_source"] = latest.get("anchor_source")
            for key in (
                "afterhours_reference_price",
                "afterhours_reference_time",
                "afterhours_reference_source",
                "afterhours_bid",
                "afterhours_ask",
                "afterhours_mid",
                "afterhours_last_trade",
                "afterhours_volume",
                "afterhours_data_quality",
                "afterhours_missing_reason",
                "afterhours_cache_status",
                "afterhours_provider_name",
                "afterhours_anchor_status",
                "afterhours_error",
            ):
                if key in latest:
                    current[key] = latest.get(key)


def _historical_regular_close_anchor(history: pd.DataFrame, window) -> dict[str, object] | None:
    if history is None or history.empty or "date" not in history.columns or "close" not in history.columns:
        return None
    frame = history.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    target = getattr(window, "last_trading_day", None) or window.start_et.date()
    lower_bound = target - timedelta(days=7)
    candidates = frame[(frame["date"] <= target) & (frame["date"] >= lower_bound) & (frame["close"] > 0)]
    if candidates.empty:
        return None
    row = candidates.sort_values("date").iloc[-1]
    close = _number(row.get("close"))
    if close is None or close <= 0:
        return None
    close_date = row.get("date")
    close_date_text = close_date.isoformat() if hasattr(close_date, "isoformat") else str(close_date or "")
    return {
        "regular_close_price": close,
        "friday_close": close,
        "regular_close_date": close_date_text,
        "friday_close_date": close_date_text,
        "anchor_source": "HISTORICAL_REGULAR_CLOSE",
    }


def _merge_historical_afterhours_anchor(ticker: str, anchor: dict[str, object], afterhours_provider) -> None:
    regular_close_date = str(anchor.get("regular_close_date") or anchor.get("friday_close_date") or "").strip()
    if not ticker or not regular_close_date or afterhours_provider is None:
        return
    try:
        snapshot = afterhours_provider.get_afterhours_reference(
            ticker,
            regular_close_date=regular_close_date,
            force_refresh=False,
        )
    except Exception as exc:
        anchor.update(
            {
                "afterhours_reference_price": None,
                "afterhours_missing_reason": "FETCH_FAILED",
                "afterhours_error": f"{type(exc).__name__}: {exc}",
                "afterhours_cache_status": "FETCH_FAILED",
                "anchor_source": "HISTORICAL_REGULAR_CLOSE",
            }
        )
        return
    afterhours_price = _number(getattr(snapshot, "reference_price", None))
    anchor.update(
        {
            "afterhours_reference_price": afterhours_price,
            "afterhours_reference_time": getattr(snapshot, "reference_time", "") or "",
            "afterhours_reference_source": getattr(snapshot, "reference_source", "") or "",
            "afterhours_bid": getattr(snapshot, "bid", None),
            "afterhours_ask": getattr(snapshot, "ask", None),
            "afterhours_mid": getattr(snapshot, "mid", None),
            "afterhours_last_trade": getattr(snapshot, "last_trade", None),
            "afterhours_volume": getattr(snapshot, "volume", None),
            "afterhours_data_quality": getattr(snapshot, "data_quality", "") or "MISSING",
            "afterhours_missing_reason": getattr(snapshot, "missing_reason", "") or "",
            "afterhours_cache_status": getattr(snapshot, "cache_status", "") or "",
            "afterhours_provider_name": getattr(snapshot, "provider_name", "") or "",
            "afterhours_anchor_status": getattr(snapshot, "anchor_status", "") or "",
            "afterhours_error": getattr(snapshot, "error_message", "") or getattr(snapshot, "error", "") or "",
        }
    )
    if afterhours_price is not None and afterhours_price > 0:
        anchor["anchor_source"] = "HISTORICAL_AFTERHOURS_REFERENCE"


def _historical_afterhours_anchor_summary_text(anchors: dict[str, dict]) -> str:
    total = 0
    afterhours = 0
    cache = 0
    fallback = 0
    reasons: dict[str, int] = {}
    for root in anchors.values():
        weekly = root.get("weekly_anchors") if isinstance(root, dict) else None
        if not isinstance(weekly, dict):
            continue
        for row in weekly.values():
            if not isinstance(row, dict):
                continue
            total += 1
            if _number(row.get("afterhours_reference_price")) is not None:
                afterhours += 1
                if str(row.get("afterhours_cache_status") or "").strip().upper() in {"CACHE_HIT", "CACHE_FALLBACK"}:
                    cache += 1
            else:
                fallback += 1
                reason = str(row.get("afterhours_missing_reason") or "未返回原因").strip()
                reasons[reason] = reasons.get(reason, 0) + 1
    if total <= 0:
        return "尚无可分析的盘后锚点。"
    note = f"盘后锚点：已读取 {afterhours}/{total}；缓存 {cache}；回退 {fallback}。"
    if fallback and reasons:
        primary = sorted(reasons.items(), key=lambda item: item[1], reverse=True)[0]
        note += f" 主要回退原因：{_afterhours_reason_text(primary[0])}（{primary[1]} 条）。"
    return note


def _historical_afterhours_result_summary_text(rows: list[dict]) -> str:
    total = len(rows)
    if total <= 0:
        return "盘后锚点：本次回测无样本。"
    available = 0
    cache = 0
    fallback = 0
    missing_reasons: dict[str, int] = {}
    for row in rows:
        p0 = _actual_afterhours_close(row)
        if p0 is not None:
            available += 1
            cache_status = str(row.get("afterhours_cache_status") or row.get("cache_status") or "").strip().upper()
            if cache_status in {"CACHE_HIT", "CACHE_FALLBACK"}:
                cache += 1
            quality = str(row.get("p0_quality") or row.get("data_quality") or row.get("transmission_data_quality") or "").strip().upper()
            source = str(row.get("anchor_source") or row.get("afterhours_reference_source") or "").strip().upper()
            if quality in {"REGULAR_CLOSE_FALLBACK", "FALLBACK_REGULAR_CLOSE"} or "REGULAR_CLOSE" in source or row.get("p0_is_fallback"):
                fallback += 1
            continue
        reason = str(
            row.get("friday_afterhours_reason")
            or row.get("p0_failure_reason")
            or row.get("afterhours_missing_reason")
            or row.get("error_message")
            or "盘后锚点缺失"
        ).strip()
        missing_reasons[reason] = missing_reasons.get(reason, 0) + 1
    missing = total - available
    parts = [f"盘后锚点：实际用于回测 {available}/{total}"]
    if cache:
        parts.append(f"缓存 {cache}")
    if fallback:
        parts.append(f"常规收盘回退 {fallback}")
    if missing:
        parts.append(f"缺失 {missing}")
    note = "；".join(parts) + "。"
    if missing and missing_reasons:
        primary = sorted(missing_reasons.items(), key=lambda item: item[1], reverse=True)[0]
        note += f" 主要缺失原因：{_afterhours_reason_text(primary[0])}（{primary[1]} 条）。"
    return note


def _render_backtest_advanced_records() -> None:
    with st.expander("高级 / 前瞻记录", expanded=False):
        st.caption("前瞻记录只作为周末价差观察数据，不会写入交易日志、错题本或信号表现。")


def _render_mapping_tab(
    rows: list[dict],
    mapping: dict[str, dict],
    mapping_counts: dict[str, int],
    ignored: dict[str, dict] | None = None,
    *,
    watchlist: list[str] | None = None,
) -> None:
    st.subheader("Binance 美股 / TradFi 映射管理")
    st.caption("Binance 合约价格读取成功即视为映射可用；不想看的标的可以忽略，忽略后不再进入刷新、实时观察和历史回测。")
    ignored = ignored or load_binance_symbol_ignore()
    action_cols = st.columns([1.25, 1, 1])
    if action_cols[0].button(
        "一键同步 Binance 美股映射",
        width="stretch",
        type="primary",
        key="weekend_spread_scan_binance_equities",
        help="低频操作：从 Binance exchangeInfo 重新同步美股 / TradFi 映射。",
    ):
        _load_realtime_scan_records(watchlist or [], mapping, ignored, refresh_options={"scan": True})
        st.rerun()
    action_cols[1].caption("同步会更新本模块 mapping 缓存，不影响主系统观察池或持仓。")
    action_cols[2].caption("实时页只保留高频刷新价格和更新锚点。")
    records = _mapping_management_records(rows, mapping) + _ignored_mapping_records(ignored)
    state_counts = _mapping_state_counts(records)
    cols = st.columns(5)
    cols[0].metric("Binance 映射总数", len([record for record in records if record.get("state_group") != "ignored"]))
    cols[1].metric("映射可用", state_counts.get("available", 0) + state_counts.get("locked", 0))
    cols[2].metric("价格异常", state_counts.get("anomaly", 0))
    cols[3].metric("不可用", state_counts.get("unavailable", 0) + state_counts.get("missing", 0))
    cols[4].metric("已忽略", state_counts.get("ignored", 0))

    filter_options = ["全部", "映射可用", "价格异常", "不可用", "已忽略"]
    selected_filter = st.selectbox("映射筛选", filter_options, key="weekend_spread_mapping_filter")
    show_all = st.toggle("显示全部映射", value=False, key="weekend_spread_mapping_show_all")
    display_records = records if show_all else _filter_mapping_records(records, selected_filter)
    if display_records:
        edited_frame = _render_mapping_operation_table(display_records, selected_filter)
        _render_mapping_batch_actions(edited_frame, display_records, selected_filter, DEFAULT_LOCAL_MAPPING_PATH)
    elif records:
        st.success("当前筛选下没有需要处理的映射。打开“显示全部映射”可以查看全量扫描结果。")
    else:
        st.info("当前还没有 Binance 美股映射。请点击上方“一键同步 Binance 美股映射”。")

    _render_mapping_editor(mapping, rows, mapping_counts, DEFAULT_LOCAL_MAPPING_PATH, ignored)
    _render_mapping_diagnostics(_filter_ignored_mapping(mapping, ignored))
    _render_ignore_list(ignored)


def _mapping_record_from_row(row: dict, config: dict | None = None) -> dict:
    config = config or {}
    ticker = str(row.get("ticker") or "").strip().upper()
    binance_symbol = str(row.get("binance_symbol") or config.get("binance_symbol") or "").strip().upper()
    binance_price = _number(row.get("adjusted_binance_price") or row.get("binance_last_price"))
    stock_ref_price = _number(row.get("afterhours_reference_price") or row.get("regular_close_price") or row.get("friday_close"))
    price_diff_pct = None
    if binance_price is not None and stock_ref_price is not None and stock_ref_price > 0:
        price_diff_pct = (binance_price / stock_ref_price - 1) * 100
    display_label = _mapping_display_label_for_row({**config, **row, "binance_symbol": binance_symbol})
    if not binance_symbol:
        group = "missing"
        label = "无映射"
    elif display_label == "人工锁定":
        group = "locked"
        label = "人工锁定"
    elif display_label == MAPPING_ANOMALY_LABEL or (price_diff_pct is not None and abs(price_diff_pct) >= 8):
        group = "anomaly"
        label = MAPPING_ANOMALY_LABEL
    elif display_label == MAPPING_UNAVAILABLE_LABEL:
        group = "unavailable"
        label = MAPPING_UNAVAILABLE_LABEL
    elif display_label == MAPPING_AVAILABLE_LABEL:
        group = "available"
        label = MAPPING_AVAILABLE_LABEL
    else:
        group = "available" if binance_price is not None else "unavailable"
        label = MAPPING_AVAILABLE_LABEL if binance_price is not None else MAPPING_UNAVAILABLE_LABEL
    return {
        "ticker": ticker,
        "binance_symbol": binance_symbol,
        "binance_category": row.get("binance_category") or config.get("binance_category") or "",
        "underlying_type": row.get("underlying_type") or config.get("underlying_type") or "",
        "underlying_sub_type": row.get("underlying_sub_type") or config.get("underlying_sub_type") or "",
        "binance_price": binance_price,
        "stock_ref_price": stock_ref_price,
        "price_diff_pct": price_diff_pct,
        "state_group": group,
        "state_label": label,
        "market_type": row.get("market_type") or config.get("market_type") or "usdm_futures",
        "updated_at": row.get("updated_at") or config.get("updated_at") or "",
        "risk_note": str(config.get("risk_note") or row.get("mapping_risk") or ""),
    }


def _mapping_display_label_for_record(record: dict) -> str:
    return str(record.get("state_label") or {
        "available": MAPPING_AVAILABLE_LABEL,
        "anomaly": MAPPING_ANOMALY_LABEL,
        "unavailable": MAPPING_UNAVAILABLE_LABEL,
        "ignored": MAPPING_IGNORED_LABEL,
        "locked": "人工锁定",
        "review": MAPPING_ANOMALY_LABEL,
        "invalid": MAPPING_UNAVAILABLE_LABEL,
        "missing": "无映射",
    }.get(str(record.get("state_group") or ""), MAPPING_UNAVAILABLE_LABEL))


def _mapping_management_records(rows: list[dict], mapping: dict[str, dict]) -> list[dict]:
    return [_mapping_record_from_row(row, mapping.get(str(row.get("ticker") or "").upper(), {})) for row in rows]


def _ignored_mapping_records(ignored: dict[str, dict] | None = None) -> list[dict]:
    records: list[dict] = []
    for ticker, config in sorted((ignored or {}).items()):
        records.append(
            {
                "ticker": str(config.get("ticker") or ticker).strip().upper(),
                "binance_symbol": str(config.get("binance_symbol") or "").strip().upper(),
                "binance_category": "",
                "underlying_type": "",
                "underlying_sub_type": "",
                "binance_price": None,
                "stock_ref_price": None,
                "price_diff_pct": None,
                "state_group": "ignored",
                "state_label": MAPPING_IGNORED_LABEL,
                "market_type": "usdm_futures",
                "updated_at": str(config.get("ignored_at") or ""),
                "ignore_reason": str(config.get("ignore_reason") or ""),
                "ignored_at": str(config.get("ignored_at") or ""),
            }
        )
    return records


def _mapping_state_counts(records: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        group = str(record.get("state_group") or "unknown")
        counts[group] = counts.get(group, 0) + 1
    return counts


def _filter_mapping_records(records: list[dict], selected_filter: str) -> list[dict]:
    group_map = {
        "映射可用": {"available", "locked"},
        "价格异常": {"anomaly", "review"},
        "不可用": {"unavailable", "invalid", "missing"},
        "已忽略": {"ignored"},
        "全部": {"available", "locked", "anomaly", "review", "unavailable", "invalid", "missing", "ignored"},
    }
    allowed = group_map.get(selected_filter, group_map["全部"])
    return [record for record in records if str(record.get("state_group") or "") in allowed]


def _off_universe_mapping_records(rows: list[dict], mapping: dict[str, dict]) -> list[dict]:
    """Local weekend-spread mappings that are not in the current read-only filter scope."""
    row_tickers = {str(row.get("ticker") or "").strip().upper() for row in rows}
    records: list[dict] = []
    for ticker, config in (mapping or {}).items():
        normalized = str(ticker or "").strip().upper()
        if not normalized or normalized in row_tickers:
            continue
        records.append(
            _mapping_record_from_row(
                {
                    "ticker": normalized,
                    "binance_symbol": config.get("binance_symbol"),
                    "mapping_confidence": config.get("mapping_confidence"),
                },
                config,
            )
        )
    return records


def _mapping_counts(rows: list[dict], mapping: dict[str, dict]) -> dict[str, int]:
    mapped_rows = [row for row in rows if str(row.get("binance_symbol") or "").strip()]
    priced_rows = [
        row
        for row in mapped_rows
        if _number(row.get("adjusted_binance_price") or row.get("binance_last_price")) is not None
    ]
    local_mapping_count = len([key for key in (mapping or {}) if str(key or "").strip()])
    return {
        "universe_total": len(rows),
        "universe_mapping_count": len(mapped_rows),
        "price_row_count": len(priced_rows),
        "local_mapping_count": local_mapping_count,
        "scan_record_count": len(rows),
        "identified_equity_count": len(mapped_rows),
    }


def _mapping_management_counts(rows: list[dict], mapping: dict[str, dict]) -> dict[str, int]:
    counts = _mapping_counts(rows, mapping)
    records = _mapping_management_records(rows, mapping)
    off_universe_records = _off_universe_mapping_records(rows, mapping)
    state_counts = _mapping_state_counts(records)
    counts.update(
        {
            "usable_count": state_counts.get("available", 0) + state_counts.get("locked", 0),
            "review_count": state_counts.get("anomaly", 0) + state_counts.get("review", 0) + state_counts.get("invalid", 0) + state_counts.get("unavailable", 0),
            "manual_locked_count": state_counts.get("locked", 0),
            "invalid_count": state_counts.get("invalid", 0) + state_counts.get("unavailable", 0),
            "no_mapping_count": state_counts.get("missing", 0),
            "pending_count": 0,
            "other_tradfi_count": 0,
            "us_equity_verified_count": 0,
            "etf_verified_count": 0,
            "off_universe_mapping_count": len(off_universe_records),
        }
    )
    return counts


def _should_show_empty_mapping_state(mapping_counts: dict[str, int], scope: str) -> bool:
    return mapping_counts.get("universe_mapping_count", 0) <= 0 and scope != "\u65e0\u6620\u5c04"


def _empty_mapping_message(mapping_counts: dict[str, int], local_mapping_path: Path) -> str:
    lines = [
        "\u5c1a\u672a\u914d\u7f6e\u53ef\u7528\u7684 Binance \u6620\u5c04\u3002",
        "\u5468\u672b\u4ef7\u5dee\u53ea\u7ef4\u62a4\u672c\u6a21\u5757\u7684 mapping\uff0c\u4e0d\u4fee\u6539\u89c2\u5bdf\u6c60\u6216\u6301\u4ed3\u3002",
        f"\u672c\u5730 local mapping \u8def\u5f84\uff1a{local_mapping_path.as_posix()}",
        "\u793a\u4f8b\uff1aNVDA -> NVDAUSDT / USDT-M \u5408\u7ea6 / \u4eba\u5de5\u9501\u5b9a",
    ]
    if mapping_counts.get("local_mapping_count", 0) > 0:
        lines.append("\u5df2\u68c0\u6d4b\u5230\u672c\u5730 mapping\uff0c\u4f46\u5f53\u524d\u7b5b\u9009\u8303\u56f4\u5185\u6ca1\u6709\u53ef\u7528\u6620\u5c04\u3002")
    return "\n\n".join(lines)


def _off_universe_mapping_note(mapping_counts: dict[str, int]) -> str:
    if mapping_counts.get("local_mapping_count", 0) <= 0:
        return "\u6682\u65e0\u672c\u5730 mapping"
    if mapping_counts.get("local_mapping_count", 0) > 0 and mapping_counts.get("universe_mapping_count", 0) == 0:
        return "\u672c\u5730 mapping \u5b58\u5728\uff0c\u4f46\u5f53\u524d\u7b5b\u9009\u8303\u56f4\u5185\u6ca1\u6709\u53ef\u7528\u6620\u5c04"
    return "\u672c\u5730 mapping \u4ec5\u7528\u4e8e\u5468\u672b\u4ef7\u5dee\u6a21\u5757"


def _render_empty_mapping_state(mapping_counts: dict[str, int], local_mapping_path: Path) -> None:
    st.info(_empty_mapping_message(mapping_counts, local_mapping_path))


def _render_no_mapping_expander(rows: list[dict]) -> None:
    no_mapping_rows = [row for row in rows if not row.get("binance_symbol")]
    if not no_mapping_rows:
        return
    with st.expander(f"无映射标的: {len(no_mapping_rows)}", expanded=False):
        st.dataframe(_no_mapping_frame(no_mapping_rows), width="stretch", hide_index=True)


def _render_mapping_operation_table(records: list[dict], selected_filter: str) -> pd.DataFrame:
    frame = _mapping_management_frame(records)
    editor_key = f"weekend_spread_mapping_editor_{_state_key_text(selected_filter)}_{st.session_state.get('weekend_spread_mapping_editor_nonce', 0)}"
    column_config = {
        "选择": st.column_config.CheckboxColumn("选择", help="勾选后可批量忽略、恢复或保存合约修改。"),
        "Binance 合约": st.column_config.TextColumn("Binance 合约", help="可直接修改，例如 NVDAUSDT。保存后会写入本模块本地 mapping，并标记为人工锁定。"),
        "是否忽略": st.column_config.CheckboxColumn("是否忽略", help="改为勾选后点“忽略选中”，取消勾选后点“恢复选中”。"),
        "操作状态": st.column_config.TextColumn("操作状态"),
    }
    return st.data_editor(
        frame,
        width="stretch",
        hide_index=True,
        key=editor_key,
        disabled=["股票", "Binance 最新价", "盘后锚点", "相对盘后%", "状态", "更新时间", "操作状态"],
        column_config=column_config,
    )


def _render_mapping_batch_actions(
    edited_frame: pd.DataFrame,
    records: list[dict],
    selected_filter: str,
    local_mapping_path: Path,
) -> None:
    operations = _mapping_operation_rows(edited_frame, records)
    selected_count = len([row for row in operations if row.get("selected")])
    ignore_count = len(_pending_ignore_operations(operations))
    restore_count = len(_pending_restore_operations(operations))
    change_count = len(_pending_contract_changes(operations))
    if selected_count or ignore_count or restore_count or change_count:
        st.caption(
            f"已选 {selected_count} 个；待忽略 {ignore_count} 个；待恢复 {restore_count} 个；合约修改 {change_count} 个。"
        )

    col_ignore, col_restore, col_save, col_clear = st.columns(4)
    if col_ignore.button("忽略选中", key="weekend_spread_batch_ignore", width="stretch"):
        summary = _apply_ignore_operations(_pending_ignore_operations(operations), path=DEFAULT_IGNORE_PATH)
        _show_batch_operation_summary(summary, success_text="已忽略 {count} 个映射。")
    if col_restore.button("恢复选中", key="weekend_spread_batch_restore", width="stretch"):
        summary = _apply_restore_operations(_pending_restore_operations(operations), path=DEFAULT_IGNORE_PATH)
        _show_batch_operation_summary(summary, success_text="已恢复 {count} 个映射。")
    if col_save.button("保存合约修改", key="weekend_spread_batch_save_mapping", width="stretch"):
        summary = _apply_contract_changes(_pending_contract_changes(operations), path=local_mapping_path)
        _show_batch_operation_summary(summary, success_text="已保存 {count} 个合约修改，并标记为人工锁定。")
    if col_clear.button("清空选择", key="weekend_spread_batch_clear_selection", width="stretch"):
        st.session_state["weekend_spread_mapping_editor_nonce"] = int(st.session_state.get("weekend_spread_mapping_editor_nonce", 0)) + 1
        st.rerun()

    if selected_filter not in {"全部", "已忽略"} and records:
        candidates = [record for record in records if str(record.get("state_group") or "") != "ignored"]
        with st.expander("批量忽略当前筛选结果", expanded=False):
            st.caption(
                f"将忽略当前筛选下的 {len(candidates)} 个映射。它们不会再出现在实时观察和回测候选中，但可以在忽略清单中恢复。"
            )
            confirmed = st.checkbox("确认忽略当前筛选结果", key=f"weekend_spread_confirm_ignore_filter_{_state_key_text(selected_filter)}")
            if st.button("忽略当前筛选结果", key=f"weekend_spread_ignore_filter_{_state_key_text(selected_filter)}", disabled=not confirmed, width="stretch"):
                summary = _apply_ignore_operations(_records_to_operations(candidates), path=DEFAULT_IGNORE_PATH)
                _show_batch_operation_summary(summary, success_text="已忽略当前筛选下的 {count} 个映射。")


def _mapping_operation_rows(edited_frame: pd.DataFrame, records: list[dict]) -> list[dict]:
    if edited_frame is None or edited_frame.empty:
        return []
    original_by_ticker = {str(record.get("ticker") or "").strip().upper(): record for record in records}
    operations: list[dict] = []
    for _, row in edited_frame.iterrows():
        ticker = str(row.get("股票") or "").strip().upper()
        if not ticker:
            continue
        original = original_by_ticker.get(ticker, {})
        original_symbol = str(original.get("binance_symbol") or "").strip().upper()
        edited_symbol = str(row.get("Binance 合约") or "").strip().upper()
        was_ignored = str(original.get("state_group") or "") == "ignored"
        ignored_requested = bool(row.get("是否忽略"))
        operations.append(
            {
                "ticker": ticker,
                "original_symbol": original_symbol,
                "edited_symbol": edited_symbol,
                "selected": bool(row.get("选择")),
                "was_ignored": was_ignored,
                "ignored_requested": ignored_requested,
                "state_group": str(original.get("state_group") or ""),
                "market_type": str(original.get("market_type") or "usdm_futures"),
            }
        )
    return operations


def _records_to_operations(records: list[dict]) -> list[dict]:
    return [
        {
            "ticker": str(record.get("ticker") or "").strip().upper(),
            "original_symbol": str(record.get("binance_symbol") or "").strip().upper(),
            "edited_symbol": str(record.get("binance_symbol") or "").strip().upper(),
            "selected": True,
            "was_ignored": str(record.get("state_group") or "") == "ignored",
            "ignored_requested": True,
            "state_group": str(record.get("state_group") or ""),
            "market_type": str(record.get("market_type") or "usdm_futures"),
        }
        for record in records
        if str(record.get("ticker") or "").strip()
    ]


def _pending_ignore_operations(operations: list[dict]) -> list[dict]:
    return [
        row
        for row in operations
        if not row.get("was_ignored")
        and (row.get("selected") or row.get("ignored_requested"))
        and str(row.get("ticker") or "").strip()
        and str(row.get("edited_symbol") or row.get("original_symbol") or "").strip()
    ]


def _pending_restore_operations(operations: list[dict]) -> list[dict]:
    return [
        row
        for row in operations
        if row.get("was_ignored")
        and (row.get("selected") or not row.get("ignored_requested"))
        and str(row.get("ticker") or "").strip()
    ]


def _pending_contract_changes(operations: list[dict]) -> list[dict]:
    return [
        row
        for row in operations
        if str(row.get("edited_symbol") or "").strip().upper()
        and str(row.get("edited_symbol") or "").strip().upper() != str(row.get("original_symbol") or "").strip().upper()
    ]


def _apply_ignore_operations(operations: list[dict], *, path: Path) -> dict[str, object]:
    summary = {"count": 0, "failures": []}
    for row in operations:
        ticker = str(row.get("ticker") or "").strip().upper()
        symbol = str(row.get("edited_symbol") or row.get("original_symbol") or "").strip().upper()
        if not ticker or not symbol:
            summary["failures"].append(f"{ticker or '未知'}：缺少 Binance 合约")
            continue
        try:
            ignore_binance_symbol(ticker, symbol, ignore_reason="用户批量忽略", path=path)
        except Exception as exc:
            summary["failures"].append(f"{ticker}：{_mapping_editor_error_text(str(exc))}")
        else:
            summary["count"] = int(summary["count"]) + 1
    return summary


def _apply_restore_operations(operations: list[dict], *, path: Path) -> dict[str, object]:
    summary = {"count": 0, "failures": []}
    for row in operations:
        ticker = str(row.get("ticker") or "").strip().upper()
        if not ticker:
            summary["failures"].append("未知：缺少股票代码")
            continue
        try:
            restore_ignored_binance_symbol(ticker, path=path)
        except Exception as exc:
            summary["failures"].append(f"{ticker}：{_mapping_editor_error_text(str(exc))}")
        else:
            summary["count"] = int(summary["count"]) + 1
    return summary


def _apply_contract_changes(operations: list[dict], *, path: Path, ignore_path: Path = DEFAULT_IGNORE_PATH) -> dict[str, object]:
    summary = {"count": 0, "failures": []}
    for row in operations:
        ticker = str(row.get("ticker") or "").strip().upper()
        symbol = str(row.get("edited_symbol") or "").strip().upper()
        if not ticker:
            summary["failures"].append("未知：缺少股票代码")
            continue
        if not _is_valid_usdt_contract(symbol):
            summary["failures"].append(f"{ticker}：Binance 合约格式异常，请检查。")
            continue
        try:
            upsert_local_binance_symbol_mapping(
                ticker,
                symbol,
                market_type="usdm_futures",
                mapping_confidence="confirmed",
                risk_note="用户在映射管理中手动锁定。",
                path=path,
            )
            restore_ignored_binance_symbol(ticker, path=ignore_path)
        except Exception as exc:
            summary["failures"].append(f"{ticker}：{_mapping_editor_error_text(str(exc))}")
        else:
            summary["count"] = int(summary["count"]) + 1
    return summary


def _show_batch_operation_summary(summary: dict[str, object], *, success_text: str) -> None:
    count = int(summary.get("count") or 0)
    failures = [str(item) for item in summary.get("failures") or [] if str(item).strip()]
    if count:
        st.success(success_text.format(count=count))
    if failures:
        st.warning(f"失败 {len(failures)} 个：" + "；".join(failures[:5]))
    if count:
        st.rerun()
    if not count and not failures:
        st.info("没有可执行的映射操作。")


def _is_valid_usdt_contract(symbol: str) -> bool:
    text = str(symbol or "").strip().upper()
    return bool(text) and text.endswith("USDT") and text.isalnum()


def _state_key_text(value: object) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in str(value or "all"))


def _render_mapping_editor(
    mapping: dict[str, dict],
    rows: list[dict],
    mapping_counts: dict[str, int],
    local_mapping_path: Path,
    ignored: dict[str, dict] | None = None,
) -> None:
    with st.expander("高级单项操作", expanded=False):
        st.caption("主操作请直接在上方表格勾选或修改合约；这里保留单项兜底操作。忽略只影响周末价差模块，不会修改主系统观察池、持仓或交易记录。")
        ignored = ignored or {}
        candidates = [
            row
            for row in rows
            if str(row.get("ticker") or "").strip().upper()
            and str(row.get("binance_symbol") or "").strip().upper()
        ]
        labels = [f"{row.get('ticker')} · {row.get('binance_symbol')} · {_mapping_display_label_for_row(row)}" for row in candidates]
        selected_index = 0
        if labels:
            selected_label = st.selectbox("选择映射", labels, key="weekend_spread_mapping_action_select")
            selected_index = labels.index(selected_label)
            selected_row = candidates[selected_index]
            ticker = str(selected_row.get("ticker") or "").strip().upper()
            symbol = str(selected_row.get("binance_symbol") or "").strip().upper()
            reason = st.text_input("忽略原因，可选", value="", key="weekend_spread_ignore_reason")
            col_ignore, col_restore, col_modify = st.columns(3)
            if col_ignore.button("忽略", key="weekend_spread_ignore_selected", width="stretch"):
                ignore_binance_symbol(ticker, symbol, ignore_reason=reason or "用户忽略", path=DEFAULT_IGNORE_PATH)
                st.success(f"已忽略 {ticker}，后续不再纳入周末价差观察。")
                st.rerun()
            if col_restore.button("恢复", key="weekend_spread_restore_selected", width="stretch"):
                restore_ignored_binance_symbol(ticker, path=DEFAULT_IGNORE_PATH)
                st.success(f"已恢复 {ticker}，重新纳入周末价差观察。")
                st.rerun()
            new_symbol = st.text_input("修改 Binance 合约", value=symbol, key="weekend_spread_modify_symbol").strip().upper()
            if col_modify.button("保存合约", key="weekend_spread_modify_selected", width="stretch"):
                try:
                    upsert_local_binance_symbol_mapping(
                        ticker,
                        new_symbol,
                        market_type=str(selected_row.get("market_type") or "usdm_futures"),
                        mapping_confidence="auto_available",
                        path=local_mapping_path,
                    )
                except ValueError as exc:
                    st.warning(_mapping_editor_error_text(str(exc)))
                else:
                    st.success(f"已更新 {ticker} -> {new_symbol}")
                    st.rerun()
        else:
            st.info("当前没有可操作的映射。")
        st.caption(f"仅写入周末价差本地 mapping：{local_mapping_path.as_posix()}")


def _render_ignore_list(ignored: dict[str, dict] | None = None) -> None:
    ignored = ignored or {}
    with st.expander("忽略清单", expanded=False):
        records = _ignored_mapping_records(ignored)
        if not records:
            st.caption("暂无已忽略标的。")
            return
        st.dataframe(_mapping_management_frame(records), width="stretch", hide_index=True)
        tickers = [str(record.get("ticker") or "").strip().upper() for record in records if str(record.get("ticker") or "").strip()]
        selected = st.selectbox("选择要恢复的标的", tickers, key="weekend_spread_restore_ignored_select")
        if st.button("恢复选中标的", key="weekend_spread_restore_ignored_button", width="stretch"):
            restore_ignored_binance_symbol(selected, path=DEFAULT_IGNORE_PATH)
            st.success(f"已恢复 {selected}，重新纳入周末价差观察。")
            st.rerun()


def _mapping_editor_error_text(error_code: str) -> str:
    return {
        "ticker_required": "请填写股票代码",
        "binance_symbol_required": "请填写 Binance 合约，例如 NVDAUSDT",
    }.get(error_code, "映射保存失败，请检查输入")


def _render_refresh_diagnostics(rows: list[dict], ignored: dict[str, dict] | None = None) -> None:
    st.markdown("**刷新诊断**")
    frame = _refresh_diagnostics_frame(rows, ignored or {})
    if frame.empty:
        st.caption("暂无刷新诊断。")
        return
    st.dataframe(frame, width="stretch", hide_index=True)


def _refresh_diagnostics_frame(rows: list[dict], ignored: dict[str, dict] | None = None) -> pd.DataFrame:
    columns = ["股票", "Binance 合约", "尝试刷新", "Binance 返回价格", "Binance 最新价", "盘后锚点", "是否忽略", "状态", "失败原因"]
    records: list[dict[str, object]] = []
    ignored = ignored or {}
    for row in rows or []:
        ticker = str(row.get("ticker") or "").strip().upper()
        symbol = str(row.get("binance_symbol") or "").strip().upper()
        is_ignored = is_binance_symbol_ignored(ticker, symbol, ignored) or _mapping_display_label_for_row(row) == MAPPING_IGNORED_LABEL
        has_price = _row_has_binance_price(row)
        has_anchor = _row_has_afterhours_anchor(row)
        records.append(
            {
                "股票": ticker or "未识别",
                "Binance 合约": symbol or "缺少 Binance 合约",
                "尝试刷新": "否" if is_ignored or not symbol else "是",
                "Binance 返回价格": "是" if has_price else "否",
                "Binance 最新价": _money_text(row.get("adjusted_binance_price") or row.get("binance_last_price")),
                "盘后锚点": _money_text(row.get("afterhours_reference_price")) if has_anchor else "缺失",
                "是否忽略": "是" if is_ignored else "否",
                "状态": _realtime_row_status_label(row),
                "失败原因": _refresh_diagnostic_reason(row, ignored=ignored),
            }
        )
    return pd.DataFrame(records, columns=columns)


def _refresh_diagnostic_reason(row: dict, *, ignored: dict[str, dict] | None = None) -> str:
    ticker = str(row.get("ticker") or "").strip().upper()
    symbol = str(row.get("binance_symbol") or "").strip().upper()
    if ignored and is_binance_symbol_ignored(ticker, symbol, ignored):
        return "已忽略，跳过"
    if _mapping_display_label_for_row(row) == MAPPING_IGNORED_LABEL:
        return "已忽略，跳过"
    if not symbol:
        return "缺少 Binance 合约"
    if not _row_has_binance_price(row):
        error = str(row.get("error") or row.get("error_message") or "").strip()
        if error == "price_not_loaded":
            return "Binance 未返回该合约价格"
        if error:
            return _localized_realtime_error(error)
        return "Binance 价格为空"
    if not _row_has_afterhours_anchor(row):
        return "价格可用但锚点缺失"
    return "可计算价差"


def _live_frame(rows: list[dict]) -> pd.DataFrame:
    columns = [
        "股票",
        "美股盘后锚点",
        "Binance 最新",
        "相对盘后",
        "相对收盘",
        "状态",
        "标签",
        "更新时间",
    ]
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=columns)
    display = pd.DataFrame()
    display["股票"] = frame.get("ticker")
    display["美股盘后锚点"] = frame.apply(lambda row: _price_anchor_text(row.to_dict()), axis=1)
    display["Binance 最新"] = frame.get("binance_last_price").map(_money_text)
    display["相对盘后"] = frame.get("spread_vs_afterhours_pct").map(_afterhours_spread_text)
    display["相对收盘"] = frame.get("spread_vs_regular_close_pct").map(_percent_text)
    display["状态"] = frame.apply(lambda row: _realtime_row_status_label(row.to_dict()), axis=1)
    display["标签"] = frame.apply(lambda row: _realtime_row_tags_text(row.to_dict()), axis=1)
    display["更新时间"] = frame.get("updated_at").map(_short_hkt_time)
    return display[columns]


def _realtime_row_tags_text(row: dict) -> str:
    tags: list[str] = []
    if row.get("is_watchlist"):
        tags.append("观察池")
    if row.get("is_position"):
        tags.append("持仓")
    if row.get("is_core") or row.get("is_core_position"):
        tags.append("核心")
    if _is_manual_locked_mapping(row):
        tags.append("人工锁定")
    label = _mapping_display_label_for_row(row)
    if label in {MAPPING_AVAILABLE_LABEL, MAPPING_ANOMALY_LABEL, MAPPING_IGNORED_LABEL}:
        tags.append(label)
    status = _realtime_row_status_label(row)
    if status == "价格异常" and status not in tags:
        tags.append(status)
    return " / ".join(dict.fromkeys([tag for tag in tags if tag])) or "映射可用"


def _live_type_label(row: dict) -> str:
    if not _row_has_afterhours_anchor(row):
        return "锚点缺失"
    label = _mapping_display_label_for_row(row)
    if label == MAPPING_AVAILABLE_LABEL:
        return MAPPING_AVAILABLE_LABEL
    if label == MAPPING_ANOMALY_LABEL:
        return MAPPING_ANOMALY_LABEL
    if label == MAPPING_MANUAL_LOCKED:
        return "人工锁定"
    return label


def _display_frame(rows: list[dict]) -> pd.DataFrame:
    return _live_frame(rows)


def _no_mapping_frame(rows: list[dict]) -> pd.DataFrame:
    columns = [("ticker", "股票"), ("friday_close", "本周最后交易日收盘"), ("friday_close_date", "收盘时间")]
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=[label for _, label in columns])
    display = pd.DataFrame()
    for key, label in columns:
        display[label] = frame.get(key)
    display["本周最后交易日收盘"] = display["本周最后交易日收盘"].map(_money_text)
    display["收盘时间"] = display["收盘时间"].replace("", "暂无")
    return display


def _summary_frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows or [])


def _monday_outcome_frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows or [])


def _history_frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows or [])


def _backtest_frame(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows or [])
    if frame.empty:
        return frame
    if "排除 / 提醒" not in frame.columns:
        frame["排除 / 提醒"] = frame.apply(
            lambda row: " / ".join(
                part
                for part in (
                    _data_quality_text(row.get("data_quality")),
                    _localized_realtime_error(row.get("error_message")),
                )
                if str(part or "").strip()
            ),
            axis=1,
        )
    return frame


def _mapping_management_frame(records: list[dict], mapping: dict[str, dict] | None = None) -> pd.DataFrame:
    if mapping is not None:
        records = _mapping_management_records(records, mapping)
    columns = ["选择", "股票", "Binance 合约", "Binance 最新价", "盘后锚点", "相对盘后%", "状态", "是否忽略", "更新时间", "操作状态"]
    if not records:
        return pd.DataFrame(columns=columns)
    table_rows: list[dict] = []
    for record in records:
        is_ignored = str(record.get("state_group") or "") == "ignored"
        table_rows.append(
            {
                "选择": False,
                "股票": str(record.get("ticker") or "").upper(),
                "Binance 合约": str(record.get("binance_symbol") or "").upper(),
                "Binance 最新价": _money_text(record.get("binance_price")),
                "盘后锚点": _money_text(record.get("stock_ref_price")),
                "相对盘后%": _percent_text(record.get("price_diff_pct")),
                "状态": str(record.get("state_label") or _mapping_display_label_for_record(record)),
                "是否忽略": is_ignored,
                "更新时间": _short_hkt_time(record.get("updated_at")),
                "操作状态": "已忽略" if is_ignored else "未修改",
            }
        )
    return pd.DataFrame(table_rows, columns=columns)


def _binance_category_text(record: dict) -> str:
    category = str(record.get("binance_category") or "").strip()
    if category:
        return category
    underlying_type = str(record.get("underlying_type") or "").strip()
    underlying_sub_type = str(record.get("underlying_sub_type") or "").strip()
    combined = " / ".join(part for part in (underlying_type, underlying_sub_type) if part)
    return combined or "未标注"


def _refresh_single_realtime_row(
    ticker: str,
    rows: list[dict],
    *,
    mapping: dict[str, dict],
    action: str,
    tickers: list[str] | None = None,
    persist: bool = True,
) -> tuple[list[dict], dict, str]:
    normalized_ticker = str(ticker or "").strip().upper()
    normalized_action = str(action or "").strip().lower()
    current_rows = [dict(row) for row in rows or []]
    old_row = next((row for row in current_rows if str(row.get("ticker") or "").strip().upper() == normalized_ticker), {})
    if not normalized_ticker:
        return current_rows, {}, "缺少股票代码，无法刷新。"
    if normalized_action not in {"price", "anchor", "both"}:
        return current_rows, old_row, "未知刷新动作。"

    refresh_price = normalized_action in {"price", "both"}
    refresh_anchor = normalized_action in {"anchor", "both"}
    provider = _single_symbol_binance_provider() if refresh_price else _CachedRowBinanceProvider(current_rows)
    afterhours_provider = _fresh_afterhours_provider() if refresh_anchor else _CachedRowAfterhoursProvider(current_rows)
    refreshed_rows = build_weekend_spread_rows(
        [normalized_ticker],
        mapping=mapping,
        provider=provider,
        afterhours_provider=afterhours_provider,
        force_refresh=refresh_price,
        afterhours_force_refresh=refresh_anchor,
        expected_close_date=_expected_realtime_anchor_date(),
    )
    if not refreshed_rows:
        return current_rows, old_row, f"{normalized_ticker} 没有生成刷新结果。"

    refreshed = _preserve_single_realtime_context(old_row, refreshed_rows[0])
    updated_rows: list[dict] = []
    replaced = False
    for row in current_rows:
        if str(row.get("ticker") or "").strip().upper() == normalized_ticker:
            updated_rows.append(refreshed)
            replaced = True
        else:
            updated_rows.append(dict(row))
    if not replaced:
        updated_rows.append(refreshed)

    generated_at = datetime.now(timezone.utc).isoformat()
    updated_rows = annotate_cached_rows(updated_rows, cache_state="API_LIVE", generated_at=generated_at)
    if persist:
        write_weekend_spread_snapshot(
            updated_rows,
            mapping=mapping,
            tickers=tickers or [str(row.get("ticker") or "").strip().upper() for row in updated_rows if str(row.get("ticker") or "").strip()],
            generated_at=datetime.now(timezone.utc),
        )
    return updated_rows, refreshed, _single_refresh_message(normalized_ticker, refreshed, normalized_action)


def _preserve_single_realtime_context(old_row: dict, refreshed_row: dict) -> dict:
    item = dict(refreshed_row or {})
    for key in (
        "scan_detected_by",
        "underlying_type",
        "underlying_sub_type",
        "binance_category",
        "mapping_quality",
        "mapping_quality_reason",
        "is_watchlist",
        "is_position",
        "is_core",
        "is_core_position",
        "mapping_price_diff_pct",
    ):
        if key in old_row and (key not in item or item.get(key) in {None, ""}):
            item[key] = old_row.get(key)
    return item


def _single_refresh_message(ticker: str, row: dict, action: str) -> str:
    price = _number(row.get("binance_last_price"))
    anchor = _number(row.get("afterhours_reference_price"))
    if action == "price":
        if price is None:
            return f"{ticker} Binance 价格刷新失败：{_localized_realtime_error(row.get('error') or 'price_not_loaded')}"
        return f"{ticker} Binance 价格已刷新：{_money_text(price)}。"
    if action == "anchor":
        if anchor is None:
            reason = _afterhours_reason_text(row.get("afterhours_missing_reason")) or "未读取到盘后锚点"
            return f"{ticker} 盘后锚点仍缺失：{reason}。"
        return f"{ticker} 盘后锚点已重抓：{_money_text(anchor)}。"
    parts: list[str] = []
    parts.append(f"Binance {_money_text(price)}" if price is not None else "Binance 价格缺失")
    parts.append(f"盘后锚点 {_money_text(anchor)}" if anchor is not None else "盘后锚点缺失")
    return f"{ticker} 已完成单标的刷新：" + "，".join(parts) + "。"


def _render_row_details(
    rows: list[dict],
    *,
    all_rows: list[dict] | None = None,
    mapping: dict[str, dict] | None = None,
    tickers: list[str] | None = None,
) -> None:
    if not rows:
        return
    labels = [_detail_select_label(row) for row in rows]
    selected_label = st.selectbox(
        "查看单只详情",
        labels,
        key="weekend_spread_realtime_detail_symbol",
    )
    selected_index = labels.index(selected_label) if selected_label in labels else 0
    row = rows[selected_index]
    ticker = str(row.get("ticker") or "").upper()
    st.markdown(f'<section class="weekend-detail-card"><strong>{escape(ticker)} 价差详情</strong></section>', unsafe_allow_html=True)
    if mapping is not None:
        _render_single_row_refresh_actions(row, all_rows=all_rows or rows, mapping=mapping, tickers=tickers)
    col_price, col_quality, col_tags = st.columns(3)
    with col_price:
        st.markdown("**价格**")
        st.caption(f"盘后锚点：{_money_text(row.get('afterhours_reference_price'))}")
        st.caption(f"Binance 最新：{_money_text(row.get('binance_last_price'))}")
        st.caption(f"相对盘后：{_afterhours_spread_text(row.get('spread_vs_afterhours_pct'))}")
        st.caption(f"相对收盘：{_percent_text(row.get('spread_vs_regular_close_pct'))}")
    with col_quality:
        st.markdown("**数据质量**")
        st.caption(f"映射状态：{_mapping_display_label_for_row(row)}")
        st.caption(f"锚点状态：{_anchor_display_label_for_row(row)}")
        st.caption(f"Binance 状态：{_realtime_row_status_label(row)}")
        st.caption(f"更新时间：{_short_hkt_time(row.get('updated_at'))}")
        st.caption(f"锚点时间：{_short_hkt_time(row.get('afterhours_reference_time'))}")
    with col_tags:
        st.markdown("**关系标签**")
        st.caption(_realtime_row_tags_text(row))
        reason = _realtime_row_status_reason(row)
        if reason:
            st.caption(f"原因：{reason}")
        error = str(row.get("error") or "").strip()
        if error:
            st.caption(f"失败原因：{_localized_realtime_error(error)}")
        quality_reason = str(row.get("mapping_quality_reason") or "").strip()
        if quality_reason:
            st.caption(f"备注：{quality_reason}")


def _detail_select_label(row: dict) -> str:
    ticker = str(row.get("ticker") or "").strip().upper() or "未识别"
    spread = _afterhours_spread_text(row.get("spread_vs_afterhours_pct"))
    status = _realtime_row_status_label(row)
    return f"{ticker} ｜ {spread} ｜ {status}"


def _render_single_row_refresh_actions(
    row: dict,
    *,
    all_rows: list[dict],
    mapping: dict[str, dict],
    tickers: list[str] | None = None,
) -> None:
    ticker = str(row.get("ticker") or "").strip().upper()
    if not ticker:
        return
    st.caption("单标的刷新：用于排查单个价格或锚点异常，不会刷新全市场。")
    col_price, col_anchor, col_both = st.columns(3)
    actions = [
        (col_price, "只刷新 Binance 价格", "price"),
        (col_anchor, "只重抓盘后锚点", "anchor"),
        (col_both, "价格和锚点都刷新", "both"),
    ]
    for column, label, action in actions:
        if column.button(label, key=f"weekend_spread_single_{action}_{ticker}", width="stretch"):
            updated_rows, _, message = _refresh_single_realtime_row(
                ticker,
                all_rows,
                mapping=mapping,
                action=action,
                tickers=tickers,
            )
            st.session_state["weekend_spread_realtime_rows"] = updated_rows
            st.session_state["weekend_spread_realtime_flash"] = message
            st.rerun()


def _row_membership_text(row: dict) -> str:
    if not str(row.get("binance_symbol") or "").strip():
        return "未映射"
    labels: list[str] = []
    if row.get("is_watchlist"):
        labels.append("观察池")
    if row.get("is_position"):
        labels.append("持仓")
    if row.get("is_core") or row.get("is_core_position"):
        labels.append("核心仓")
    return " / ".join(labels) if labels else "全市场扫描"


def _scan_detected_by_text(value: object) -> str:
    text = str(value or "").strip()
    return {
        "auto_scan": "Binance 自动扫描",
        "binance_internal_category": "Binance 官方分类",
        "local_universe_fallback": "本地股票库校验",
        "local_mapping": "本地映射",
        "manual_mapping": "手动映射",
    }.get(text, "")


def _render_mapping_diagnostics(mapping: dict[str, dict]) -> None:
    with st.expander("映射诊断", expanded=False):
        validate = st.button("校验 symbol 映射", width="stretch", key="weekend_spread_validate_mapping")
        diagnostics = build_mapping_diagnostics(
            load_watchlist(),
            mapping=mapping,
            validate=validate,
            include_candidates=validate,
        )
        st.dataframe(_diagnostics_frame(diagnostics), width="stretch", hide_index=True)
        if validate:
            st.caption("候选 symbol 只表示 Binance 上存在相似合约，不代表真实美股映射关系。")


def _diagnostics_frame(rows: list[dict]) -> pd.DataFrame:
    columns = [
        ("ticker", "股票"),
        ("configured_symbol", "配置 symbol"),
        ("market_type", "市场类型"),
        ("mapping_confidence", "映射可信度"),
        ("validation_status", "校验状态"),
        ("last_validated_at", "校验时间"),
        ("price_available", "价格"),
        ("book_available", "买卖盘"),
        ("volume_available", "成交量"),
        ("funding_available", "资金费率"),
        ("candidate_scan_status", "候选扫描"),
        ("candidate_scan_message", "候选说明"),
        ("risk_note", "备注"),
        ("candidates", "候选"),
    ]
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=[label for _, label in columns])
    display = pd.DataFrame()
    for key, label in columns:
        display[label] = frame.get(key)
    for col in ("价格", "买卖盘", "成交量", "资金费率"):
        if col in display:
            display[col] = display[col].map(lambda value: "可用" if bool(value) else "暂缺")
    if "映射可信度" in display:
        display["映射可信度"] = display["映射可信度"].map(_mapping_confidence_label)
    return display


def _mapping_confidence_label(value: object) -> str:
    text = str(value or "").strip().lower()
    return {
        "confirmed": "人工锁定",
        "candidate": "映射可用",
        "unverified": "映射可用",
        "verified_ready": "映射可用",
        "stale": "映射可用",
        "rejected": "已忽略",
        "auto_available": "映射可用",
    }.get(text, "无映射" if not text else text)


def _candidate_text(value: object) -> str:
    if not isinstance(value, list) or not value:
        return ""
    labels = []
    for item in value[:5]:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "")
        market_type = str(item.get("market_type") or "")
        quote = str(item.get("quote_currency") or "")
        if symbol:
            labels.append(f"{symbol}({market_type}/{quote})")
    return ", ".join(labels)


def _strongest_signal_row(rows: list[dict]) -> dict | None:
    eligible = [row for row in rows if _is_realtime_main_row(row)]
    priced = [row for row in eligible if _number(row.get("spread_vs_afterhours_pct")) is not None]
    if not priced:
        priced = [row for row in eligible if _number(row.get("spread_pct")) is not None]
    if not priced:
        return None
    return max(
        priced,
        key=lambda row: abs(float(_number(row.get("spread_vs_afterhours_pct")) or _number(row.get("spread_pct")) or 0)),
    )


def _strongest_signal_warning(row: dict) -> str:
    if str(row.get("mapping_confidence") or "") != "confirmed":
        return "映射未人工锁定，仅作为观察偏离。"
    if str(row.get("primary_spread_anchor") or "") == "REGULAR_CLOSE_FALLBACK":
        return "盘后锚点缺失，当前使用常规收盘回退。"
    risk = _primary_risk_text(row)
    if risk:
        return risk
    return ""


def _primary_risk_text(row: dict) -> str:
    for key in ("mapping_risk", "liquidity_warning", "error"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    if str(row.get("mapping_confidence") or "") != "confirmed":
        return "映射未人工锁定，需复核"
    if str(row.get("primary_spread_anchor") or "") == "REGULAR_CLOSE_FALLBACK":
        return "盘后锚点缺失，使用收盘回退"
    return "仅观察"


def _last_price_status(row: dict) -> str:
    if not row.get("binance_symbol"):
        return "无映射"
    if row.get("status") == "OK" and row.get("binance_last_price") is not None:
        return "价格可用"
    if row.get("status") == "INVALID_SYMBOL":
        return "symbol 无效"
    return str(row.get("mapping_status") or row.get("status") or "数据不可用")


def _mapping_summary(mapping: dict[str, dict]) -> str:
    total = sum(1 for item in mapping.values() if item.get("enabled", True) and item.get("binance_symbol"))
    confirmed = sum(
        1
        for item in mapping.values()
        if item.get("enabled", True)
        and item.get("binance_symbol")
        and str(item.get("mapping_confidence") or "") == "confirmed"
    )
    return f"{total} 条本地映射，{confirmed} 条人工锁定"


def _binance_status_text(rows: list[dict], universe_mapping_count: int) -> str:
    if universe_mapping_count <= 0:
        return "无可用映射"
    if all(str(row.get("error") or "") == "price_not_loaded" for row in rows if row.get("binance_symbol")):
        return "等待刷新"
    if any(row.get("status") == "OK" for row in rows):
        return "可用"
    if any(row.get("status") in {"BINANCE_UNAVAILABLE", "PRICE_UNAVAILABLE"} for row in rows):
        return "部分不可用"
    if any(row.get("status") == "INVALID_SYMBOL" for row in rows):
        return "symbol 无效"
    return "待刷新"


def _market_price_source_status(rows: list[dict], market_type: str) -> str:
    market_rows = [
        row
        for row in rows
        if row.get("binance_symbol") and str(row.get("binance_market_type") or row.get("market_type") or "") == market_type
    ]
    if not market_rows:
        return "无数据"
    if all(str(row.get("error") or "") == "price_not_loaded" for row in market_rows):
        return "等待刷新"
    if any(row.get("status") == "OK" for row in market_rows):
        return "可用"
    return "待复核"


def _market_data_status(rows: list[dict], market_type: str) -> str:
    market_rows = [
        row
        for row in rows
        if row.get("binance_symbol") and str(row.get("binance_market_type") or row.get("market_type") or "") == market_type
    ]
    if not market_rows:
        return "无数据"
    if any(row.get("status") == "OK" for row in market_rows):
        return "可用"
    if any(row.get("status") in {"BINANCE_UNAVAILABLE", "PRICE_UNAVAILABLE"} for row in market_rows):
        return "不可用"
    return "待复核"


def _latest_updated_at(rows: list[dict]) -> str:
    values = [str(row.get("updated_at") or "") for row in rows if row.get("updated_at")]
    return values[-1] if values else ""


def _recorded_max_abs_spread(log_snapshot: dict) -> float | None:
    values = [log_snapshot.get("max_premium_pct"), log_snapshot.get("max_discount_pct")]
    numeric = [_number(value) for value in values]
    numeric = [value for value in numeric if value is not None]
    if not numeric:
        return None
    return max(numeric, key=lambda value: abs(float(value)))


def _cache_generated_text(cache_status: dict | None) -> str:
    if not cache_status:
        return "\u6682\u7f3a"
    generated_at = str(cache_status.get("generated_at") or "")
    return _short_hkt_time(generated_at) if generated_at else "\u6682\u7f3a"


def _cache_state_text(cache_status: dict | None) -> str:
    if not cache_status:
        return "\u6682\u65e0\u7f13\u5b58"
    state = str(cache_status.get("cache_state") or "")
    return {
        "FRESH": "\u6700\u65b0",
        "STALE": "\u5df2\u8fc7\u671f",
        "MAPPING_CHANGED": "\u6620\u5c04\u5df2\u53d8\u5316",
        "UNIVERSE_CHANGED": "\u89c2\u5bdf\u6c60\u5df2\u53d8\u5316",
        "REFRESH_FAILED": "\u5237\u65b0\u5931\u8d25\uff0c\u4f7f\u7528\u4e0a\u6b21\u6210\u529f\u7f13\u5b58",
        "API_LIVE": "API \u5b9e\u65f6",
        "MISSING": "\u6682\u65e0\u7f13\u5b58",
    }.get(state, state or "\u6682\u65e0\u7f13\u5b58")


def _refresh_error_text(rows: list[dict]) -> str:
    for row in rows:
        value = str(row.get("error") or "").strip()
        if value:
            return value
    return "Binance 刷新失败"


def _afterhours_reason_text(value: object) -> str:
    code = str(value or "").strip().upper()
    return {
        "PROVIDER_MISSING": "盘后数据源未配置",
        "API_KEY_MISSING": "盘后 API key 缺失",
        "NOT_FETCHED": "尚未读取盘后锚点",
        "FETCH_FAILED": "盘后锚点读取失败",
        "NO_ALPACA_AFTERHOURS_BAR": "缺少 Alpaca 盘后 1m bar",
        "NO_AFTERHOURS_BAR": "缺少盘后 1m bar",
        "NO_AFTERHOURS_TRADE": "缺少盘后成交",
        "NO_AFTERHOURS_QUOTE": "缺少 bid/ask 报价",
        "CACHE_MISSING": "盘后缓存缺失",
        "CACHE_CORRUPT": "盘后缓存损坏",
        "CACHE_DATE_MISMATCH": "盘后缓存日期不匹配",
        "STALE_CACHE": "盘后缓存过期",
        "FALLBACK_REGULAR_CLOSE": "常规收盘回退",
        "REGULAR_CLOSE_FALLBACK": "常规收盘回退",
    }.get(code, code or "盘后锚点缺失")


def _afterhours_source_text(value: object) -> str:
    code = str(value or "").strip()
    return {
        "POLYGON_OPEN_CLOSE_AFTERHOURS": "Polygon/Massive open-close afterHours",
        "POLYGON_TRADES_1955_2000": "Polygon/Massive 19:55-20:00 trade",
        "POLYGON_AFTERHOURS_LAST_TRADE": "Polygon/Massive afterhours last trade",
        "POLYGON_QUOTE_MID": "Polygon/Massive quote mid",
        "FMP_AFTERHOURS_TRADE": "FMP aftermarket trade",
        "FMP_AFTERHOURS_QUOTE_MID": "FMP aftermarket quote mid",
        "ALPHAVANTAGE_INTRADAY_EXTENDED": "Alpha Vantage extended-hours intraday",
    }.get(code, code or "盘后锚点来源缺失")


def _afterhours_cache_text(value: object) -> str:
    code = str(value or "").strip().upper()
    return {
        "API_LIVE": "API 读取",
        "CACHE_HIT": "缓存命中",
        "CACHE_FALLBACK": "缓存回退",
        "CACHE_MISSING": "盘后缓存缺失",
        "CACHE_CORRUPT": "盘后缓存损坏",
        "CACHE_DATE_MISMATCH": "盘后缓存日期不匹配",
        "NOT_FETCHED": "未读取",
    }.get(code, code or "未知")


def _afterhours_anchor_badge(row: dict) -> str:
    if _number(row.get("afterhours_reference_price")) is None:
        return "锚点缺失"
    status = str(row.get("afterhours_anchor_status") or "").strip().upper()
    cache_status = str(row.get("afterhours_cache_status") or "").strip().upper()
    if cache_status == "CACHE_DATE_MISMATCH":
        return "待复核"
    if status == "FINAL":
        return "已固定锚点"
    if status == "PROVISIONAL":
        return "临时锚点"
    return "盘后锚点"


def _price_anchor_text(row: dict) -> str:
    afterhours = _number(row.get("afterhours_reference_price"))
    if afterhours is not None:
        return f"盘后 ${afterhours:,.2f} ({_afterhours_anchor_badge(row)})"
    regular = _number(row.get("regular_close_price") or row.get("friday_close"))
    if regular is None:
        return "锚点缺失"
    reason = _afterhours_reason_text(row.get("afterhours_missing_reason"))
    return f"收盘 ${regular:,.2f} (锚点缺失: {reason})"


def _afterhours_spread_text(value: object) -> str:
    number = _number(value)
    if number is None:
        return "--"
    return f"{number:+.2f}%"


def _parse_utc_time(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        try:
            timestamp = int(text)
            if timestamp > 10_000_000_000:
                timestamp = timestamp / 1000
            return datetime.fromtimestamp(timestamp, timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_et_datetime(value: object) -> datetime | None:
    parsed = _parse_utc_time(value)
    return parsed.astimezone(ET) if parsed is not None else None


def _risk_badge_text(row: dict) -> str:
    risks: list[str] = []
    data_source = str(row.get("data_source_text") or "").strip()
    if data_source:
        risks.append(data_source)
    status = str(row.get("status") or "")
    confidence = str(row.get("mapping_confidence") or "")
    if confidence and confidence != "confirmed":
        risks.append("映射待核")
    if status == "INVALID_SYMBOL":
        risks.append("symbol 无效")
    if status in {"BINANCE_UNAVAILABLE", "PRICE_UNAVAILABLE"}:
        risks.append("Binance 不可用")
    if _number(row.get("afterhours_reference_price")) is None and row.get("binance_symbol"):
        risks.append(f"锚点缺失：{_afterhours_reason_text(row.get('afterhours_missing_reason'))}")
    elif str(row.get("afterhours_cache_status") or "") in {"CACHE_HIT", "CACHE_FALLBACK"}:
        risks.append(_afterhours_cache_text(row.get("afterhours_cache_status")))
    liquidity = str(row.get("liquidity_warning") or "")
    if "volume" in liquidity.lower():
        risks.append("成交量不足")
    if "spread" in liquidity.lower():
        risks.append("bid-ask 偏宽")
    if not risks:
        risks.append("正常")
    return " / ".join(dict.fromkeys(risks))


def _localized_realtime_error(value: object) -> str:
    text = str(value or "").strip()
    return {
        "price_not_loaded": "Binance 尚未刷新",
        "invalid_symbol": "Binance symbol 无效",
        "binance_price_missing": "Binance 价格缺失",
        "NO_MAPPING": "无映射",
    }.get(text, text or "未知错误")


def _short_hkt_time(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "暂缺"
    if text.isdigit():
        parsed = _parse_utc_time(text)
        return parsed.astimezone(HKT).strftime("%m-%d %H:%M HKT") if parsed is not None else text[:16]
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return text[:16]
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(HKT).strftime("%m-%d %H:%M HKT")


def _percent_text(value: object) -> str:
    number = _number(value)
    if number is None:
        return "暂缺"
    return f"{number:+.2f}%"


def _bps_text(value: object) -> str:
    number = _number(value)
    if number is None:
        return "暂缺"
    return f"{number:+.1f} bps"


def _ratio_text(value: object) -> str:
    number = _number(value)
    if number is None:
        return "暂缺"
    return f"{number:.2f}"


def _funding_text(value: object) -> str:
    number = _number(value)
    if number is None:
        return "暂缺"
    return f"{number:.4%}"


def _plain_number(value: object) -> str:
    number = _number(value)
    if number is None:
        return "暂缺"
    return f"{number:,.0f}"


def _plain_decimal_text(value: object) -> str:
    number = _number(value)
    if number is None:
        return "暂缺"
    return f"{number:,.4f}"


def _average_backtest_pullback(rows: list[dict]) -> float | None:
    values = [_number(row.get("short_return_at_open_pct")) for row in rows]
    values = [value for value in values if value is not None]
    if not values:
        return None
    return sum(values) / len(values)


def _number(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number
