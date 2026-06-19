from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from html import escape
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from data.equity_afterhours_provider import CachedAfterhoursProvider, NullAfterhoursProvider, default_afterhours_provider
from data.binance_equity_scan import (
    DEFAULT_BINANCE_EQUITY_SCAN_CACHE_PATH,
    MAPPING_ANCHOR_MISSING,
    MAPPING_AUTO_USABLE,
    MAPPING_INVALID,
    MAPPING_MANUAL_LOCKED,
    MAPPING_PRICE_UNVERIFIED,
    MAPPING_REVIEW,
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
    DEFAULT_LOCAL_MAPPING_PATH,
    build_mapping_diagnostics,
    build_weekend_spread_rows,
    load_binance_symbol_mapping,
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

TAB_REALTIME = "实时观察"
TAB_BACKTEST = "历史回测"
TAB_MAPPING = "映射管理"
HKT = ZoneInfo("Asia/Hong_Kong")
ET = ZoneInfo("America/New_York")


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
        @media (max-width: 860px) {
          .weekend-core-metrics {
            grid-template-columns: repeat(2, minmax(0, 1fr));
          }
          .weekend-core-flow {
            font-size: 23px;
          }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render() -> None:
    _apply_weekend_spread_layout_css()
    st.markdown(
        """
        <section class="zhx-page-head">
          <div>
            <span class="zhx-eyebrow">ZHX RESEARCH</span>
            <h1>周末价差观察台</h1>
            <p>观察本周最后交易日盘后收盘价、Binance 周末最高价和下周第一个交易日夜盘首分钟价格之间的传导关系。</p>
          </div>
        </section>
        """,
        unsafe_allow_html=True,
    )
    st.warning(RISK_NOTICE)

    mapping = load_binance_symbol_mapping()
    watchlist = load_watchlist()

    realtime_tab, backtest_tab, mapping_tab = st.tabs([TAB_REALTIME, TAB_BACKTEST, TAB_MAPPING])

    with realtime_tab:
        rows, mapping_counts = _render_realtime_tab(watchlist, mapping)
    with backtest_tab:
        _render_backtest_tab(watchlist, mapping)
    with mapping_tab:
        _render_mapping_tab(rows, mapping, mapping_counts)


def _weekend_scope_tickers(watchlist: list[str], mapping: dict[str, dict] | None = None) -> list[str]:
    tickers: list[str] = []
    seen: set[str] = set()
    scan_cache = read_binance_equity_scan_cache()
    sources: list[object] = []
    sources.extend(str(record.get("ticker") or "") for record in scan_cache.get("records") or [] if isinstance(record, dict))
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
) -> tuple[list[dict], dict[str, int]]:
    st.subheader("Binance 美股映射全市场扫描")
    st.caption("扫描 Binance USDT-M 合约里可能映射美股的标的，观察当前合约价格相对美股盘后锚点的偏离。")
    status_slot = st.empty()
    deviation_slot = st.empty()
    action_slot = st.empty()
    filter_slot = st.empty()
    table_slot = st.empty()
    advanced_slot = st.empty()
    with action_slot.container():
        refresh_options = _render_realtime_action_bar()
    scan_records, scan_status = _load_realtime_scan_records(watchlist, mapping, refresh_options=refresh_options)
    scan_mapping = scan_records_to_mapping(scan_records, mapping)
    scan_tickers = [str(record.get("ticker") or "").strip().upper() for record in scan_records if record.get("ticker")]
    rows, cache_status = _build_weekend_spread_rows_with_feedback(scan_tickers, mapping=scan_mapping, refresh_options=refresh_options)
    rows = _merge_scan_metadata(rows, scan_records, watchlist)
    st.session_state["weekend_spread_realtime_rows"] = rows
    st.session_state["weekend_spread_realtime_cache_status"] = cache_status
    st.session_state["weekend_spread_realtime_scan_status"] = scan_status

    mapping_counts = _mapping_counts(rows, scan_mapping)
    mapping_counts.update(
        {
            "scan_record_count": len(scan_records),
            "scan_cache_state": str(scan_status.get("cache_state") or ""),
            "scan_generated_at": str(scan_status.get("generated_at") or ""),
        }
    )

    with status_slot.container():
        _render_realtime_status_strip(rows, mapping_counts, cache_status)
    with deviation_slot.container():
        _render_largest_deviation(rows, mapping_counts)
    with filter_slot.container():
        visible_scope = _render_realtime_filters(rows)

    main_rows = _filter_live_rows_by_scope(rows, visible_scope)
    with table_slot.container():
        st.markdown("#### 实时价差表")
        if not scan_records and _should_show_empty_mapping_state(mapping_counts, "重点/有数据"):
            _render_empty_mapping_state(mapping_counts, DEFAULT_LOCAL_MAPPING_PATH)
        elif main_rows:
            st.dataframe(_live_frame(main_rows), width="stretch", hide_index=True)
            _render_row_details(main_rows)
        else:
            st.info("当前筛选下没有可展示的实时价差。可以切换筛选，或点击“一键同步 Binance 美股映射”。")

    with advanced_slot.container():
        with st.expander("高级设置 / 缓存管理", expanded=False):
            _render_no_mapping_expander(rows)
    return rows, mapping_counts


def _render_realtime_action_bar() -> dict[str, bool]:
    col_scan, col_refresh, col_anchor = st.columns([1.4, 1, 1])
    scan = col_scan.button("一键同步 Binance 美股映射", width="stretch", type="primary", key="weekend_spread_scan_binance_equities")
    refresh = col_refresh.button("刷新实时价格", width="stretch", key="weekend_spread_refresh")
    anchor_refresh = col_anchor.button("更新美股盘后锚点", width="stretch", key="weekend_spread_anchor_refresh")
    use_cache = False
    force_anchor = False
    clear_scan_cache = False
    with st.expander("数据源与补数工具", expanded=False):
        col_cache, col_force_anchor, col_clear = st.columns([1, 1, 1])
        use_cache = col_cache.button("使用缓存", width="stretch", key="weekend_spread_use_cache")
        force_anchor = col_force_anchor.button("强制重建锚点", width="stretch", key="weekend_spread_force_anchor_refresh")
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
        "scan": bool(scan),
        "use_cache": bool(use_cache),
        "refresh": bool(refresh),
        "anchor_refresh": bool(anchor_refresh),
        "force_anchor_refresh": bool(force_anchor),
        "clear_scan_cache": bool(clear_scan_cache),
    }


def _load_realtime_scan_records(
    watchlist: list[str],
    mapping: dict[str, dict],
    *,
    refresh_options: dict[str, bool] | None = None,
) -> tuple[list[dict], dict[str, object]]:
    options = refresh_options or {}
    force_scan = bool(options.get("scan"))
    use_cache = bool(options.get("use_cache"))
    cached = read_binance_equity_scan_cache()
    if not force_scan and cached.get("records") and (use_cache or cached.get("cache_state") == "FRESH"):
        return _tag_scan_records(list(cached.get("records") or []), watchlist), cached
    if not force_scan and cached.get("records"):
        return _tag_scan_records(list(cached.get("records") or []), watchlist), cached
    if not force_scan:
        records = _fallback_scan_records_from_mapping(watchlist, mapping)
        return records, {"cache_state": "LOCAL_FALLBACK", "records": records, "generated_at": ""}
    provider = CachedBinancePriceProvider(BinanceHTTPPriceProvider(), ttl_seconds=60)
    records = scan_binance_equity_mapped_symbols(
        provider=provider,
        cache=CacheReadModel(),
        watchlist=watchlist,
        position_symbols=_portfolio_symbols(),
        manual_mapping=mapping,
        force_refresh=True,
    )
    payload = write_binance_equity_scan_cache(records)
    scan_mapping = scan_records_to_mapping(records, mapping)
    if records:
        _write_scan_mapping_local_file(scan_mapping)
    summary = _scan_sync_summary(records, mapping)
    if records:
        st.success(
            "已从 Binance 官方合约信息识别 "
            f"{summary['total']} 个美股 / TradFi 映射，新增 {summary['added']} 个，"
            f"更新 {summary['updated']} 个，需复核 {summary['review']} 个。"
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
    review = 0
    for record in records:
        ticker = str(record.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        quality = str(record.get("mapping_quality") or "")
        if quality in {MAPPING_REVIEW, MAPPING_INVALID}:
            review += 1
        old = previous.get(ticker)
        if old is None:
            added += 1
            continue
        old_symbol = str(old.get("binance_symbol") or "").strip().upper()
        new_symbol = str(record.get("binance_symbol") or "").strip().upper()
        old_status = str(old.get("mapping_status") or old.get("mapping_confidence") or "")
        if old_symbol != new_symbol or old_status != quality:
            updated += 1
    return {"total": total, "added": added, "updated": updated, "review": review}


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
        elif quality not in {MAPPING_AUTO_USABLE, MAPPING_PRICE_UNVERIFIED, MAPPING_REVIEW, MAPPING_INVALID}:
            quality = MAPPING_AUTO_USABLE if confidence == "auto_available" else MAPPING_PRICE_UNVERIFIED
        records.append(
            {
                "ticker": str(ticker or "").strip().upper(),
                "binance_symbol": symbol,
                "market_type": "usdm_futures",
                "detected_by": "local_mapping",
                "underlying_type": (config or {}).get("underlying_type", ""),
                "underlying_sub_type": (config or {}).get("underlying_sub_type", ""),
                "binance_category": (config or {}).get("binance_category", ""),
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
    options = ["异常偏离", "全部 Binance 美股映射", "我的观察池", "我的持仓", "核心仓", "锚点缺失", "映射待核"]
    review_labels = {"异常复核", "需确认", "无效映射", "无映射"}
    counts = {
        "全部 Binance 美股映射": len([row for row in rows if row.get("binance_symbol")]),
        "我的观察池": len([row for row in rows if row.get("is_watchlist")]),
        "我的持仓": len([row for row in rows if row.get("is_position")]),
        "核心仓": len([row for row in rows if row.get("is_core") or row.get("is_core_position")]),
        "异常偏离": len([row for row in rows if _realtime_row_status_key(row) in {"focus", "review"}]),
        "锚点缺失": len([row for row in rows if row.get("binance_symbol") and _number(row.get("afterhours_reference_price")) is None]),
        "映射待核": len([row for row in rows if _mapping_display_label_for_row(row) in review_labels]),
    }
    labels = [f"{option} {counts.get(option, 0)}" for option in options]
    selected = st.radio("筛选", labels, horizontal=True, label_visibility="collapsed", key="weekend_spread_realtime_filter_scope")
    return options[labels.index(selected)]


def _filter_live_rows_by_scope(rows: list[dict], scope: str) -> list[dict]:
    if scope == "全部 Binance 美股映射":
        selected = [row for row in rows if row.get("binance_symbol")]
    elif scope == "我的观察池":
        selected = [row for row in rows if row.get("is_watchlist")]
    elif scope == "我的持仓":
        selected = [row for row in rows if row.get("is_position")]
    elif scope == "核心仓":
        selected = [row for row in rows if row.get("is_core") or row.get("is_core_position")]
    elif scope == "锚点缺失":
        selected = [row for row in rows if row.get("binance_symbol") and _number(row.get("afterhours_reference_price")) is None]
    elif scope == "映射待核":
        selected = [row for row in rows if _mapping_display_label_for_row(row) in {"异常复核", "需确认", "无效映射", "无映射"}]
    else:
        selected = [row for row in rows if _realtime_row_status_key(row) in {"focus", "review"}]
    return sorted(selected, key=_realtime_sort_key)


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
            item["afterhours_reference_price"] = None
            item["afterhours_reference_time"] = ""
            item["afterhours_reference_source"] = ""
            item["afterhours_data_quality"] = "MISSING"
            item["afterhours_cache_status"] = "CACHE_DATE_MISMATCH"
            item["afterhours_anchor_status"] = ""
            item["afterhours_missing_reason"] = "CACHE_DATE_MISMATCH"
            item["spread_vs_afterhours_pct"] = None
            item["primary_spread_pct"] = item.get("spread_vs_regular_close_pct")
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
    force_anchor_refresh = bool(options.get("force_anchor_refresh"))
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
        return (
            annotate_cached_rows(
                cached_rows,
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
            afterhours_provider=default_afterhours_provider(),
            force_refresh=False,
            afterhours_force_refresh=force_anchor_refresh,
            progress_callback=update_anchor_progress,
        )
        generated_at = datetime.now(timezone.utc).isoformat()
        progress_bar.progress(1.0)
        if has_successful_price(rows):
            write_weekend_spread_snapshot(rows, mapping=mapping, tickers=watchlist, generated_at=datetime.now(timezone.utc))
        live_rows = annotate_cached_rows(rows, cache_state="API_LIVE", generated_at=generated_at)
        status_slot.success("盘后锚点更新完成。")
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
        afterhours_provider=default_afterhours_provider() if cached.get("cache_state") == "ANCHOR_DATE_STALE" else CachedAfterhoursProvider(NullAfterhoursProvider()),
        force_refresh=True,
        afterhours_force_refresh=False,
        progress_callback=update_progress,
    )
    ok_count = sum(1 for row in rows if row.get("status") == "OK")
    mapped_count = sum(1 for row in rows if row.get("binance_symbol"))
    generated_at = datetime.now(timezone.utc).isoformat()
    progress_bar.progress(1.0)
    if has_successful_price(rows):
        write_weekend_spread_snapshot(rows, mapping=mapping, tickers=watchlist, generated_at=datetime.now(timezone.utc))
        live_rows = annotate_cached_rows(rows, cache_state="API_LIVE", generated_at=generated_at)
        status_slot.success(f"刷新完成：{ok_count}/{mapped_count} 个映射有可用价格，共 {len(rows)} 行。")
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
        stale_cached_rows = (
            _mask_stale_afterhours_rows(cached_rows, expected_anchor_date=expected_anchor_date)
            if cached.get("cache_state") == "ANCHOR_DATE_STALE"
            else cached_rows
        )
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
    status_slot.warning(f"刷新完成：{ok_count}/{mapped_count} 个映射有可用价格，共 {len(rows)} 行。")
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
    afterhours_counts = _afterhours_counts(rows)
    status_counts = _realtime_status_counts(rows)
    price_available = sum(
        1
        for row in rows
        if row.get("binance_symbol") and _number(row.get("adjusted_binance_price") or row.get("binance_last_price")) is not None
    )
    items = [
        ("扫描候选", str(mapping_counts.get("scan_record_count") or mapping_counts.get("universe_total") or 0)),
        ("可识别映射", str(mapping_counts.get("identified_equity_count") or mapping_counts.get("universe_mapping_count") or 0)),
        ("价格可用", str(price_available)),
        ("异常偏离", str(status_counts["review"] + status_counts["focus"])),
        ("锚点缺失", str(afterhours_counts["missing"])),
        ("最近更新", _latest_updated_at(rows) or _cache_generated_text(cache_status)),
    ]
    text = " ｜ ".join(f"{label}：{value}" for label, value in items)
    st.markdown(f'<div class="weekend-status-strip">{escape(text)}</div>', unsafe_allow_html=True)


def _render_largest_deviation(rows: list[dict], mapping_counts: dict[str, int]) -> None:
    row = _strongest_signal_row(rows)
    if row is None:
        if mapping_counts.get("universe_mapping_count", 0) <= 0:
            st.info("尚未同步 Binance 美股映射。点击“一键同步 Binance 美股映射”后再观察。")
        else:
            st.info("当前没有可展示的价差偏离。")
        return

    status_key = _realtime_row_status_key(row)
    heading = "当前最大异常偏离" if status_key == "review" else "当前最大偏离"
    spread = _afterhours_spread_text(row.get("spread_vs_afterhours_pct") if row.get("spread_vs_afterhours_pct") is not None else row.get("spread_pct"))
    status_label = _realtime_row_status_label(row)
    reason = _realtime_row_status_reason(row)
    tags = [
        f"映射：{_mapping_display_label_for_row(row)}",
        f"锚点：{_anchor_display_label_for_row(row)}",
        f"数据：{status_label}",
    ]
    st.markdown(
        f"""
        <section class="zhx-card">
          <span class="zhx-eyebrow">{escape(heading)}</span>
          <h3>{escape(str(row.get("ticker") or ""))} &nbsp; {escape(spread)}</h3>
          <p>Binance 相对盘后锚点</p>
          <p>{escape(" ｜ ".join(tags))}</p>
          <p>{escape(reason)}</p>
        </section>
        """,
        unsafe_allow_html=True,
    )


def _render_strongest_signal(rows: list[dict], mapping_counts: dict[str, int]) -> None:
    _render_largest_deviation(rows, mapping_counts)


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


def _realtime_status_counts(rows: list[dict]) -> dict[str, int]:
    counts = {"normal": 0, "focus": 0, "review": 0, "unavailable": 0}
    for row in rows:
        key = _realtime_row_status_key(row)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _realtime_sort_key(row: dict) -> tuple[int, float, str]:
    priority = {"review": 0, "focus": 1, "normal": 2, "unavailable": 3}
    key = _realtime_row_status_key(row)
    spread = _number(row.get("spread_vs_afterhours_pct"))
    spread_abs = abs(spread) if spread is not None else -1.0
    return (priority.get(key, 9), -spread_abs, str(row.get("ticker") or ""))


def _realtime_row_status_key(row: dict) -> str:
    status = str(row.get("status") or "").upper()
    if status in {"NO_MAPPING", "BINANCE_UNAVAILABLE", "INVALID_SYMBOL", "PRICE_NOT_LOADED"}:
        return "unavailable"
    if status == "UNIT_UNCONFIRMED":
        return "review"
    if _mapping_display_label_for_row(row) in {"异常复核", "需确认", "无效映射", "无映射"}:
        return "review"
    spread = _number(row.get("spread_vs_afterhours_pct"))
    if spread is None:
        return "unavailable"
    if abs(spread) >= 8:
        return "review"
    if abs(spread) >= 2:
        return "focus"
    return "normal"


def _realtime_row_status_label(row: dict) -> str:
    return {
        "normal": "正常",
        "focus": "重点关注",
        "review": "异常复核",
        "unavailable": "不可用",
    }.get(_realtime_row_status_key(row), "不可用")


def _realtime_row_status_reason(row: dict) -> str:
    key = _realtime_row_status_key(row)
    if key == "review":
        return "偏离较大或映射、锚点质量不足，需要复核后再参考。"
    if key == "focus":
        return "偏离超过观察阈值，数据质量可用时可重点关注。"
    if key == "unavailable":
        return "缺少 Binance 价格、映射或盘后锚点，暂不可用于实时价差观察。"
    return "映射、价格与盘后锚点可用，当前偏离不大。"


def _mapping_display_label_for_row(row: dict) -> str:
    status = str(row.get("status") or "").upper()
    if status == "NO_MAPPING" or not str(row.get("binance_symbol") or "").strip():
        return "无映射"
    if status in {"INVALID_SYMBOL", "UNIT_UNCONFIRMED"}:
        return "异常复核"
    quality = str(row.get("mapping_quality") or row.get("mapping_status") or "").strip()
    if quality in {MAPPING_MANUAL_LOCKED, "人工锁定"}:
        return "人工锁定"
    if quality in {MAPPING_AUTO_USABLE, "自动可用"}:
        return "自动可用"
    if quality in {MAPPING_PRICE_UNVERIFIED, "自动可用，价格校验不足", MAPPING_ANCHOR_MISSING, "锚点缺失"}:
        return "自动可用，价格校验不足"
    if quality in {MAPPING_REVIEW, "异常复核", "需确认"}:
        return "异常复核"
    if quality in {MAPPING_INVALID, "无效映射"}:
        return "无效映射"
    confidence = str(row.get("mapping_confidence") or row.get("mapping_status") or "").strip().lower()
    if confidence == "confirmed" or confidence == "人工锁定":
        return "人工锁定"
    if confidence == "auto_available":
        return "自动可用"
    return "自动可用"


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
    st.caption("正式回测路径：本周最后交易日盘后收盘价 → Binance 周末合约最高价 → 下周第一个交易日美股夜盘首分钟收盘价。系统统计 Binance 周末冲高幅度、夜盘首分钟兑现程度和最终传导涨幅。")




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
    options = all_tickers
    opening_anchor = "overnight"
    open_window = 2
    selected_default = str(st.session_state.get("weekend_spread_backtest_ticker") or options[0]).strip().upper()
    selected = selected_default if selected_default in options else options[0]
    if st.session_state.get("weekend_spread_backtest_ticker") not in options:
        st.session_state["weekend_spread_backtest_ticker"] = selected
    weeks = _safe_backtest_weeks(st.session_state.get("weekend_spread_backtest_weeks"))
    include_unconfirmed = bool(st.session_state.get("weekend_spread_backtest_include_unconfirmed") or False)
    anchors = _backtest_anchor_mapping([selected], weeks=weeks)
    preflight = build_weekend_backtest_preflight(
        [selected],
        mapping=effective_mapping,
        anchors=anchors,
        include_unconfirmed=include_unconfirmed,
        ticker_filter=selected,
    )
    preliminary = build_weekend_backtest_preflight(
        all_tickers,
        mapping=effective_mapping,
        anchors=_backtest_anchor_mapping(all_tickers, weeks=weeks),
        include_unconfirmed=include_unconfirmed,
    )
    run_clicked = False
    clear_clicked = False

    with st.expander("回测设置", expanded=False):
        include_unconfirmed = st.checkbox(
            "包含未确认映射",
            value=include_unconfirmed,
            key="weekend_spread_backtest_include_unconfirmed",
            help="未确认映射只用于观察，不计入正式统计。",
        )
        cols = st.columns(4)
        selected = cols[0].selectbox("标的", options, key="weekend_spread_backtest_ticker")
        weeks = int(cols[1].number_input("回测周数", min_value=1, max_value=12, value=weeks, step=1, key="weekend_spread_backtest_weeks"))
        cols[2].markdown("**夜盘窗口**  \\n20:00-20:02 ET")
        cols[3].markdown("**开盘锚点**  \\n下周第一个交易日夜盘 / 美东 20:00 ET")
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
        afterhours_anchor_note = _historical_afterhours_anchor_summary_text(anchors)
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
            require_exact_broker_open=True,
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
                "include_unconfirmed": include_unconfirmed,
            },
            error_message=error_message,
        )
        st.session_state["weekend_spread_backtest_results"] = results
        st.session_state["weekend_spread_backtest_cache"] = saved
        if error_message:
            status_slot.warning(f"{error_message}\n\n{afterhours_anchor_note}")
        else:
            status_slot.success(f"回测完成：{len(results)} 条结果。{afterhours_anchor_note}")

    cached_result = dict(st.session_state.get("weekend_spread_backtest_cache") or load_backtest_results())
    results = _current_backtest_results(
        st.session_state.get("weekend_spread_backtest_results"),
        cached_result,
        preflight=preflight,
        mapping=effective_mapping,
        include_unconfirmed=include_unconfirmed,
    )
    if not results:
        if cached_result.get("error_message"):
            st.warning(f"上次运行失败：{cached_result.get('error_message')}")
        else:
            st.info(_backtest_empty_prompt(weeks))
        _render_tradingview_backfill_tools()
        _render_backfill_audit_area_v2(watchlist, mapping, anchors)
        _render_backtest_advanced_records()
        return

    last_run_at = str(cached_result.get("last_run_at") or "")
    if last_run_at:
        st.caption(f"上次运行：{_short_hkt_time(last_run_at)}")
    if include_unconfirmed:
        st.caption("观察回测包含未确认映射，结果不计入正式统计。")

    review_rows = _weekend_review_rows(results)
    ok_review_rows = _ok_weekend_review_rows(review_rows)
    display_weeks = _backtest_result_weeks(cached_result, weeks)
    _render_weekend_review_core_card(review_rows, weeks=display_weeks)
    _render_weekend_review_kpis(review_rows)
    st.subheader(_weekend_review_detail_title(display_weeks))
    _render_weekend_review_table(review_rows)
    with st.expander("数据质量 / 排除原因", expanded=False):
        if not ok_review_rows:
            st.info(_weekend_review_empty_reason(review_rows))
        st.dataframe(_weekend_review_diagnostic_frame(_display_weekend_review_rows(review_rows)), width="stretch", hide_index=True)
        st.dataframe(_backtest_diagnostic_frame(results), width="stretch", hide_index=True)
    _render_tradingview_backfill_tools()
    _render_backfill_audit_area_v2(watchlist, mapping, anchors)
    _render_backtest_advanced_records()


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
    return confidence == "auto_available" or status in {MAPPING_AUTO_USABLE, MAPPING_PRICE_UNVERIFIED}


def _render_overnight_provider_self_check(result: dict[str, object]) -> None:
    reason = _clean_self_check_text(result.get("reason"), "未返回原因")
    if result.get("ok"):
        st.success("夜盘数据源可用，已读取首分钟 1m bar。")
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
        ("返回 bar 数量", str(int(result.get("returned_bar_count") or 0))),
        ("第一根 bar 时间", _weekend_review_short_time(result.get("first_bar_time")) or "暂无"),
        (
            "第一根 bar close",
            _money_text(result.get("first_bar_close"))
            if _number(result.get("first_bar_close")) is not None
            else "暂无",
        ),
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
    text = str(value or "").strip().lower()
    return {
        "auto usable": "\u81ea\u52a8\u53ef\u7528",
        "auto_usable": "\u81ea\u52a8\u53ef\u7528",
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
        "candidate": "自动匹配",
        "unverified": "需确认",
        "no_mapping": "无映射",
        "missing": "无映射",
        "invalid": "无效",
    }.get(text, str(value or "未知"))


def _data_quality_text(value: object) -> str:
    text = str(value or "").strip().upper()
    return {
        "OK": "正式样本",
        "MAPPING_MISSING": "映射缺失",
        "STOCK_MISSING": "美股价格缺失",
        "CONTRACT_MISSING": "合约价格缺失",
        "STALE_CACHE": "缓存过期",
        "INVALID_PRICE": "价格无效",
        "DATA_UNAVAILABLE": "数据不可用",
        "UNCONFIRMED_MAPPING": "映射未确认",
        "OBSERVE_ONLY": "仅观察",
        "OBSERVE_ANCHOR_ONLY": "仅观察锚点",
        "NO_AFTERHOURS_CLOSE": "缺少最后交易日盘后价格",
        "REGULAR_CLOSE_FALLBACK": "常规收盘回退",
        "FALLBACK_REGULAR_CLOSE": "常规收盘回退",
        "P0_UNVERIFIED": "P0 待验证",
        "MISSING_OVERNIGHT_FIRST_1M": "缺少夜盘首分钟 1m K线",
        "OVERNIGHT_PROVIDER_MISSING": "美股夜盘数据源未配置",
        "TRADINGVIEW_WEBHOOK_SAMPLE": "TradingView Webhook 样本",
        "TRADINGVIEW_CSV_SAMPLE": "TradingView CSV 样本",
        "MANUAL_BROKER_SAMPLE": "人工券商样本",
        "MANUAL_AFTERHOURS_SAMPLE": "人工盘后样本",
        "ALPACA_BOATS_SAMPLE": "Alpaca BOATS 样本",
        "BOATS_DELAY_PENDING": "BOATS 延迟等待",
        "ALPACA_BOATS_PERMISSION": "Alpaca BOATS 权限不足",
        "MISSING_BOATS_FIRST_1M": "缺少 BOATS 首分钟 1m K线",
        "PROVIDER_ERROR": "数据源错误",
        "DEGRADED": "降级样本",
        "DEGRADED_5M": "5m 降级样本",
        "BINANCE_KLINE_UNAVAILABLE": "Binance K线不可用",
        "NO_BROKER_OVERNIGHT_BAR": "缺少券商夜盘 bar",
        "MISSING_STOCK_FIRST_BAR": "缺少美股夜盘首分钟 bar",
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
        "CANDIDATE_OBSERVATION": "自动可用 / 观察",
    }.get(text, str(value or "未知"))


def _basis_status_text(value: object) -> str:
    text = str(value or "").strip().upper()
    overrides = {
        "ALLOW_SHORT": "允许观察",
        "BLOCK_MAPPING": "映射待处理",
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
        "UNCONFIRMED_EXCLUDED": "未确认映射已排除",
        "SYMBOL_INVALID": "合约无效",
        "BINANCE_KLINE_UNAVAILABLE": "Binance K线不可用",
        "FUTURES_UNAVAILABLE": "USDT-M 合约不可用",
        "NO_PRICE_ANCHOR": "缺少价格锚点",
        "PROVIDER_ERROR": "数据源错误",
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
        quality = str(row.get("data_quality") or row.get("transmission_data_quality") or "").strip().upper()
        raw_error = str(row.get("error_message") or "").strip().upper()
        if quality == "OVERNIGHT_PROVIDER_MISSING":
            reason = "\u7f8e\u80a1\u591c\u76d8\u6570\u636e\u6e90\u672a\u914d\u7f6e"
        elif quality == "NO_AFTERHOURS_CLOSE" or raw_error == "MISSING_FRIDAY_AFTERHOURS_CLOSE":
            reason = "\u7f3a\u5c11\u672c\u5468\u6700\u540e\u4ea4\u6613\u65e5\u76d8\u540e\u6536\u76d8\u4ef7"
        elif quality in {"NO_BROKER_OVERNIGHT_BAR", "MISSING_STOCK_FIRST_BAR", "MISSING_OVERNIGHT_FIRST_1M"}:
            reason = "\u7f3a\u5c11\u7f8e\u80a1\u591c\u76d8\u9996\u5206\u949f 1m K\u7ebf"
        elif quality == "HOLIDAY_OR_NO_SESSION":
            reason = "\u975e\u6b63\u5e38\u4ea4\u6613\u65e5 / \u65e0\u591c\u76d8 session"
        elif quality in {"DATA_UNAVAILABLE", "BINANCE_KLINE_UNAVAILABLE"}:
            reason = "\u7f3a\u5c11 Binance \u5468\u672b 1m K\u7ebf"
        elif quality == "STALE_OR_MISALIGNED":
            reason = "Binance \u6570\u636e\u8fc7\u671f\u6216\u65f6\u95f4\u4e0d\u5bf9"
        elif quality == "INVALID":
            reason = raw_error or "\u6570\u636e\u65e0\u6548"
        else:
            reason = _weekend_review_failure_reason(row, quality)
        grouped[(ticker, reason)] = grouped.get((ticker, reason), 0) + 1
    return "\uff1b".join(
        f"{ticker}\uff1a{reason}\uff0c\u5df2\u6392\u9664 {count} \u4e2a\u6837\u672c"
        for (ticker, reason), count in sorted(grouped.items())
    )


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
    metrics: list[tuple[str, object, str]] = [
        ("样本数", int(summary.get("sample_count") or 0), "number"),
        ("周末冲高%", summary.get("avg_binance_premium_pct"), "percent"),
        ("夜盘相对高点%", summary.get("avg_overnight_vs_binance_pct"), "percent"),
        ("平均兑现率%", summary.get("avg_capture_pct"), "percent"),
        ("最新一周兑现率%", summary.get("latest_week_capture_pct"), "percent"),
    ]
    cols = st.columns(len(metrics))
    if not int(summary.get("sample_count") or 0):
        for col, (label, _, _) in zip(cols, metrics):
            col.metric(label, "暂无")
        st.caption(
            f"正式 {quality_counts['ok']} 条｜观察 {quality_counts['observe']} 条｜"
            f"降级 {quality_counts['degraded']} 条｜排除 {quality_counts['excluded']} 条"
        )
        st.info(_weekend_review_empty_reason(review_rows))
        return
    for col, (label, value, kind) in zip(cols, metrics):
        if kind == "percent":
            col.metric(label, _review_percent_text(value))
        else:
            col.metric(label, value)
    st.caption(
        f"正式 {quality_counts['ok']} 条｜观察 {quality_counts['observe']} 条｜"
        f"降级 {quality_counts['degraded']} 条｜排除 {quality_counts['excluded']} 条"
    )
    if summary.get("summary_quality") == "OBSERVE":
        st.info("当前显示观察样本统计：已读取部分 P0/P2，但不计入正式胜率。")


def _latest_weekend_review_row(review_rows: list[dict]) -> dict | None:
    display_rows = _display_weekend_review_rows(review_rows)
    return display_rows[0] if display_rows else None


def _money_or_missing(value: object, fallback: str) -> str:
    return _money_text(value) if _number(value) is not None else fallback


def _percent_or_missing(value: object, row: dict) -> str:
    if _number(value) is not None:
        return _review_percent_text(value)
    missing: list[str] = []
    if _number(row.get("friday_afterhours_close")) is None:
        missing.append("P0")
    if _number(row.get("broker_open_close")) is None:
        missing.append("P2")
    if missing:
        return "缺 " + " / ".join(missing)
    return "无法计算"


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
    source = _price_source_text(row.get("overnight_provider"))
    if not source or source == "未知":
        return _data_quality_text(row.get("data_quality"))
    return source


def _render_weekend_review_core_card(review_rows: list[dict], *, weeks: int = 4) -> None:
    row = _latest_weekend_review_row(review_rows)
    if not row:
        st.info(f"尚未运行历史回测。展开“回测设置”后点击“{_backtest_run_button_label(weeks)}”。")
        return
    metrics = [
        ("Binance 周末冲高%", _percent_or_missing(row.get("binance_premium_pct"), row)),
        ("夜盘相对 Binance 高点%", _percent_or_missing(row.get("overnight_vs_binance_pct"), row)),
        ("夜盘相对盘后%", _percent_or_missing(row.get("overnight_vs_afterhours_pct"), row)),
        ("周末高点兑现率%", _percent_or_missing(row.get("capture_pct"), row)),
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
          <div class="weekend-core-flow-label">最后交易日盘后 → Binance 周末最高 → 下周首个交易日夜盘首分钟</div>
          <div class="weekend-core-flow">
            {escape(_money_or_missing(row.get("friday_afterhours_close"), "缺 P0"))}
            → {escape(_money_or_missing(row.get("binance_price"), "缺 P1"))}
            → {escape(_money_or_missing(row.get("broker_open_close"), "缺 P2"))}
          </div>
          <div class="weekend-core-metrics">{metric_html}</div>
          <div class="weekend-core-sources">
            P0 来源：{escape(_p0_source_summary(row))}｜
            P1 来源：{escape(_p1_source_summary(row))}｜
            P2 来源：{escape(_p2_source_summary(row))}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if _number(row.get("broker_open_close")) is None:
        st.caption("缺少夜盘首分钟价格，只能观察 Binance 周末冲高。")
    elif str(row.get("data_quality") or "").strip().upper() in {"REGULAR_CLOSE_FALLBACK", "FALLBACK_REGULAR_CLOSE"}:
        st.caption("P0 使用常规收盘回退，仅作为观察样本。")



def _weekend_review_quality_counts(review_rows: list[dict]) -> dict[str, int]:
    counts = {"ok": 0, "observe": 0, "degraded": 0, "excluded": 0}
    for row in review_rows:
        quality = str(row.get("data_quality") or "").strip().upper()
        if quality == "OK" and not bool(row.get("holiday_rollover")):
            counts["ok"] += 1
        elif quality == "OK" and bool(row.get("holiday_rollover")):
            counts["observe"] += 1
        elif quality in {
            "OBSERVE_ONLY",
            "MISSING_OVERNIGHT_FIRST_1M",
            "OVERNIGHT_PROVIDER_MISSING",
            "REGULAR_CLOSE_FALLBACK",
            "FALLBACK_REGULAR_CLOSE",
            "P0_UNVERIFIED",
            "NO_AFTERHOURS_CLOSE",
            "TRADINGVIEW_WEBHOOK_SAMPLE",
            "TRADINGVIEW_CSV_SAMPLE",
            "MANUAL_BROKER_SAMPLE",
            "MANUAL_AFTERHOURS_SAMPLE",
            "ALPACA_BOATS_SAMPLE",
            "BOATS_DELAY_PENDING",
            "ALPACA_BOATS_PERMISSION",
            "MISSING_BOATS_FIRST_1M",
            "PROVIDER_ERROR",
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
        return "\u7f3a\u5c11\u591c\u76d8\u9996\u5206\u949f 1m K\u7ebf\uff0c\u4e0d\u8ba1\u5165\u6b63\u5f0f\u7edf\u8ba1"
    if qualities & {"NO_AFTERHOURS_CLOSE", "MISSING_FRIDAY_AFTERHOURS_CLOSE", "MISSING_P0"}:
        return "\u7f3a\u5c11\u672c\u5468\u6700\u540e\u4ea4\u6613\u65e5\u76d8\u540e\u951a\u70b9"
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
        friday_afterhours_close = _first_number(
            row,
            (
                "last_trading_day_afterhours_close",
                "friday_afterhours_close",
                "afterhours_reference_price",
            ),
        )
        binance_price = _first_number(row, ("binance_equivalent_max", "binance_weekend_max", "binance_weekend_max_price"))
        broker_open_close = _first_number(row, ("overnight_first_1m_close", "broker_first_1m_close"))
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
    ok_rows = _ok_weekend_review_rows(review_rows)
    source_rows = ok_rows
    summary_quality = "OK"
    if not source_rows:
        summary_quality = "NONE"
    latest_weeks = set(_latest_week_ids(source_rows, limit=4))
    scoped = [row for row in source_rows if row.get("week_id") in latest_weeks] if latest_weeks else list(source_rows)
    valid = [
        row
        for row in scoped
        if _number(row.get("binance_premium_pct")) is not None
        and _number(row.get("overnight_vs_binance_pct")) is not None
    ]
    if not valid:
        return {
            "summary_quality": summary_quality,
            "sample_count": 0,
            "avg_binance_premium_pct": None,
            "avg_overnight_vs_binance_pct": None,
            "avg_capture_pct": None,
            "latest_week_capture_pct": None,
        }
    premiums = [float(_number(row.get("binance_premium_pct")) or 0.0) for row in valid]
    overnight_vs_binance = [float(_number(row.get("overnight_vs_binance_pct")) or 0.0) for row in valid]
    captures = [_number(row.get("capture_pct")) for row in valid]
    captures = [float(value) for value in captures if value is not None]
    latest_week = _latest_week_ids(valid, limit=1)
    latest_rows = [row for row in valid if latest_week and row.get("week_id") == latest_week[0]]
    latest_captures = [_number(row.get("capture_pct")) for row in latest_rows]
    latest_captures = [float(value) for value in latest_captures if value is not None]
    return {
        "summary_quality": summary_quality,
        "sample_count": len(valid),
        "avg_binance_premium_pct": sum(premiums) / len(premiums),
        "avg_overnight_vs_binance_pct": sum(overnight_vs_binance) / len(overnight_vs_binance),
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
    display["P0 请求区间"] = frame.get("p0_request_window")
    display["P0 返回bars"] = frame.get("p0_returned_bar_count")
    display["P0 选中时间"] = frame.get("p0_selected_bar_time")
    display["P0 选中close"] = frame.get("p0_selected_bar_close")
    display["P0 volume"] = frame.get("p0_selected_bar_volume")
    display["Binance 合约"] = frame.get("binance_symbol")
    display["Binance 高点时间"] = frame.get("contract_sample_time")
    return display


def _weekend_review_frame(review_rows: list[dict]) -> pd.DataFrame:
    columns = [
        "周次",
        "股票",
        "P0 最后交易日盘后",
        "P1 Binance 高点",
        "P2 夜盘首分钟",
        "周末冲高%",
        "高点回落%",
        "最终传导%",
        "兑现率%",
        "样本状态",
    ]
    if not review_rows:
        return pd.DataFrame(columns=columns)
    records: list[dict] = []
    for row in review_rows:
        records.append(
            {
                "周次": row.get("week_id"),
                "股票": row.get("ticker"),
                "P0 最后交易日盘后": _money_or_missing(row.get("friday_afterhours_close"), "缺 P0"),
                "P1 Binance 高点": _money_or_missing(row.get("binance_price"), "缺 P1"),
                "P2 夜盘首分钟": _money_or_missing(row.get("broker_open_close"), "缺 P2"),
                "周末冲高%": _percent_or_missing(row.get("binance_premium_pct"), row),
                "高点回落%": _percent_or_missing(row.get("overnight_vs_binance_pct"), row),
                "最终传导%": _percent_or_missing(row.get("overnight_vs_afterhours_pct"), row),
                "兑现率%": _percent_or_missing(row.get("capture_pct"), row),
                "样本状态": _data_quality_text(row.get("data_quality")),
            }
        )
    return pd.DataFrame(records, columns=columns)


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

    percent_columns = ["周末冲高%", "高点回落%", "最终传导%", "兑现率%"]
    color_subset = [column for column in percent_columns if column in frame.columns]
    styler = frame.style
    if not color_subset:
        return styler
    if hasattr(styler, "map"):
        return styler.map(color_value, subset=color_subset)
    return styler.applymap(color_value, subset=color_subset)


def _ok_weekend_review_rows(review_rows: list[dict]) -> list[dict]:
    return [
        row
        for row in review_rows
        if str(row.get("data_quality") or "").strip().upper() == "OK"
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
    if quality in {"TRADINGVIEW_WEBHOOK_SAMPLE", "TRADINGVIEW_CSV_SAMPLE", "MANUAL_BROKER_SAMPLE", "MANUAL_AFTERHOURS_SAMPLE"}:
        return quality
    if quality in {"BOATS_DELAY_PENDING", "ALPACA_BOATS_PERMISSION", "MISSING_BOATS_FIRST_1M", "PROVIDER_ERROR"}:
        return quality
    if binance_price is None or binance_price <= 0 or quality in {"BINANCE_KLINE_UNAVAILABLE", "CONTRACT_MISSING", "DATA_UNAVAILABLE"}:
        return "CONTRACT_MISSING"
    if anchor_price is None or anchor_price <= 0 or quality in {"NO_AFTERHOURS_CLOSE", "NO_PRICE_ANCHOR"}:
        return "NO_AFTERHOURS_CLOSE"
    if broker_open_close is None or broker_open_close <= 0 or quality in {"MISSING_OVERNIGHT_FIRST_1M", "MISSING_STOCK_FIRST_BAR", "NO_BROKER_OVERNIGHT_BAR", "HOLIDAY_OR_NO_SESSION"}:
        return "MISSING_OVERNIGHT_FIRST_1M"
    if cache_status in {"STALE", "STALE_CACHE", "CACHE_FALLBACK"} or quality in {"STALE_CACHE", "STALE_OR_MISALIGNED"}:
        return "STALE_CACHE"
    if quality in {"", "OK", "ESTIMATED_EXECUTION"} and anchor_price is not None and anchor_price > 0 and binance_price is not None and binance_price > 0 and broker_open_close is not None and broker_open_close > 0:
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
    if data_quality in {"MISSING_OVERNIGHT_FIRST_1M", "OVERNIGHT_PROVIDER_MISSING", "REGULAR_CLOSE_FALLBACK", "FALLBACK_REGULAR_CLOSE", "P0_UNVERIFIED"}:
        return "仅观察"
    if data_quality in {"BOATS_DELAY_PENDING", "ALPACA_BOATS_PERMISSION", "MISSING_BOATS_FIRST_1M", "PROVIDER_ERROR"}:
        return "仅观察"
    if data_quality in {"TRADINGVIEW_WEBHOOK_SAMPLE", "TRADINGVIEW_CSV_SAMPLE", "MANUAL_BROKER_SAMPLE", "MANUAL_AFTERHOURS_SAMPLE"}:
        return _weekend_review_sample_status(data_quality)
    if str(data_quality or "").startswith("DEGRADED"):
        return "降级样本"
    if data_quality != "OK" or premium_pct is None:
        return "排除"
    return "正式样本"


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
        return _weekend_review_sample_status_with_context("自动正式样本", raw)
    if quality == "ALPACA_BOATS_SAMPLE":
        return "Alpaca BOATS 样本"
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
    if quality in {"MISSING_OVERNIGHT_FIRST_1M", "OVERNIGHT_PROVIDER_MISSING", "REGULAR_CLOSE_FALLBACK", "FALLBACK_REGULAR_CLOSE", "NO_AFTERHOURS_CLOSE", "OBSERVE_ONLY"}:
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
    for key in ("overnight_first_1m_time", "overnight_bar_start_et", "broker_first_1m_time", "broker_bar_start_time", "stock_bar_timestamp", "broker_overnight_open_ts"):
        value = str(row.get(key) or "").strip()
        if value:
            return _weekend_review_short_time(value)
    return ""


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
        return "缺少 BOATS 夜盘首分钟 1m K线"
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
        return str(row.get("overnight_reason") or "缺少夜盘首分钟 1m K线")
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


def _render_backtest_advanced_records() -> None:
    with st.expander("高级 / 前瞻记录", expanded=False):
        st.caption("前瞻记录只作为周末价差观察数据，不会写入交易日志、错题本或信号表现。")


def _render_mapping_tab(rows: list[dict], mapping: dict[str, dict], mapping_counts: dict[str, int]) -> None:
    st.subheader("Binance 美股 / TradFi 映射管理")
    st.caption("系统会自动匹配 Binance 合约。价格可用且偏差正常的映射会自动参与观察；只有价格异常或合约无效时才需要人工处理。")
    records = _mapping_management_records(rows, mapping)
    usable = sum(1 for record in records if record.get("state_group") in {"usable", "locked"})
    review = sum(1 for record in records if record.get("state_group") == "review")
    invalid = sum(1 for record in records if record.get("state_group") in {"invalid", "missing"})
    locked = sum(1 for record in records if record.get("state_group") == "locked")
    watchlist_covered = sum(1 for row in rows if row.get("is_watchlist") and row.get("binance_symbol"))
    cols = st.columns(5)
    cols[0].metric("可用映射", usable)
    cols[1].metric("需处理", review)
    cols[2].metric("无效映射", invalid)
    cols[3].metric("人工锁定", locked)
    cols[4].metric("观察池覆盖数", watchlist_covered)

    show_all = st.toggle("显示全部映射", value=False, key="weekend_spread_mapping_show_all")
    display_records = records if show_all else [
        record for record in records if record.get("state_group") in {"review", "invalid", "missing"}
    ]
    if display_records:
        st.dataframe(_mapping_management_frame(display_records), width="stretch", hide_index=True)
    elif records:
        st.success("当前没有需要处理的异常映射。打开“显示全部映射”可以查看自动可用项目。")
    else:
        st.info("当前还没有 Binance 美股映射。请先在实时观察页点击“一键同步 Binance 美股映射”。")

    _render_mapping_editor(mapping, rows, mapping_counts, DEFAULT_LOCAL_MAPPING_PATH)
    _render_mapping_diagnostics(mapping)


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
    elif display_label in {"异常复核", "需确认"}:
        group = "review"
        label = "异常复核"
    elif display_label == "无效映射":
        group = "invalid"
        label = "无效映射"
    elif price_diff_pct is not None and abs(price_diff_pct) > 30:
        group = "review"
        label = "异常复核"
    else:
        group = "usable"
        label = display_label if display_label.startswith("自动可用") else "自动可用"
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
        "risk_note": str(config.get("risk_note") or row.get("mapping_risk") or ""),
    }


def _mapping_display_label_for_record(record: dict) -> str:
    return str(record.get("state_label") or {
        "usable": "自动可用",
        "locked": "人工锁定",
        "review": "异常复核",
        "invalid": "无效映射",
        "missing": "无映射",
    }.get(str(record.get("state_group") or ""), "异常复核"))


def _mapping_management_records(rows: list[dict], mapping: dict[str, dict]) -> list[dict]:
    return [_mapping_record_from_row(row, mapping.get(str(row.get("ticker") or "").upper(), {})) for row in rows]


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
    counts.update(
        {
            "usable_count": sum(1 for record in records if record.get("state_group") in {"usable", "locked"}),
            "review_count": sum(1 for record in records if record.get("state_group") in {"review", "invalid"}),
            "manual_locked_count": sum(1 for record in records if record.get("state_group") == "locked"),
            "invalid_count": sum(1 for record in records if record.get("state_group") == "invalid"),
            "no_mapping_count": sum(1 for record in records if record.get("state_group") == "missing"),
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


def _render_mapping_editor(
    mapping: dict[str, dict],
    rows: list[dict],
    mapping_counts: dict[str, int],
    local_mapping_path: Path,
) -> None:
    expanded = mapping_counts.get("usable_count", 0) <= 0 and mapping_counts.get("review_count", 0) > 0
    with st.expander("映射操作", expanded=expanded):
        st.caption("自动可用映射已经可以参与观察；人工锁定只是把本模块的本地 mapping 固定下来，不会修改主系统观察池或持仓。")
        auto_rows = [row for row in rows if _mapping_display_label_for_row(row).startswith("自动可用")]
        if auto_rows and st.button("一键采用全部自动可用映射", key="weekend_spread_lock_auto_mappings", width="stretch"):
            changed = 0
            for row in auto_rows:
                ticker = str(row.get("ticker") or "").strip().upper()
                symbol = str(row.get("binance_symbol") or "").strip().upper()
                if not ticker or not symbol:
                    continue
                upsert_local_binance_symbol_mapping(
                    ticker,
                    symbol,
                    market_type=str(row.get("market_type") or "usdm_futures"),
                    mapping_confidence="confirmed",
                    path=local_mapping_path,
                )
                changed += 1
            st.success(f"已人工锁定 {changed} 条映射")
        st.caption(f"仅写入周末价差本地 mapping：{local_mapping_path.as_posix()}")


def _mapping_editor_error_text(error_code: str) -> str:
    return {
        "ticker_required": "请填写股票代码",
        "binance_symbol_required": "请填写 Binance 合约，例如 NVDAUSDT",
    }.get(error_code, "映射保存失败，请检查输入")


def _live_frame(rows: list[dict]) -> pd.DataFrame:
    columns = [
        "股票",
        "美股盘后锚点",
        "Binance 最新",
        "相对盘后",
        "相对收盘",
        "状态",
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
    display["更新时间"] = frame.get("updated_at").map(_short_hkt_time)
    return display[columns]


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
    columns = ["股票", "Binance 合约", "Binance 分类", "Binance 最新价", "股票参考价", "价格差异%", "映射状态", "操作"]
    if not records:
        return pd.DataFrame(columns=columns)
    table_rows: list[dict] = []
    for record in records:
        table_rows.append(
            {
                "股票": str(record.get("ticker") or "").upper(),
                "Binance 合约": str(record.get("binance_symbol") or "未配置"),
                "Binance 分类": _binance_category_text(record),
                "Binance 最新价": _money_text(record.get("binance_price")),
                "股票参考价": _money_text(record.get("stock_ref_price")),
                "价格差异%": _percent_text(record.get("price_diff_pct")),
                "映射状态": str(record.get("state_label") or _mapping_display_label_for_record(record)),
                "操作": _mapping_action_hint(record),
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


def _mapping_action_hint(record: dict) -> str:
    group = str(record.get("state_group") or "")
    if group == "usable":
        return "可直接观察；可选人工锁定"
    if group == "locked":
        return "已人工锁定"
    if group == "invalid":
        return "检查合约或忽略"
    if group == "off_watchlist":
        return "不在当前筛选范围"
    if group == "missing":
        return "补充 Binance 合约"
    return "采用 / 忽略 / 修改"


def _render_row_details(rows: list[dict]) -> None:
    if not rows:
        return
    for row in rows:
        ticker = str(row.get("ticker") or "").upper()
        with st.expander(f"{ticker} 详情", expanded=False):
            col_anchor, col_binance, col_note = st.columns(3)
            with col_anchor:
                st.markdown("**美股锚点**")
                st.caption(f"盘后锚点：{_money_text(row.get('afterhours_reference_price'))}")
                st.caption(f"常规收盘：{_money_text(row.get('regular_close_price') or row.get('friday_close'))}")
                st.caption(f"相对盘后：{_afterhours_spread_text(row.get('spread_vs_afterhours_pct'))}")
                st.caption(f"相对收盘：{_percent_text(row.get('spread_vs_regular_close_pct'))}")
                st.caption(f"来源：{_afterhours_source_text(row.get('afterhours_reference_source'))}")
                st.caption(f"时间：{_short_hkt_time(row.get('afterhours_reference_time'))}")
                st.caption(f"锚点状态：{_anchor_display_label_for_row(row)}")
                missing_reason = _afterhours_reason_text(row.get('afterhours_missing_reason'))
                if missing_reason:
                    st.caption(f"原因：{missing_reason}")
            with col_binance:
                st.markdown("**Binance 合约**")
                st.caption(f"Binance symbol：{str(row.get('binance_symbol') or '未配置')}")
                st.caption(f"最新价：{_money_text(row.get('binance_last_price'))}")
                st.caption(f"更新：{_short_hkt_time(row.get('updated_at'))}")
                st.caption(f"bid：{_money_text(row.get('binance_bid'))}")
                st.caption(f"ask：{_money_text(row.get('binance_ask'))}")
                st.caption(f"bid-ask spread：{_percent_text(row.get('binance_spread_pct'))}")
                st.caption(f"24h volume：{_plain_number(row.get('binance_volume_24h'))}")
                st.caption(f"funding：{_funding_text(row.get('funding_rate'))}")
            with col_note:
                st.markdown("**数据状态**")
                st.caption(f"映射：{_mapping_display_label_for_row(row)}")
                st.caption(f"范围：{_row_membership_text(row)}")
                detected_by = _scan_detected_by_text(row.get("scan_detected_by"))
                if detected_by:
                    st.caption(f"识别来源：{detected_by}")
                quality_reason = str(row.get("mapping_quality_reason") or "").strip()
                if quality_reason:
                    st.caption(f"质量说明：{quality_reason}")
                st.caption(f"状态：{_realtime_row_status_label(row)}")
                st.caption(f"原因：{_realtime_row_status_reason(row)}")
                error = str(row.get("error") or "").strip()
                if error:
                    st.caption(f"错误：{_localized_realtime_error(error)}")


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
        "candidate": "自动匹配",
        "unverified": "需确认",
        "verified_ready": "需确认",
        "stale": "需确认",
        "rejected": "已忽略",
        "auto_available": "自动可用",
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
    priced = [row for row in rows if _number(row.get("spread_vs_afterhours_pct")) is not None]
    if not priced:
        priced = [row for row in rows if _number(row.get("spread_pct")) is not None]
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
