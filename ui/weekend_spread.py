from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from data.equity_afterhours_provider import CachedAfterhoursProvider, NullAfterhoursProvider, default_afterhours_provider
from data.binance_provider import DEFAULT_BINANCE_CACHE_PATH, normalize_market_type
from data.cache_read_model import CacheReadModel
from data.weekend_basis import (
    build_basis_opportunity,
    close_weekend_basis_trade,
    create_weekend_basis_trade,
    load_weekend_basis_trades,
    record_broker_hedge,
    upsert_weekend_basis_trade,
)
from data.weekend_basis_mapping_audit import (
    audit_weekend_basis_mappings,
    confirm_weekend_basis_mapping,
    reject_weekend_basis_mapping,
)
from data.weekend_spread_backtest import (
    build_weekend_backtest_preflight,
    clear_backtest_view_state,
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


def _weekend_scope_tickers(watchlist: list[str]) -> list[str]:
    tickers: list[str] = []
    seen: set[str] = set()
    for item in watchlist or []:
        ticker = str(item or "").strip().upper()
        if not ticker or ticker in seen:
            continue
        tickers.append(ticker)
        seen.add(ticker)
    return tickers


def _render_realtime_tab(
    watchlist: list[str],
    mapping: dict[str, dict],
) -> tuple[list[dict], dict[str, int]]:
    st.subheader("实时观察")
    st.caption("观察当前 Binance 合约价格相对美股盘后锚点的偏离，不展示后台调试字段。")
    status_slot = st.empty()
    deviation_slot = st.empty()
    action_slot = st.empty()
    table_slot = st.empty()
    advanced_slot = st.empty()
    with action_slot.container():
        refresh_options = _render_realtime_action_bar()
    rows, cache_status = _build_weekend_spread_rows_with_feedback(watchlist, mapping=mapping, refresh_options=refresh_options)
    st.session_state["weekend_realtime_rows"] = rows
    st.session_state["weekend_realtime_cache_status"] = cache_status

    mapping_counts = _mapping_counts(rows, mapping)

    with status_slot.container():
        _render_realtime_status_strip(rows, mapping_counts, cache_status)
    with deviation_slot.container():
        _render_largest_deviation(rows, mapping_counts)

    main_rows = _default_live_rows(rows)
    with table_slot.container():
        st.markdown("#### 实时价差表")
        if _should_show_empty_mapping_state(mapping_counts, "重点/有数据"):
            _render_empty_mapping_state(mapping_counts, DEFAULT_LOCAL_MAPPING_PATH)
        elif main_rows:
            st.dataframe(_live_frame(main_rows), width="stretch", hide_index=True)
            _render_row_details(main_rows)
        else:
            st.info("当前没有可展示的实时价差。若已有映射，请刷新实时观察或查看映射状态。")

    with advanced_slot.container():
        with st.expander("高级设置 / 缓存管理", expanded=False):
            _render_paper_trade_area(rows, mapping)
            _render_no_mapping_expander(rows)
    return rows, mapping_counts


def _render_realtime_action_bar() -> dict[str, bool]:
    col_refresh, col_anchor = st.columns([1, 1])
    refresh = col_refresh.button("刷新实时观察", width="stretch", type="primary", key="weekend_spread_refresh")
    anchor_refresh = col_anchor.button("更新盘后锚点", width="stretch", key="weekend_spread_anchor_refresh")
    use_cache = False
    force_anchor = False
    with st.expander("数据源与补数工具", expanded=False):
        col_cache, col_force_anchor = st.columns([1, 1])
        use_cache = col_cache.button("使用缓存", width="stretch", key="weekend_spread_use_cache")
        force_anchor = col_force_anchor.button("强制重建锚点", width="stretch", key="weekend_spread_force_anchor_refresh")
        st.caption("Binance 价格和最后交易日盘后锚点已解耦：刷新实时观察不会强制重建盘后锚点。")
    return {
        "use_cache": bool(use_cache),
        "refresh": bool(refresh),
        "anchor_refresh": bool(anchor_refresh),
        "force_anchor_refresh": bool(force_anchor),
    }


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
    cached = read_weekend_spread_snapshot(mapping=mapping, tickers=watchlist)
    if not force_refresh and not anchor_refresh and cached.get("rows"):
        return (
            annotate_cached_rows(
                list(cached.get("rows") or []),
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
        status_slot.caption(f"Updating last trading day afterhours anchors: {total} symbols.")

        def update_anchor_progress(completed: int, total_count: int, ticker: str) -> None:
            ratio = completed / max(total_count, 1)
            progress_bar.progress(min(max(ratio, 0.0), 1.0))
            status_slot.caption(f"Updating afterhours anchor: {ticker} ({completed}/{total_count})")

        rows = build_weekend_spread_rows(
            watchlist,
            mapping=mapping,
            provider=_CachedRowBinanceProvider(list(cached.get("rows") or [])),
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
        status_slot.success("Afterhours anchor update complete.")
        return live_rows, {
            "cache_state": "API_LIVE",
            "cache_message": "afterhours anchors updated",
            "rows": live_rows,
            "generated_at": generated_at,
            "last_failure": {},
        }
    total = len([ticker for ticker in watchlist if str(ticker or "").strip()])
    if total <= 0:
        st.info("No watchlist symbols available for Binance refresh.")
        return [], {"cache_state": "MISSING", "cache_message": "empty watchlist", "rows": []}

    progress_bar = st.progress(0.0)
    status_slot = st.empty()
    status_slot.caption(f"Preparing Binance refresh: {total} symbols.")

    def update_progress(completed: int, total_count: int, ticker: str) -> None:
        ratio = completed / max(total_count, 1)
        progress_bar.progress(min(max(ratio, 0.0), 1.0))
        status_slot.caption(f"Refreshing Binance data: {ticker} ({completed}/{total_count})")

    rows = build_weekend_spread_rows(
        watchlist,
        mapping=mapping,
        afterhours_provider=CachedAfterhoursProvider(NullAfterhoursProvider()),
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
        status_slot.success(f"Refresh complete: {ok_count}/{mapped_count} mapped prices available, {len(rows)} rows.")
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
        fallback_rows = annotate_cached_rows(
            list(cached.get("rows") or []),
            cache_state="REFRESH_FAILED",
            generated_at=str(cached.get("generated_at") or ""),
        )
        status_slot.warning("刷新失败，使用上次成功缓存。")
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
            status_slot.warning("刷新失败，使用上次可用 Binance 价格缓存。")
            return fallback_rows, {
                "cache_state": "REFRESH_FAILED",
                "cache_message": error_message,
                "rows": fallback_rows,
                "generated_at": "",
                "last_failure": {"error_message": error_message},
            }
    status_slot.warning(f"Refresh complete: {ok_count}/{mapped_count} mapped prices available.")
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
    cols[0].metric("当前可拉价", mapping_counts["price_row_count"])
    cols[1].metric("实时异常价差", abnormal_count)
    cols[2].metric("Binance 数据状态", _binance_status_text(rows, mapping_counts["universe_mapping_count"]))


def _render_data_status_cards(rows: list[dict], mapping_counts: dict[str, int], local_mapping_path: Path, cache_status: dict | None = None) -> None:
    afterhours_counts = _afterhours_counts(rows)
    values = [
        ("观察池映射数", f"{mapping_counts['universe_mapping_count']} / {mapping_counts['universe_total']}"),
        ("USDT-M 合约价格源", _market_price_source_status(rows, "usdm_futures")),
        ("盘后锚点", _afterhours_anchor_status_text(rows, afterhours_counts)),
        ("最后刷新", _latest_updated_at(rows) or "暂缺"),
        ("缓存时间", _cache_generated_text(cache_status)),
        ("缓存状态", _cache_state_text(cache_status)),
    ]
    cols = st.columns(len(values))
    for col, (label, value) in zip(cols, values):
        col.caption(label)
        col.write(value)
    off_universe_note = _off_universe_mapping_note(mapping_counts)
    if local_mapping_path.exists() or mapping_counts["local_mapping_count"] > 0:
        st.caption(f"{off_universe_note}；local 配置：{local_mapping_path.as_posix()}")
    else:
        st.caption(f"{off_universe_note}；local 配置尚未创建。")


def _render_realtime_status_strip(rows: list[dict], mapping_counts: dict[str, int], cache_status: dict | None = None) -> None:
    afterhours_counts = _afterhours_counts(rows)
    status_counts = _realtime_status_counts(rows)
    items = [
        ("可观察标的", f"{mapping_counts['price_row_count']} / {mapping_counts['universe_total']}"),
        ("异常偏离", str(status_counts["review"] + status_counts["focus"])),
        ("Binance 数据", _binance_status_text(rows, mapping_counts["universe_mapping_count"])),
        ("盘后锚点", f"{afterhours_counts['available']} / {afterhours_counts['mapped']} 已缓存"),
        ("最近更新", _latest_updated_at(rows) or _cache_generated_text(cache_status)),
    ]
    text = " ｜ ".join(f"{label}：{value}" for label, value in items)
    st.markdown(f'<div class="weekend-status-strip">{escape(text)}</div>', unsafe_allow_html=True)


def _render_largest_deviation(rows: list[dict], mapping_counts: dict[str, int]) -> None:
    row = _strongest_signal_row(rows)
    if row is None:
        if mapping_counts.get("universe_mapping_count", 0) <= 0:
            st.info("当前没有可拉取 Binance 价格的映射。先配置股票代码到 Binance 合约代码的映射后，系统会自动读取价格。")
        else:
            st.info("暂无实时价差。若映射存在但没有价格，请刷新实时观察或查看映射状态。")
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
    row = _strongest_signal_row(rows)
    if row is None:
        if mapping_counts.get("universe_mapping_count", 0) <= 0:
            st.info("当前没有可拉取 Binance 价格的映射。先配置股票代码到 Binance 合约代码的映射后，系统会自动读取价格。")
        else:
            st.info("暂无实时价差信号。若映射存在但没有价格，请查看映射状态或刷新 Binance 价格。")
        return

    spread = _percent_text(row.get("spread_pct"))
    risk = _primary_risk_text(row)
    st.markdown(
        f"""
        <section class="zhx-card">
          <span class="zhx-eyebrow">当前最大偏离</span>
          <h3>{escape(str(row.get("ticker") or ""))} · {escape(spread)}</h3>
          <p>{escape(str(row.get("spread_direction") or ""))}｜{escape(str(row.get("alert_level_cn") or ""))}｜映射：{escape(_mapping_display_label_for_row(row))}</p>
          <p>{escape(risk)}</p>
        </section>
        """,
        unsafe_allow_html=True,
    )
    warning = _strongest_signal_warning(row)
    if warning:
        st.warning(warning)


def _render_weekly_tab(rows: list[dict], log_snapshot: dict) -> None:
    st.subheader("本周记录")
    _render_record_buttons(rows, key_prefix="weekly")
    log_snapshot = get_weekly_log_snapshot()
    _render_weekly_peak_cards(log_snapshot)
    summaries = list(log_snapshot.get("summaries") or [])
    if summaries:
        st.dataframe(_summary_frame(summaries), width="stretch", hide_index=True)
    else:
        st.info("本周还没有已生成的 summary。先记录当前快照，再生成本周总结。")


def _render_record_buttons(rows: list[dict], *, key_prefix: str) -> None:
    cols = st.columns(2)
    if cols[0].button("记录当前快照", width="stretch", key=f"weekend_record_{key_prefix}"):
        samples = record_spread_samples(rows)
        st.success(f"已记录 {len(samples)} 条有映射快照。")
    if cols[1].button("生成本周总结", width="stretch", key=f"weekend_summary_{key_prefix}"):
        summaries = generate_weekly_summary()
        st.success(f"已生成 {len(summaries)} 条本周总结。")


def _render_weekly_peak_cards(log_snapshot: dict) -> None:
    summaries = list(log_snapshot.get("summaries") or [])
    max_abs = _recorded_max_abs_spread(log_snapshot)
    sample_count = int(log_snapshot.get("sample_count") or 0)
    cols = st.columns(4)
    cols[0].metric("已记录最大溢价", _percent_text(log_snapshot.get("max_premium_pct")))
    cols[1].metric("已记录最大折价", _percent_text(log_snapshot.get("max_discount_pct")))
    cols[2].metric("最大绝对价差", _percent_text(max_abs))
    cols[3].metric("sample_count", sample_count)
    if not summaries:
        st.caption("已记录最大溢价/折价来自样本快照；summary 表需要点击“生成本周总结”。")


def _render_monday_tab(log_snapshot: dict) -> None:
    st.subheader("下周首个交易日验证")
    summaries = list(log_snapshot.get("summaries") or [])
    if not summaries:
        st.info("暂无可验证的本周总结。先在“本周记录”里生成 summary。")
        return

    st.caption("这里记录的是信号验证结果，不是套利成功或交易建议。")
    labels = [f"{item.get('ticker')} | {item.get('week_id')}" for item in summaries]
    selected = st.selectbox("选择验证标的", labels, key="weekend_monday_target")
    selected_summary = summaries[labels.index(selected)]
    cols = st.columns(4)
    reference_type = cols[0].selectbox(
        "monday_reference_type",
        ["MONDAY_PREMARKET_OPEN", "MONDAY_RTH_OPEN", "MONDAY_OVERNIGHT_OPEN", "MANUAL"],
        key="weekend_monday_reference_type",
    )
    monday_price = cols[1].number_input(
        "下周首个交易日验证价（非 Binance 实时价）",
        min_value=0.0,
        value=0.0,
        step=0.01,
        key="weekend_monday_price",
    )
    estimated_cost_pct = cols[2].number_input(
        "估算成本（%）",
        min_value=0.0,
        value=0.0,
        step=0.05,
        key="weekend_monday_cost",
    )
    notes = cols[3].text_input("验证备注", value="", key="weekend_monday_notes")
    if st.button("保存/计算验证结果", width="stretch", key="weekend_save_monday_outcome"):
        if monday_price <= 0:
            st.warning("请输入有效的下周首个交易日验证价。")
        else:
            updated = update_monday_outcome(
                str(selected_summary.get("ticker") or ""),
                week_id=str(selected_summary.get("week_id") or ""),
                monday_reference_price=monday_price,
                reference_type=reference_type,
                estimated_cost_pct=estimated_cost_pct,
                notes=notes,
            )
            if updated:
                st.success(f"已保存信号验证结果：{updated.get('outcome_status')}")
            else:
                st.warning("未找到可更新的本周总结。")

    st.dataframe(_monday_outcome_frame(summaries), width="stretch", hide_index=True)


def _render_history_tab() -> None:
    st.subheader("历史规律")
    stats = build_history_stats()
    if not stats:
        st.info("暂无历史验证记录。记录周末样本并完成下周首个交易日验证后，这里会显示命中率和平均捕捉比例。")
        return
    st.dataframe(_history_frame(stats), width="stretch", hide_index=True)


def _render_backtest_tab(watchlist: list[str], mapping: dict[str, dict]) -> None:
    st.subheader("历史回测")
    st.warning(
        "正式回测口径：本周最后交易日盘后收盘价 → Binance 周末合约最高价 → "
        "下周第一个交易日美股夜盘首分钟收盘价。系统统计 Binance 周末冲高幅度、"
        "夜盘首分钟兑现程度和最终传导涨幅。"
    )
    include_unconfirmed = st.checkbox(
        "包含未确认映射",
        value=False,
        key="weekend_backtest_include_unconfirmed",
        help="未确认映射仅观察，默认不纳入正式胜率。",
    )
    effective_mapping = {str(key or "").strip().upper(): dict(value or {}) for key, value in (mapping or {}).items()}
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
    all_tickers = ["NVDA"]
    options = ["NVDA"]
    opening_anchor = "overnight"
    cols = st.columns(4)
    selected = cols[0].selectbox("标的", options, key="weekend_backtest_ticker")
    weeks = int(cols[1].number_input("回测周数", min_value=1, max_value=12, value=4, step=1, key="weekend_backtest_weeks"))
    open_window = 2
    cols[2].markdown("**夜盘窗口**  \n20:00-20:02 ET")
    cols[3].markdown("**开盘锚点**  \n下周第一个交易日夜盘 / 美东 20:00 ET")
    run_tickers = [selected]
    anchors = _backtest_anchor_mapping(run_tickers or all_tickers, weeks=weeks)
    preflight = build_weekend_backtest_preflight(
        run_tickers,
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
    _render_backtest_preflight(preflight)
    if not preflight.get("can_run"):
        st.warning(_backtest_block_text(str(preflight.get("primary_block_reason") or "")))
    op_cols = st.columns([2, 1, 1, 2])
    run_clicked = op_cols[0].button(
        _backtest_run_button_label(weeks),
        width="stretch",
        key="weekend_run_backtest",
        disabled=not bool(preflight.get("can_run")),
    )
    clear_clicked = op_cols[1].button("清空本次结果", width="stretch", key="weekend_clear_backtest_view")
    self_check_clicked = op_cols[2].button("夜盘数据源自检", width="stretch", key="weekend_overnight_provider_self_check")
    with op_cols[3].expander("查看排除原因", expanded=False):
        excluded = list(preflight.get("excluded") or preliminary.get("excluded") or [])
        st.dataframe(_backtest_exclusion_frame(excluded), width="stretch", hide_index=True)
    if self_check_clicked:
        with st.spinner("正在检查 NVDA 下周第一个交易日夜盘首分钟 1m bar..."):
            _render_overnight_provider_self_check(build_overnight_provider_self_check("NVDA"))
    _render_tradingview_backfill_tools()
    if not preflight.get("can_run"):
        st.session_state["weekend_backtest_results"] = []
        st.session_state["weekend_backtest_cache"] = clear_backtest_view_state()
        st.info(f"没有可回测标的：{_backtest_block_text(str(preflight.get('primary_block_reason') or 'NO_MAPPING'))}")
        return
    if clear_clicked:
        st.session_state["weekend_backtest_results"] = []
        st.session_state["weekend_backtest_cache"] = clear_backtest_view_state()
        st.info("已清空本次页面结果；不会删除已保存的历史回测缓存。")
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
        status_slot.caption(f"正在运行历史回测：{len(tickers)} 个标的，{weeks} 周。")
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
        st.session_state["weekend_backtest_results"] = results
        st.session_state["weekend_backtest_cache"] = saved
        if error_message:
            status_slot.warning(f"{error_message}\n\n{afterhours_anchor_note}")
        else:
            status_slot.success(f"回测完成：{len(results)} 条结果。{afterhours_anchor_note}")
    cached_result = dict(st.session_state.get("weekend_backtest_cache") or load_backtest_results())
    results = _current_backtest_results(
        st.session_state.get("weekend_backtest_results"),
        cached_result,
        preflight=preflight,
        mapping=effective_mapping,
        include_unconfirmed=include_unconfirmed,
    )
    if not results:
        if not preflight.get("can_run"):
            st.info(f"没有可回测标的：{_backtest_block_text(str(preflight.get('primary_block_reason') or 'NO_MAPPING'))}")
        elif cached_result.get("error_message"):
            st.warning(f"上次运行失败：{cached_result.get('error_message')}")
        else:
            st.info(
                f"尚未运行历史回测。配置映射后点击“{_backtest_run_button_label(weeks)}”。"
                "当前固定使用下周第一个交易日夜盘 20:00 ET 后第一根有效价格。"
            )
        _render_backfill_audit_area_v2(watchlist, mapping, anchors)
        _render_backtest_advanced_records()
        return
    last_run_at = str(cached_result.get("last_run_at") or "")
    if last_run_at:
        st.caption(f"上次运行：{_short_hkt_time(last_run_at)}")
    if include_unconfirmed:
        st.caption("观察回测：包含未确认映射，结果不计为正式胜率。")
    review_rows = _weekend_review_rows(results)
    ok_review_rows = _ok_weekend_review_rows(review_rows)
    _render_weekend_review_kpis(review_rows)
    _render_weekend_review_table(review_rows)
    with st.expander("历史回测 / 数据质量 / 排除提醒", expanded=False):
        if ok_review_rows:
            _render_backtest_kpis(results)
            st.dataframe(_backtest_frame(results), width="stretch", hide_index=True)
        else:
            st.info(_weekend_review_empty_reason(review_rows))
            st.dataframe(_backtest_diagnostic_frame(results), width="stretch", hide_index=True)
    _render_backfill_audit_area_v2(watchlist, mapping, anchors)
    _render_backtest_advanced_records()


def _render_backtest_tab(watchlist: list[str], mapping: dict[str, dict]) -> None:
    st.subheader("历史回测")
    st.caption(
        "正式回测口径：本周最后交易日盘后收盘价 → Binance 周末合约最高价 → 下周第一个交易日美股夜盘首分钟收盘价。"
        "系统统计 Binance 周末冲高幅度、夜盘首分钟兑现程度和最终传导涨幅。"
    )

    effective_mapping = {str(key or "").strip().upper(): dict(value or {}) for key, value in (mapping or {}).items()}
    all_tickers = _weekend_scope_tickers(watchlist)
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
        st.info("当前观察名单为空。周末价差只读取当前观察名单里的股票；请先在观察池添加标的。")
        with st.expander("数据源与补数工具", expanded=False):
            _render_tradingview_backfill_tools()
        return
    options = all_tickers
    opening_anchor = "overnight"
    open_window = 2
    selected_default = str(st.session_state.get("weekend_backtest_ticker") or options[0]).strip().upper()
    selected = selected_default if selected_default in options else options[0]
    if st.session_state.get("weekend_backtest_ticker") not in options:
        st.session_state["weekend_backtest_ticker"] = selected
    weeks = _safe_backtest_weeks(st.session_state.get("weekend_backtest_weeks"))
    include_unconfirmed = bool(st.session_state.get("weekend_backtest_include_unconfirmed") or False)
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
            key="weekend_backtest_include_unconfirmed",
            help="未确认映射仅观察，默认不纳入正式胜率。",
        )
        cols = st.columns(4)
        selected = cols[0].selectbox("标的", options, key="weekend_backtest_ticker")
        weeks = int(cols[1].number_input("回测周数", min_value=1, max_value=12, value=weeks, step=1, key="weekend_backtest_weeks"))
        cols[2].markdown("**夜盘窗口**  \n20:00-20:02 ET")
        cols[3].markdown("**开盘锚点**  \n下周第一个交易日夜盘 / 美东 20:00 ET")
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
            key="weekend_run_backtest",
            disabled=not bool(preflight.get("can_run")),
        )
        clear_clicked = op_cols[1].button("清空本次结果", width="stretch", key="weekend_clear_backtest_view")
        self_check_clicked = op_cols[2].button("夜盘数据源自检", width="stretch", key="weekend_overnight_provider_self_check")
        with op_cols[3].expander("查看排除原因", expanded=False):
            excluded = list(preflight.get("excluded") or preliminary.get("excluded") or [])
            st.dataframe(_backtest_exclusion_frame(excluded), width="stretch", hide_index=True)
        if self_check_clicked:
            with st.spinner(f"正在检查 {selected} 下周第一个交易日夜盘首分钟 1m bar..."):
                _render_overnight_provider_self_check(build_overnight_provider_self_check(selected))

    if not preflight.get("can_run"):
        st.session_state["weekend_backtest_results"] = []
        st.session_state["weekend_backtest_cache"] = clear_backtest_view_state()
        st.info(f"没有可回测标的：{_backtest_block_text(str(preflight.get('primary_block_reason') or 'NO_MAPPING'))}")
        with st.expander("数据源与补数工具", expanded=False):
            _render_tradingview_backfill_tools()
        return
    if clear_clicked:
        st.session_state["weekend_backtest_results"] = []
        st.session_state["weekend_backtest_cache"] = clear_backtest_view_state()
        st.info("已清空本次页面结果；不会删除已保存的历史回测缓存。")
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
        status_slot.caption(f"正在运行历史回测：{len(tickers)} 个标的，{weeks} 周。")
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
        st.session_state["weekend_backtest_results"] = results
        st.session_state["weekend_backtest_cache"] = saved
        if error_message:
            status_slot.warning(f"{error_message}\n\n{afterhours_anchor_note}")
        else:
            status_slot.success(f"回测完成：{len(results)} 条结果。{afterhours_anchor_note}")

    cached_result = dict(st.session_state.get("weekend_backtest_cache") or load_backtest_results())
    results = _current_backtest_results(
        st.session_state.get("weekend_backtest_results"),
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
        with st.expander("数据源与补数工具", expanded=False):
            _render_tradingview_backfill_tools()
        _render_backfill_audit_area_v2(watchlist, mapping, anchors)
        _render_backtest_advanced_records()
        return

    last_run_at = str(cached_result.get("last_run_at") or "")
    if last_run_at:
        st.caption(f"上次运行：{_short_hkt_time(last_run_at)}")
    if include_unconfirmed:
        st.caption("观察回测：包含未确认映射，结果不计为正式胜率。")

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
    with st.expander("数据源与补数工具", expanded=False):
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


def _render_overnight_provider_self_check(result: dict[str, object]) -> None:
    reason = _clean_self_check_text(result.get("reason"), "自检完成")
    if result.get("ok"):
        st.success("夜盘数据源自检通过：已读取 NVDA 下周第一个交易日夜盘首分钟 1m bar。")
    else:
        st.error(f"夜盘数据源自检失败：{reason}")
    rows = [
        ("OVERNIGHT_PRICE_PROVIDER", _clean_self_check_text(result.get("provider_display"), "未配置")),
        ("Alpaca 配置", "已配置" if result.get("alpaca_configured") else "缺少 API key"),
        ("Alpaca feed", _clean_self_check_text(result.get("feed"), "未返回")),
        ("timeframe", _clean_self_check_text(result.get("timeframe"), "未返回")),
        ("IBKR 配置", _ibkr_self_check_status(result)),
        ("请求窗口开始", _weekend_review_short_time(result.get("requested_start")) or "未返回"),
        ("请求窗口结束", _weekend_review_short_time(result.get("requested_end")) or "未返回"),
        ("返回 bar 数量", str(int(result.get("returned_bar_count") or 0))),
        ("第一根 bar 时间", _weekend_review_short_time(result.get("first_bar_time")) or "未返回"),
        (
            "第一根 bar close",
            _money_text(result.get("first_bar_close"))
            if _number(result.get("first_bar_close")) is not None
            else "未返回",
        ),
        ("provider 返回", _clean_self_check_text(result.get("provider"), "未返回")),
        ("数据质量", _data_quality_text(result.get("quality"))),
        ("疑似 15 分钟延迟", "是" if result.get("boats_delay_suspected") else "否"),
        ("失败原因", "" if result.get("ok") else reason),
    ]
    st.dataframe(pd.DataFrame(rows, columns=["检查项", "结果"]), width="stretch", hide_index=True)


def _ibkr_self_check_status(result: dict[str, object]) -> str:
    if result.get("ibkr_configured") and result.get("ibkr_path_exists"):
        return "已配置"
    if result.get("ibkr_configured"):
        return "已配置路径，但文件不存在"
    return "未配置"


def _clean_self_check_text(value: object, fallback: str) -> str:
    text = str(value or "").strip()
    if not text or text.lower() in {"none", "anchor_source"}:
        return fallback
    return text


def _render_tradingview_backfill_tools() -> None:
    with st.expander("TradingView Webhook 自动记录 / CSV 补数 / 手动补 P2", expanded=False):
        status = webhook_status_summary()
        st.caption("这些补数只用于周末价差三价模型，不会修改买区、研报或持仓数据。")
        cols = st.columns(4)
        cols[0].metric("Webhook secret", "已配置" if status.get("secret_configured") else "未配置")
        cols[1].metric("最近收到 symbol", status.get("latest_symbol") or "尚未收到")
        latest_p0 = dict(status.get("latest_p0") or {})
        latest_p2 = dict(status.get("latest_p2") or {})
        cols[2].metric("最近 P0", _tradingview_event_metric(latest_p0))
        cols[3].metric("最近 P2", _tradingview_event_metric(latest_p2))
        if not status.get("latest_write_ok"):
            st.info("尚未收到 TradingView 推送。")
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
        code_cols = st.columns(2)
        code_cols[0].code(json.dumps(example_p0, ensure_ascii=False, indent=2), language="json")
        code_cols[1].code(json.dumps(example_p2, ensure_ascii=False, indent=2), language="json")

        st.markdown("**TradingView CSV 本地导入**")
        csv_dir = st.text_input(
            "CSV 目录",
            value=str(DEFAULT_TRADINGVIEW_CSV_DIR),
            key="weekend_tradingview_csv_dir",
        )
        csv_cols = st.columns([1, 1, 3])
        if csv_cols[0].button("扫描 CSV", key="weekend_tv_scan_csv"):
            st.session_state["weekend_tv_csv_scan"] = scan_tradingview_csv_dir(csv_dir)
        if csv_cols[1].button("导入全部", key="weekend_tv_import_csv"):
            st.session_state["weekend_tv_csv_import"] = import_tradingview_csv_dir(csv_dir)
            st.success("CSV 导入完成。重新运行回测后会读取本地补数缓存。")
        scan_rows = st.session_state.get("weekend_tv_csv_import") or st.session_state.get("weekend_tv_csv_scan")
        if scan_rows:
            st.dataframe(pd.DataFrame(scan_rows), width="stretch", hide_index=True)

        st.markdown("**手动补下周第一个交易日美股夜盘首分钟价格**")
        manual_cols = st.columns([1, 1, 1, 1])
        manual_symbol = manual_cols[0].text_input("股票", value="NVDA", key="weekend_manual_p2_symbol")
        manual_time = manual_cols[1].text_input("时间 ET", value=_latest_overnight_session_text(), key="weekend_manual_p2_time")
        manual_price = manual_cols[2].number_input("首分钟收盘价", min_value=0.0, value=0.0, step=0.01, key="weekend_manual_p2_price")
        manual_source = manual_cols[3].selectbox("来源", ["IBKR", "Alpaca", "富途", "老虎", "其他"], key="weekend_manual_p2_source")
        manual_note = st.text_input("备注，可选", key="weekend_manual_p2_note")
        if st.button("保存人工券商样本", key="weekend_save_manual_p2"):
            if not manual_symbol.strip() or manual_price <= 0:
                st.error("请填写股票和大于 0 的夜盘首分钟收盘价。")
            else:
                try:
                    upsert_manual_overnight_price(
                        symbol=manual_symbol,
                        timestamp_et=manual_time,
                        close=manual_price,
                        source=manual_source,
                        note=manual_note,
                    )
                    st.success("已保存人工券商样本。重新运行回测后会读取。")
                except ValueError as exc:
                    st.error(f"保存失败：{exc}")


def _tradingview_event_metric(row: dict[str, object]) -> str:
    if not row:
        return "尚未收到"
    close = _money_text(row.get("close")) if _number(row.get("close")) is not None else "价格缺失"
    time_text = _weekend_review_short_time(row.get("timestamp_et")) or "时间缺失"
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
    with st.expander("周末价差回顾 / 历史回放", expanded=False):
        st.caption("默认只看价差和溢价百分比；Backfill Audit、数据质量和排除提醒收在详情里。")
        all_tickers = [str(ticker or "").strip().upper() for ticker in watchlist if str(ticker or "").strip()]
        mapped_tickers = [
            ticker
            for ticker in all_tickers
            if (mapping.get(ticker) or {}).get("binance_symbol")
        ]
        confirmed_tickers = [
            ticker
            for ticker in mapped_tickers
            if str((mapping.get(ticker) or {}).get("mapping_confidence") or "").strip().lower() == "confirmed"
        ]
        mode = st.radio(
            "统计口径",
            ["全部观察样本", "仅 confirmed / trade-grade 样本"],
            horizontal=True,
            key="weekend_backfill_mode",
        )
        trade_grade_only = mode.startswith("仅 confirmed")
        available_tickers = confirmed_tickers if trade_grade_only else mapped_tickers
        options = ["全部可用映射"] + available_tickers if available_tickers else ["全部可用映射"]
        cols = st.columns([1.1, 0.8, 1.2, 1.1, 1])
        selected = cols[0].selectbox("回放标的", options, key="weekend_backfill_ticker")
        weeks = int(cols[1].number_input("完整周末数", min_value=1, max_value=16, value=8, step=1, key="weekend_backfill_weeks"))
        rule_filter = cols[2].selectbox(
            "规则",
            ["全部规则", "FIRST_THRESHOLD", "RELATIVE_HIGH_PULLBACK"],
            key="weekend_backfill_rule",
        )
        include_estimated = cols[3].checkbox("包含 estimated", value=True, key="weekend_backfill_include_estimated")
        low_risk_only = cols[4].checkbox("仅低风险窗口", value=False, key="weekend_backfill_low_risk_only")
        run_tickers = available_tickers if selected == "全部可用映射" else [selected]
        if not confirmed_tickers:
            st.info("当前无 confirmed mapping，因此没有交易级样本；候选映射仍可用于观察复盘，不作为交易依据。")
        if st.button("运行历史周末回放", key="weekend_run_backfill_audit", width="stretch", disabled=not bool(run_tickers)):
            progress = st.progress(0.0)
            status = st.empty()
            status.caption(f"正在回放 {len(run_tickers)} 个标的，最近 {weeks} 个完整周末。")
            rows = run_weekend_basis_backfill_audit(
                run_tickers,
                mapping=mapping,
                anchors=anchors,
                weeks=weeks,
                include_estimated=include_estimated,
                include_observation=not trade_grade_only,
                trade_grade_only=trade_grade_only,
                low_risk_window_only=low_risk_only,
            )
            progress.progress(1.0)
            st.session_state["weekend_backfill_audit_rows"] = rows
            status.success(f"历史回放完成：{len(rows)} 条结果。")
        rows = list(st.session_state.get("weekend_backfill_audit_rows") or [])
        if rule_filter != "全部规则":
            rows = [row for row in rows if str(row.get("rule_name") or "").startswith(rule_filter)]
        if not rows:
            st.info("暂无历史回放结果。点击上方按钮后会显示 observation / trade-grade / estimated 明细。")
            return
        review_rows = _weekend_review_rows(rows)
        _render_weekend_review_kpis(review_rows)
        _render_weekend_review_table(review_rows)
        with st.expander("历史回放 / 数据质量 / 排除提醒 / Backfill Audit", expanded=False):
            _render_backfill_kpis_v2(rows)
            st.dataframe(_backfill_frame_v2(rows), width="stretch", hide_index=True)
            st.dataframe(_backfill_detail_frame(rows), width="stretch", hide_index=True)


def _render_backfill_audit_area(watchlist: list[str], mapping: dict[str, dict], anchors: dict[str, dict]) -> None:
    with st.expander("历史周末回放 / Backfill Audit", expanded=False):
        st.caption("回放过去完整周末：周日 Binance 高溢价做空，Sunday 20:00 ET 后券商 overnight 第一根有效 1m bar 买入现货对冲。仅 confirmed mapping 进入 strict statistics。")
        all_tickers = [str(ticker or "").strip().upper() for ticker in watchlist if str(ticker or "").strip()]
        confirmed_tickers = [
            ticker
            for ticker in all_tickers
            if str((mapping.get(ticker) or {}).get("mapping_confidence") or "").strip().lower() == "confirmed"
            and (mapping.get(ticker) or {}).get("binance_symbol")
        ]
        options = ["全部 confirmed"] + confirmed_tickers if confirmed_tickers else ["全部 confirmed"]
        cols = st.columns([1.1, 0.8, 1.2, 1.1, 1])
        selected = cols[0].selectbox("回放标的", options, key="weekend_backfill_ticker")
        weeks = int(cols[1].number_input("完整周末数", min_value=1, max_value=16, value=8, step=1, key="weekend_backfill_weeks"))
        rule_filter = cols[2].selectbox(
            "规则",
            ["全部规则", "FIRST_THRESHOLD", "RELATIVE_HIGH_PULLBACK"],
            key="weekend_backfill_rule",
        )
        include_estimated = cols[3].checkbox("包含 estimated", value=False, key="weekend_backfill_include_estimated")
        low_risk_only = cols[4].checkbox("仅低风险窗口", value=False, key="weekend_backfill_low_risk_only")
        run_tickers = confirmed_tickers if selected == "全部 confirmed" else [selected]
        if not confirmed_tickers:
            st.info("当前没有 confirmed mapping，请先运行 Mapping Audit 并手动确认映射。")
        if st.button("运行历史周末回放", key="weekend_run_backfill_audit", width="stretch", disabled=not bool(run_tickers)):
            progress = st.progress(0.0)
            status = st.empty()
            status.caption(f"正在回放 {len(run_tickers)} 个标的，最近 {weeks} 个完整周末。")
            rows = run_weekend_basis_backfill_audit(
                run_tickers,
                mapping=mapping,
                anchors=anchors,
                weeks=weeks,
                include_estimated=include_estimated,
                low_risk_window_only=low_risk_only,
            )
            progress.progress(1.0)
            st.session_state["weekend_backfill_audit_rows"] = rows
            status.success(f"历史回放完成：{len(rows)} 条结果。")
        rows = list(st.session_state.get("weekend_backfill_audit_rows") or [])
        if rule_filter != "全部规则":
            rows = [row for row in rows if str(row.get("rule_name") or "").startswith(rule_filter)]
        if not rows:
            st.info("暂无历史回放结果。点击上方按钮后会显示 strict / estimated / observation 明细。")
            return
        _render_backfill_kpis(rows)
        st.dataframe(_backfill_frame(rows), width="stretch", hide_index=True)
        with st.expander("单周详情", expanded=False):
            st.dataframe(_backfill_detail_frame(rows), width="stretch", hide_index=True)


def _backtest_block_text(reason: str) -> str:
    return {
        "NO_MAPPING": "当前没有可回测标的：请配置 local mapping。",
        "AUTO_CANDIDATE_NOT_ALLOWED": "自动候选映射默认不进入正式回测；如仅做观察，请勾选包含未确认映射。",
        "UNCONFIRMED_EXCLUDED": "当前只有未确认映射；如仅做观察，请勾选包含未确认映射。",
        "SYMBOL_INVALID": "symbol 无效，请先在映射管理里复核。",
        "BINANCE_KLINE_UNAVAILABLE": "Binance K 线不可用。",
        "FUTURES_UNAVAILABLE": "Futures 数据源不可用。",
        "NO_PRICE_ANCHOR": "缺少价格锚点：请先刷新实时观察或更新最后交易日盘后锚点。",
        "PROVIDER_ERROR": "数据源错误，请稍后重试。",
    }.get(reason, reason or "当前没有可回测标的。")


def _backtest_mode_text(value: object) -> str:
    text = str(value or "").strip().lower()
    if text in {"auto usable", "auto_usable", "auto"}:
        return "自动可用"
    if text in {"include candidate", "include_candidates", "include unconfirmed"}:
        return "包含未确认映射"
    if text in {"confirmed only", "confirmed_only", "confirmed"}:
        return "仅确认映射"
    return str(value or "仅确认映射")


def _market_type_text(value: object) -> str:
    text = str(value or "").strip().lower()
    if text == "usdm_futures":
        return "USDT-M 合约"
    if text == "spot":
        return "现货（已停用）"
    return str(value or "暂缺")


def _mapping_status_text(value: object) -> str:
    text = str(value or "").strip().lower()
    return {
        "confirmed": "已确认",
        "candidate": "候选待确认",
        "unverified": "未验证",
        "no_mapping": "暂无映射",
        "missing": "暂无映射",
        "invalid": "无效",
    }.get(text, str(value or "暂缺"))


def _data_quality_text(value: object) -> str:
    text = str(value or "").strip().upper()
    return {
        "OK": "可用",
        "MAPPING_MISSING": "映射缺失",
        "STOCK_MISSING": "美股价格缺失",
        "CONTRACT_MISSING": "合约价格缺失",
        "STALE_CACHE": "缓存过期",
        "INVALID_PRICE": "价格无效",
        "UNCONFIRMED_MAPPING": "未确认映射，仅观察",
        "OBSERVE_ONLY": "观察样本，不计入正式胜率",
        "OBSERVE_ANCHOR_ONLY": "锚点观察样本，不计入正式胜率",
        "NO_AFTERHOURS_CLOSE": "缺少本周最后交易日盘后收盘价",
        "REGULAR_CLOSE_FALLBACK": "常规收盘回退，仅观察",
        "MISSING_OVERNIGHT_FIRST_1M": "缺少下周第一个交易日美股夜盘首分钟 1m K 线",
        "OVERNIGHT_PROVIDER_MISSING": "美股夜盘数据源未配置",
        "TRADINGVIEW_WEBHOOK_SAMPLE": "TradingView Webhook 样本",
        "TRADINGVIEW_CSV_SAMPLE": "TradingView CSV 样本",
        "MANUAL_BROKER_SAMPLE": "人工券商样本",
        "MANUAL_AFTERHOURS_SAMPLE": "人工盘后样本",
        "ALPACA_BOATS_SAMPLE": "Alpaca BOATS 样本",
        "BOATS_DELAY_PENDING": "BOATS 历史数据可能延迟",
        "ALPACA_BOATS_PERMISSION": "Alpaca BOATS 权限不足",
        "MISSING_BOATS_FIRST_1M": "缺少下周第一个交易日 BOATS 夜盘首分钟 1m K线",
        "PROVIDER_ERROR": "provider 报错",
        "DEGRADED": "降级样本",
        "DEGRADED_5M": "5m 降级样本",
        "BINANCE_KLINE_UNAVAILABLE": "Binance K 线不可用",
        "NO_BROKER_OVERNIGHT_BAR": "缺少券商 overnight bar",
        "MISSING_STOCK_FIRST_BAR": "缺少美股端第一根有效价格",
        "HOLIDAY_OR_NO_SESSION": "假期或无有效交易时段",
        "STALE_OR_MISALIGNED": "时间未对齐",
        "WIDE_SPREAD": "价差过宽",
        "LOW_DEPTH": "深度不足",
        "ESTIMATED_EXECUTION": "估算执行，仅观察",
        "DATA_UNAVAILABLE": "数据不可用",
        "INVALID": "无效样本",
        "NO_PRICE_ANCHOR": "缺少价格锚点",
        "ANCHOR_REGULAR_CLOSE_ONLY": "仅有正常收盘锚点",
        "BLOCK_MAPPING": "映射未确认",
        "NO_MAPPING": "暂无映射",
        "MISSING": "缺失",
    }.get(text, str(value or "暂缺"))


def _backfill_mapping_status_text(value: object) -> str:
    text = str(value or "").strip().upper()
    return {
        "CONFIRMED_TRADE_GRADE": "已确认 / 交易级",
        "CANDIDATE_OBSERVATION": "候选映射，仅观察",
    }.get(text, str(value or "暂缺"))


def _basis_status_text(value: object) -> str:
    text = str(value or "").strip().upper()
    overrides = {
        "ALLOW_SHORT": "高溢价观察",
        "BLOCK_MAPPING": "映射未确认",
        "BLOCK_LIQUIDITY": "流动性风险",
        "BLOCK_DATA": "数据不足",
    }
    if text in overrides:
        return overrides[text]
    return {
        "OBSERVE": "观察",
        "ENTRY_CANDIDATE": "入场候选",
        "SHORT_OPEN": "空单已开",
        "WAIT_BROKER_OPEN": "等待券商夜盘",
        "HEDGE_DUE": "待对冲",
        "HEDGE_LOCKED": "已锁仓",
        "EXIT_READY": "可准备退出",
        "CLOSED": "双腿已关闭",
        "FAILED": "失败",
    }.get(text, str(value or "暂缺"))


def _exclusion_reason_text(value: object) -> str:
    text = str(value or "").strip().upper()
    return {
        "NO_MAPPING": "暂无映射",
        "AUTO_CANDIDATE_NOT_ALLOWED": "自动候选不进入主流程",
        "UNCONFIRMED_EXCLUDED": "未确认映射默认排除",
        "SYMBOL_INVALID": "合约无效",
        "BINANCE_KLINE_UNAVAILABLE": "Binance K 线不可用",
        "FUTURES_UNAVAILABLE": "USDT-M 合约数据源不可用",
        "NO_PRICE_ANCHOR": "缺少价格锚点",
        "PROVIDER_ERROR": "数据源错误",
    }.get(text, str(value or "暂缺"))


def _backtest_exclusion_frame(rows: list[dict]) -> pd.DataFrame:
    columns = [
        ("ticker", "标的"),
        ("symbol", "合约"),
        ("market_type", "市场"),
        ("mapping_status", "映射状态"),
        ("exclusion_reason", "排除原因"),
    ]
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=[label for _, label in columns])
    display = pd.DataFrame()
    for key, label in columns:
        display[label] = frame.get(key)
    display["市场"] = display["市场"].map(_market_type_text)
    display["映射状态"] = display["映射状态"].map(_mapping_status_text)
    display["排除原因"] = display["排除原因"].map(_exclusion_reason_text)
    return display


def _backtest_error_message(rows: list[dict]) -> str:
    if not rows:
        return ""
    grouped: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        ticker = str(row.get("ticker") or "").strip().upper() or "UNKNOWN"
        quality = str(row.get("data_quality") or "").strip().upper()
        raw_error = str(row.get("error_message") or "")
        if quality == "OVERNIGHT_PROVIDER_MISSING":
            reason = "美股夜盘数据源未配置"
        elif quality in {"NO_BROKER_OVERNIGHT_BAR", "MISSING_STOCK_FIRST_BAR", "MISSING_OVERNIGHT_FIRST_1M"}:
            reason = "缺少美股端第一根有效 1m bar"
        elif quality == "HOLIDAY_OR_NO_SESSION":
            reason = "遇到美国假期或无有效交易时段"
        elif quality in {"DATA_UNAVAILABLE", "BINANCE_KLINE_UNAVAILABLE"}:
            reason = f"Binance K 线不可用{f'：{raw_error}' if raw_error else ''}"
        elif quality == "STALE_OR_MISALIGNED":
            reason = "Binance 与券商时间未对齐"
        elif quality == "INVALID":
            reason = raw_error or "无效样本"
        else:
            reason = raw_error or _data_quality_text(quality)
        grouped.setdefault((ticker, reason), []).append(row)
    reasons = []
    for (ticker, reason), items in grouped.items():
        if len(items) > 1:
            reasons.append(f"{ticker}：近 {len(items)} 周均{reason}，已排除 {len(items)} 个样本")
        else:
            reasons.append(f"{ticker}：{reason}")
    return "；".join(reasons[:5])


def _backtest_diagnostic_frame(rows: list[dict]) -> pd.DataFrame:
    columns = [
        ("week_id", "周期日期"),
        ("ticker", "标的"),
        ("stock_bar_requested_start", "请求开始"),
        ("stock_bar_requested_end", "请求结束"),
        ("stock_open_anchor_label", "开盘锚点"),
        ("stock_bar_provider", "数据源"),
        ("stock_bar_size", "bar 大小"),
        ("stock_bar_returned_count", "返回 bar 数量"),
        ("stock_bar_timestamp", "第一根 bar 时间"),
        ("data_quality", "数据质量"),
        ("warning", "排除原因"),
    ]
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=[label for _, label in columns])
    display = pd.DataFrame()
    for key, label in columns:
        if key == "warning":
            display[label] = frame.apply(lambda row: _backtest_row_warning(row.to_dict()), axis=1)
        else:
            display[label] = frame.get(key)
    for time_col in ("请求开始", "请求结束", "第一根 bar 时间"):
        display[time_col] = display[time_col].map(_short_hkt_time)
    display["数据质量"] = display["数据质量"].map(_data_quality_text)
    display["返回 bar 数量"] = display["返回 bar 数量"].fillna(0).astype(int)
    return display


def _render_backtest_kpis(rows: list[dict]) -> None:
    summary = summarize_backtest_results(rows)
    cols = st.columns(7)
    cols[0].metric("近4周样本数", int(summary.get("sample_weeks") or 0))
    cols[1].metric("平均溢价抹平率", _percent_text(summary.get("avg_premium_decay_ratio")))
    cols[2].metric("平均锁仓收益", _bps_text(summary.get("avg_net_locked_bps")))
    cols[3].metric("正收益周数", int(summary.get("positive_weeks") or 0))
    cols[4].metric("胜率", _ratio_text(summary.get("win_rate")))
    cols[5].metric("最大溢价抹平", _percent_text(summary.get("max_premium_decay_pct")))
    cols[6].metric("最大未抹平风险", _percent_text(summary.get("max_unflattened_risk_pct")))


def _render_backfill_kpis_v2(rows: list[dict]) -> None:
    summary = summarize_backfill_audit_results(rows)
    obs_cols = st.columns(5)
    obs_cols[0].metric("观察样本", int(summary.get("observation_sample_count") or 0))
    obs_cols[1].metric("平均周日峰值溢价", _bps_text(summary.get("avg_sunday_max_premium_bps")))
    obs_cols[2].metric("平均开盘残余价差", _bps_text(summary.get("avg_open_residual_basis_bps")))
    obs_cols[3].metric("平均溢价抹平", _bps_text(summary.get("avg_premium_decay_bps")))
    obs_cols[4].metric("平均最大不利波动", _bps_text(summary.get("avg_max_adverse_bps")))
    trade_cols = st.columns(4)
    trade_cols[0].metric("交易级样本", int(summary.get("trade_grade_sample_count") or 0))
    trade_cols[1].metric("平均锁结 bps", _bps_text(summary.get("avg_net_locked_bps")))
    trade_cols[2].metric("中位锁结 bps", _bps_text(summary.get("median_net_locked_bps")))
    trade_cols[3].metric("hedge success", _ratio_text(summary.get("hedge_success_rate")))
    if not int(summary.get("trade_grade_sample_count") or 0):
        st.info("当前无 confirmed mapping，因此没有交易级样本；下方为候选映射观察复盘，不作为交易依据。")


def _render_backfill_kpis(rows: list[dict]) -> None:
    summary = summarize_backfill_audit_results(rows)
    cols = st.columns(6)
    cols[0].metric("样本数", int(summary.get("sample_count") or 0))
    cols[1].metric("strict 样本", int(summary.get("strict_sample_count") or 0))
    cols[2].metric("平均锁仓", _bps_text(summary.get("avg_net_locked_bps")))
    cols[3].metric("中位锁仓", _bps_text(summary.get("median_net_locked_bps")))
    cols[4].metric("最差收益", _bps_text(summary.get("worst_net_locked_bps")))
    cols[5].metric("hedge success", _ratio_text(summary.get("hedge_success_rate")))


def _render_weekend_review_kpis(review_rows: list[dict]) -> None:
    summary = _weekend_review_summary(review_rows)
    quality_counts = _weekend_review_quality_counts(review_rows)
    metrics: list[tuple[str, object, str]] = [
        ("正式样本数", int(summary.get("sample_count") or 0), ""),
        ("平均周末冲高%", summary.get("avg_binance_premium_pct"), "percent"),
        ("夜盘相对高点%", summary.get("avg_overnight_vs_binance_pct"), "percent"),
        ("平均兑现率%", summary.get("avg_capture_pct"), "percent"),
        ("最新一周兑现率%", summary.get("latest_week_capture_pct"), "percent"),
    ]
    cols = st.columns(len(metrics))
    if not int(summary.get("sample_count") or 0):
        for col, (label, _, _) in zip(cols, metrics):
            col.metric(label, "暂无数据")
        st.caption(
            f"正式有效样本 {quality_counts['ok']}｜观察样本 {quality_counts['observe']}｜"
            f"降级样本 {quality_counts['degraded']}｜排除样本 {quality_counts['excluded']}"
        )
        st.info(_weekend_review_empty_reason(review_rows))
        return
    for col, (label, value, kind) in zip(cols, metrics):
        if kind == "percent":
            col.metric(label, _review_percent_text(value))
        else:
            col.metric(label, value)
    st.caption(
        f"正式有效样本 {quality_counts['ok']}｜观察样本 {quality_counts['observe']}｜"
        f"降级样本 {quality_counts['degraded']}｜排除样本 {quality_counts['excluded']}"
    )
    if summary.get("summary_quality") == "OBSERVE":
        st.info("当前显示的是观察样本统计：已读取往期最后交易日盘后/收盘锚点和周末合约价格，但缺少下周第一个交易日美股夜盘首分钟价格，不计入正式胜率。")


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
        return "缺少 " + " / ".join(missing)
    return "待计算"


def _p0_source_summary(row: dict) -> str:
    quality = str(row.get("p0_quality") or row.get("data_quality") or "").upper()
    source = _price_source_text(row.get("friday_afterhours_provider") or row.get("p0_provider"))
    if "FMP" in quality or "FMP" in source.upper():
        if quality in {"FMP_AFTERHOURS_1M_BAR", "FMP_AFTERHOURS_TRADE"}:
            return "FMP 盘后，待验证"
        return "FMP 盘后"
    if quality in {"REGULAR_CLOSE_FALLBACK", "FALLBACK_REGULAR_CLOSE"} or row.get("p0_is_fallback"):
        return "常规收盘回退"
    return source or "未配置"


def _p1_source_summary(row: dict) -> str:
    provider = str(row.get("binance_provider") or "").strip().upper()
    if not provider or provider == "BINANCE_USDT_M":
        return "Binance USDT-M max(high)"
    return _price_source_text(provider)


def _p2_source_summary(row: dict) -> str:
    source = _price_source_text(row.get("overnight_provider"))
    if not source or source == "未配置":
        return _data_quality_text(row.get("data_quality"))
    return source


def _render_weekend_review_core_card(review_rows: list[dict], *, weeks: int = 4) -> None:
    row = _latest_weekend_review_row(review_rows)
    if not row:
        st.info(f"还没有可展示的三价样本。展开“回测设置”后{_backtest_run_button_label(weeks)}。")
        return
    metrics = [
        ("Binance 周末冲高%", _percent_or_missing(row.get("binance_premium_pct"), row)),
        ("夜盘相对 Binance 高点%", _percent_or_missing(row.get("overnight_vs_binance_pct"), row)),
        ("夜盘相对最后交易日盘后%", _percent_or_missing(row.get("overnight_vs_afterhours_pct"), row)),
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
          <div class="weekend-core-flow-label">最后交易日盘后收盘 → Binance 周末最高 → 下周第一个交易日夜盘首分钟</div>
          <div class="weekend-core-flow">
            {escape(_money_or_missing(row.get("friday_afterhours_close"), "缺少 P0"))}
            → {escape(_money_or_missing(row.get("binance_price"), "缺少 P1"))}
            → {escape(_money_or_missing(row.get("broker_open_close"), "缺少 P2"))}
          </div>
          <div class="weekend-core-metrics">{metric_html}</div>
          <div class="weekend-core-sources">
            P0 来源：{escape(_p0_source_summary(row))} ｜
            P1 来源：{escape(_p1_source_summary(row))} ｜
            P2 来源：{escape(_p2_source_summary(row))}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if _number(row.get("broker_open_close")) is None:
        st.caption("缺少下周第一个交易日夜盘首分钟价格，只能观察 Binance 周末冲高。")
    elif str(row.get("data_quality") or "").strip().upper() == "P0_UNVERIFIED":
        st.caption("P2 已来自 Alpaca BOATS，P0 仍需验证盘后原始 bar。")


def _render_weekend_review_kpis(review_rows: list[dict]) -> None:
    counts = _weekend_review_quality_counts(review_rows)
    display_count = len(_display_weekend_review_rows(review_rows))
    alpaca_count = sum(
        1
        for row in review_rows
        if "ALPACA_BOATS" in str(row.get("overnight_provider") or "").upper()
        or "ALPACA BOATS" in str(row.get("sample_status") or "").upper()
    )
    observe_count = counts["observe"] + counts["degraded"] + counts["excluded"]
    formal_note = "正式样本 0，当前为观察 / 待验证样本。" if counts["ok"] == 0 else "正式样本可用于统计。"
    st.markdown(
        f"""
        <div class="weekend-status-strip">
          可分析样本：{display_count} ｜ 自动正式样本：{counts["ok"]} ｜
          Alpaca BOATS 样本：{alpaca_count} ｜ 观察 / 待验证样本：{observe_count} ｜ {escape(formal_note)}
        </div>
        """,
        unsafe_allow_html=True,
    )


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
        return "当前没有可计入正式统计的样本。请先确认 NVDA -> NVDAUSDT 映射已确认，并运行回测。"
    qualities = {str(row.get("data_quality") or "").strip().upper() for row in review_rows}
    raw_qualities = {
        str((row.get("raw_row") or {}).get("data_quality") or row.get("data_quality") or "").strip().upper()
        for row in review_rows
    }
    if qualities & {"OVERNIGHT_PROVIDER_MISSING"}:
        return (
            "当前只有观察样本：已读取最后交易日盘后收盘价和 Binance 周末最高价，"
            "但美股夜盘数据源未配置，不计入正式统计。"
        )
    if qualities & {"MISSING_OVERNIGHT_FIRST_1M"}:
        return (
            "当前只有观察样本：已读取最后交易日盘后收盘价和 Binance 周末最高价，"
            "但缺少下周第一个交易日美股夜盘首分钟 1m K 线，不计入正式统计。"
        )
    if qualities & {"REGULAR_CLOSE_FALLBACK", "FALLBACK_REGULAR_CLOSE"}:
        return "当前只有观察样本：部分周次缺少最后交易日盘后收盘价，已用常规收盘价回退，不计入正式统计。"
    if qualities & {"P0_UNVERIFIED"}:
        return "当前只有观察样本：P0 盘后价格缺少可验证的 19:55-20:00 ET 原始 1m bar 证据，不计入正式统计。"
    if qualities & {"NO_AFTERHOURS_CLOSE"}:
        return "当前没有完整传导链样本。主要原因：缺少本周最后交易日盘后收盘价。"
    if raw_qualities & {"MISSING_STOCK_FIRST_BAR", "NO_BROKER_OVERNIGHT_BAR"}:
        return (
            "当前没有正式样本。主要原因：缺少下周第一个交易日夜盘 20:00 ET 第一根 1m bar。"
            "请检查夜盘历史 1m 数据权限。"
        )
    if raw_qualities & {"HOLIDAY_OR_NO_SESSION"}:
        return "当前没有正式样本。主要原因：遇到美国假期或没有下周第一个交易日夜盘 session。"
    if qualities == {"MAPPING_MISSING"}:
        return "当前没有正式样本。主要原因：映射未确认或未配置；请先确认 NVDA -> NVDAUSDT。"
    if qualities == {"CONTRACT_MISSING"}:
        return "当前没有正式样本。主要原因：Binance USDT-M 合约周末 1m K 线缺失。"
    if qualities == {"STOCK_MISSING"}:
        return "当前没有正式样本。主要原因：缺少下周第一个交易日券商夜盘首分钟价格。"
    return "当前没有正式样本。请展开数据质量详情，查看每周请求区间、返回 bar 数量和排除原因。"


def _render_weekend_review_table(review_rows: list[dict]) -> None:
    frame = _weekend_review_frame(_display_weekend_review_rows(review_rows))
    if frame.empty:
        st.info("暂无可信历史回测表；没有 OK 样本时不会展示旧缓存或回退数据。")
        return
    st.dataframe(_style_weekend_review_frame(frame), width="stretch", hide_index=True)


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


def _weekend_review_frame(review_rows: list[dict]) -> pd.DataFrame:
    columns = [
        "周次",
        "股票",
        "Binance 合约",
        "本周最后交易日",
        "常规收盘价",
        "最后交易日盘后收盘价",
        "P0较常规%",
        "P0 来源",
        "P0 请求区间",
        "P0 endpoint",
        "P0 返回bars",
        "P0 选中时间",
        "P0 选中close",
        "P0 volume",
        "P0 quality",
        "P0 fallback",
        "P0失败原因",
        "Binance 周末最高价",
        "Binance 高点时间",
        "下周首个交易日夜盘首分钟收盘",
        "P2 来源",
        "下周首个交易日夜盘时间",
        "Binance 周末冲高%",
        "夜盘相对 Binance 高点%",
        "夜盘相对最后交易日盘后%",
        "周末高点兑现率%",
        "样本状态",
        "状态",
        "失败原因",
    ]
    if not review_rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(
        [
            {
                "周次": row.get("week_id"),
                "股票": row.get("ticker"),
                "Binance 合约": row.get("binance_symbol") or "暂无映射",
                "本周最后交易日": row.get("last_trading_day") or "未返回",
                "常规收盘价": row.get("regular_close_price"),
                "最后交易日盘后收盘价": row.get("friday_afterhours_close"),
                "P0较常规%": row.get("p0_vs_regular_close_pct"),
                "P0 来源": _price_source_text(row.get("friday_afterhours_provider")),
                "P0 请求区间": row.get("p0_request_window") or "暂无数据",
                "P0 endpoint": row.get("p0_endpoint") or "暂无数据",
                "P0 返回bars": row.get("p0_returned_bar_count"),
                "P0 选中时间": row.get("p0_selected_bar_time") or "暂无数据",
                "P0 选中close": row.get("p0_selected_bar_close"),
                "P0 volume": row.get("p0_selected_bar_volume"),
                "P0 quality": _price_source_text(row.get("p0_quality")),
                "P0 fallback": "是" if row.get("p0_is_fallback") else "否",
                "P0失败原因": row.get("p0_failure_reason") or "",
                "Binance 周末最高价": row.get("binance_price"),
                "Binance 高点时间": row.get("contract_sample_time") or "暂无数据",
                "下周首个交易日夜盘首分钟收盘": row.get("broker_open_close"),
                "P2 来源": _price_source_text(row.get("overnight_provider")),
                "下周首个交易日夜盘时间": row.get("broker_first_time") or "暂无数据",
                "Binance 周末冲高%": row.get("binance_premium_pct"),
                "夜盘相对 Binance 高点%": row.get("overnight_vs_binance_pct"),
                "夜盘相对最后交易日盘后%": row.get("overnight_vs_afterhours_pct"),
                "周末高点兑现率%": row.get("capture_pct"),
                "样本状态": row.get("sample_status"),
                "状态": row.get("status"),
                "失败原因": row.get("failure_reason") or "",
            }
            for row in review_rows
        ],
        columns=columns,
    )


def _style_weekend_review_frame(frame: pd.DataFrame):
    def color_value(value: object) -> str:
        number = _number(value)
        if number is None:
            return "color: #94a3b8;"
        if number > 0.05:
            return "color: #c2410c; font-weight: 800;"
        if number < -0.05:
            return "color: #047857; font-weight: 800;"
        return "color: #64748b; font-weight: 700;"

    money_columns = ["常规收盘价", "最后交易日盘后收盘价", "P0 选中close", "Binance 周末最高价", "下周首个交易日夜盘首分钟收盘"]
    percent_columns = ["P0较常规%", "Binance 周末冲高%", "夜盘相对 Binance 高点%", "夜盘相对最后交易日盘后%", "周末高点兑现率%"]
    formatters = {
        column: (lambda value: _money_text(value) if _number(value) is not None else "暂无数据")
        for column in money_columns
        if column in frame.columns
    }
    formatters.update(
        {
            column: (lambda value: _review_percent_text(value))
            for column in percent_columns
            if column in frame.columns
        }
    )
    styler = frame.style.format(formatters)
    color_subset = [column for column in percent_columns if column in frame.columns]
    if not color_subset:
        return styler
    if hasattr(styler, "map"):
        return styler.map(color_value, subset=color_subset)

    def color_frame(data: pd.DataFrame) -> pd.DataFrame:
        if hasattr(data, "map"):
            return data.map(color_value)
        return data.applymap(color_value)

    return styler.apply(color_frame, subset=color_subset, axis=None)


def _weekend_review_frame(review_rows: list[dict]) -> pd.DataFrame:
    columns = [
        "周次",
        "股票",
        "本周最后交易日",
        "P0 最后交易日盘后",
        "P1 Binance 高点",
        "P2 下周首个交易日夜盘",
        "周末冲高%",
        "高点回落%",
        "最终传导%",
        "兑现率%",
        "样本状态",
    ]
    if not review_rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(
        [
            {
                "周次": row.get("week_id"),
                "股票": row.get("ticker"),
                "本周最后交易日": row.get("last_trading_day") or "未返回",
                "P0 最后交易日盘后": row.get("friday_afterhours_close"),
                "P1 Binance 高点": row.get("binance_price"),
                "P2 下周首个交易日夜盘": row.get("broker_open_close"),
                "周末冲高%": row.get("binance_premium_pct"),
                "高点回落%": row.get("overnight_vs_binance_pct"),
                "最终传导%": row.get("overnight_vs_afterhours_pct"),
                "兑现率%": row.get("capture_pct"),
                "样本状态": row.get("sample_status") or row.get("status"),
            }
            for row in review_rows
        ],
        columns=columns,
    )


def _weekend_review_diagnostic_frame(review_rows: list[dict]) -> pd.DataFrame:
    columns = [
        "周次",
        "股票",
        "Binance 合约",
        "本周最后交易日",
        "常规收盘价",
        "最后交易日盘后收盘价",
        "P0较常规%",
        "P0 来源",
        "P0 请求区间",
        "P0 endpoint",
        "P0 返回bars",
        "P0 选中时间",
        "P0 选中close",
        "P0 volume",
        "P0 quality",
        "P0 fallback",
        "P0失败原因",
        "P1 窗口",
        "Binance 周末最高价",
        "Binance 高点时间",
        "下周首个交易日夜盘首分钟收盘",
        "P2 来源",
        "P2 下周首个交易日夜盘开始",
        "下周首个交易日夜盘时间",
        "数据质量",
        "状态",
        "失败原因",
    ]
    if not review_rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(
        [
            {
                "周次": row.get("week_id"),
                "股票": row.get("ticker"),
                "Binance 合约": row.get("binance_symbol") or "暂无映射",
                "本周最后交易日": row.get("last_trading_day") or "未返回",
                "常规收盘价": row.get("regular_close_price"),
                "最后交易日盘后收盘价": row.get("friday_afterhours_close"),
                "P0较常规%": row.get("p0_vs_regular_close_pct"),
                "P0 来源": _price_source_text(row.get("friday_afterhours_provider")),
                "P0 请求区间": row.get("p0_request_window") or "未返回",
                "P0 endpoint": row.get("p0_endpoint") or "未返回",
                "P0 返回bars": row.get("p0_returned_bar_count"),
                "P0 选中时间": row.get("p0_selected_bar_time") or "未返回",
                "P0 选中close": row.get("p0_selected_bar_close"),
                "P0 volume": row.get("p0_selected_bar_volume"),
                "P0 quality": _price_source_text(row.get("p0_quality")),
                "P0 fallback": "是" if row.get("p0_is_fallback") else "否",
                "P0失败原因": row.get("p0_failure_reason") or "",
                "P1 窗口": row.get("binance_window") or "未返回",
                "Binance 周末最高价": row.get("binance_price"),
                "Binance 高点时间": row.get("contract_sample_time") or "未返回",
                "下周首个交易日夜盘首分钟收盘": row.get("broker_open_close"),
                "P2 来源": _price_source_text(row.get("overnight_provider")),
                "P2 下周首个交易日夜盘开始": row.get("p2_session_start_et") or "未返回",
                "下周首个交易日夜盘时间": row.get("broker_first_time") or "未返回",
                "数据质量": _data_quality_text(row.get("data_quality")),
                "状态": row.get("status"),
                "失败原因": row.get("failure_reason") or "",
            }
            for row in review_rows
        ],
        columns=columns,
    )


def _style_weekend_review_frame(frame: pd.DataFrame):
    def color_value(value: object) -> str:
        number = _number(value)
        if number is None:
            return "color: #94a3b8;"
        if number > 0.05:
            return "color: #c2410c; font-weight: 800;"
        if number < -0.05:
            return "color: #047857; font-weight: 800;"
        return "color: #64748b; font-weight: 700;"

    def money_format(value: object) -> str:
        number = _number(value)
        if number is not None:
            return _money_text(number)
        text = str(value or "").strip()
        return text if text else "缺数据"

    def percent_format(value: object) -> str:
        number = _number(value)
        if number is not None:
            return _review_percent_text(number)
        text = str(value or "").strip()
        return text if text else "待计算"

    money_columns = [
        "P0 最后交易日盘后",
        "P1 Binance 高点",
        "P2 下周首个交易日夜盘",
        "常规收盘价",
        "最后交易日盘后收盘价",
        "P0 选中close",
        "Binance 周末最高价",
        "下周首个交易日夜盘首分钟收盘",
    ]
    percent_columns = [
        "周末冲高%",
        "高点回落%",
        "最终传导%",
        "兑现率%",
        "P0较常规%",
        "Binance 周末冲高%",
        "夜盘相对 Binance 高点%",
        "夜盘相对最后交易日盘后%",
        "周末高点兑现率%",
    ]
    formatters = {column: money_format for column in money_columns if column in frame.columns}
    formatters.update({column: percent_format for column in percent_columns if column in frame.columns})
    styler = frame.style.format(formatters)
    color_subset = [column for column in percent_columns if column in frame.columns]
    if not color_subset:
        return styler
    if hasattr(styler, "map"):
        return styler.map(color_value, subset=color_subset)

    def color_frame(data: pd.DataFrame) -> pd.DataFrame:
        if hasattr(data, "map"):
            return data.map(color_value)
        return data.applymap(color_value)

    return styler.apply(color_frame, subset=color_subset, axis=None)


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
        return "降级观察"
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
        return "｜".join(labels)
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
        return "P0 盘后价格缺少可验证的原始 1m bar 证据，仅作观察"
    if quality == "TRADINGVIEW_WEBHOOK_SAMPLE":
        return "TradingView Webhook 样本"
    if quality == "TRADINGVIEW_CSV_SAMPLE":
        return "TradingView CSV 样本"
    if quality == "MANUAL_BROKER_SAMPLE":
        return "人工券商样本"
    if quality == "MANUAL_AFTERHOURS_SAMPLE":
        return "人工盘后样本"
    if quality in {"MISSING_OVERNIGHT_FIRST_1M", "OVERNIGHT_PROVIDER_MISSING", "REGULAR_CLOSE_FALLBACK", "FALLBACK_REGULAR_CLOSE", "P0_UNVERIFIED", "NO_AFTERHOURS_CLOSE", "OBSERVE_ONLY"}:
        return _weekend_review_sample_status_with_context("观察样本", raw)
    if quality.startswith("DEGRADED"):
        return _weekend_review_sample_status_with_context("降级样本", raw)
    return _weekend_review_sample_status_with_context("排除样本", raw)


def _weekend_review_sample_status_with_context(base: str, row: dict) -> str:
    labels: list[str] = []
    if not bool(row.get("last_trading_day_is_friday", True)):
        labels.append("周五休市，使用本周最后交易日")
    if bool(row.get("holiday_shifted_overnight_session") or row.get("holiday_rollover")) and "夜盘顺延" not in base:
        labels.append("夜盘顺延")
    if not labels:
        return base
    labels.append(base)
    return "｜".join(labels)


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
    for key in ("regular_close_date", "friday_close_date", "anchor_ts", "weekend_window_start"):
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
        "weekend_peak_time",
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
        "P0_UNVERIFIED": "P0 待验证",
        "FMP_AFTERHOURS_1M_BAR": "FMP 1m 盘后",
        "FMP_AFTERHOURS_QUOTE_ANCHOR": "FMP quote 盘后锚点",
        "ALPACA_AFTERHOURS_1M_BAR": "Alpaca 1m 盘后",
        "TRADINGVIEW_WEBHOOK": "TradingView Webhook",
        "TRADINGVIEW_CSV": "TradingView CSV",
        "MANUAL_OVERNIGHT_1M": "人工券商",
        "MANUAL_AFTERHOURS_1M": "人工盘后",
        "REGULAR_CLOSE_FALLBACK": "常规收盘回退",
        "FMP": "FMP 盘后",
        "FMP_AFTERHOURS_TRADE": "FMP 盘后",
        "FMP_AFTERHOURS_QUOTE_MID": "FMP 盘后",
        "ALPACA_AFTERHOURS": "Alpaca 盘后",
        "ALPACA_AFTERHOURS_SIP": "Alpaca SIP 盘后",
        "ALPACA_AFTERHOURS_BOATS": "Alpaca BOATS 盘后",
        "ALPACA_AFTERHOURS_IEX": "Alpaca IEX 盘后",
        "ALPACA_BOATS": "Alpaca BOATS",
        "IBKR_OVERNIGHT": "IBKR 夜盘",
    }.get(upper, text)


def _weekend_review_failure_reason(row: dict, data_quality: str) -> str:
    quality = str(data_quality or "").strip().upper()
    if quality == "OK":
        return ""
    if quality == "OBSERVE_ONLY":
        return "映射未确认，仅作观察，不计入正式样本"
    if quality == "NO_AFTERHOURS_CLOSE":
        return str(row.get("friday_afterhours_reason") or row.get("afterhours_missing_reason") or "缺少本周最后交易日盘后收盘价")
    if quality == "OVERNIGHT_PROVIDER_MISSING":
        return "美股夜盘数据源未配置"
    if quality in {"REGULAR_CLOSE_FALLBACK", "FALLBACK_REGULAR_CLOSE"}:
        return "常规收盘回退，仅观察"
    if quality == "BOATS_DELAY_PENDING":
        return "BOATS 历史数据可能延迟，请 15 分钟后重试。"
    if quality == "ALPACA_BOATS_PERMISSION":
        return "Alpaca BOATS 权限不足，可能需要 Algo Trader Plus。"
    if quality == "MISSING_BOATS_FIRST_1M":
        return "缺少下周第一个交易日 BOATS 夜盘首分钟 1m K线。"
    if quality == "PROVIDER_ERROR":
        return str(row.get("overnight_reason") or "夜盘 provider 报错。")
    if quality == "TRADINGVIEW_WEBHOOK_SAMPLE":
        return "P0/P2 来自 TradingView Webhook，本地补数样本"
    if quality == "TRADINGVIEW_CSV_SAMPLE":
        return "P0/P2 来自 TradingView CSV，本地导入样本"
    if quality == "MANUAL_BROKER_SAMPLE":
        return "P2 来自人工录入券商价格"
    if quality == "MANUAL_AFTERHOURS_SAMPLE":
        return "P0 来自人工录入盘后价格"
    if quality == "MISSING_OVERNIGHT_FIRST_1M":
        return str(row.get("overnight_reason") or "缺少下周第一个交易日美股夜盘首分钟 1m K线")
    if quality == "CONTRACT_MISSING":
        return str(row.get("binance_weekend_max_reason") or row.get("error_message") or "缺少 Binance 周末 1m K 线")
    if quality == "STOCK_MISSING":
        return str(row.get("stock_bar_reason") or row.get("error_message") or "缺少下周第一个交易日券商夜盘首分钟价格")
    if quality == "MAPPING_MISSING":
        return "映射未确认或未配置"
    return str(row.get("warning") or row.get("error_message") or row.get("stock_bar_reason") or row.get("binance_weekend_max_reason") or quality)


def _weekend_review_anchor_source(row: dict) -> str:
    source = str(row.get("anchor_source") or row.get("afterhours_reference_source") or "").strip().upper()
    if source in {"ANCHOR_REGULAR_CLOSE_ONLY", "REGULAR_CLOSE", "REGULAR_CLOSE_FALLBACK"}:
        return "常规收盘锚点"
    if row.get("afterhours_reference_price") is not None or "AFTERHOURS" in source or "AFTERMARKET" in source:
        return "盘后锚点价"
    if source:
        return _data_quality_text(source)
    return "美股锚点价"


def _weekend_review_binance_window(row: dict) -> str:
    start = _weekend_review_short_time(row.get("binance_window_start_et") or row.get("weekend_window_start"))
    end = _weekend_review_short_time(row.get("binance_window_end_et") or row.get("weekend_window_end"))
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
    for key in ("weekend_peak_premium_pct", "primary_spread_pct", "spread_pct"):
        pct = _number(row.get(key))
        if pct is not None:
            return pct
    binance_price = _first_number(
        row,
        (
            "binance_weekend_max_price",
            "oracle_weekend_high_bid",
            "weekend_peak_binance_price",
            "weekend_peak_price",
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
            "weekend_peak_binance_price",
            "weekend_peak_price",
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
    rows = list(st.session_state.get("weekend_realtime_rows") or [])
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
                reason = str(row.get("afterhours_missing_reason") or "未抓取盘后价").strip()
                reasons[reason] = reasons.get(reason, 0) + 1
    if total <= 0:
        return "历史盘后锚点：未找到可读取的历史最后交易日收盘记录。"
    note = f"历史盘后锚点：已读取 {afterhours}/{total}，其中缓存 {cache}；回退正常收盘 {fallback}。"
    if fallback and reasons:
        primary = sorted(reasons.items(), key=lambda item: item[1], reverse=True)[0]
        note += f"主要回退原因：{_afterhours_reason_text(primary[0])}（{primary[1]} 周）。"
    return note


def _render_backtest_advanced_records() -> None:
    with st.expander("高级 / 前瞻记录", expanded=False):
        st.caption("这些记录用于前瞻观察和下周首个交易日验证，不是历史回测主流程。")
        rows = list(st.session_state.get("weekend_realtime_rows") or [])
        if rows:
            _render_record_buttons(rows, key_prefix="advanced")
        else:
            st.caption("先到“实时观察”刷新或加载一次价差，再记录当前快照。")
        snapshot = get_weekly_log_snapshot()
        _render_weekly_peak_cards(snapshot)
        summaries = list(snapshot.get("summaries") or [])
        if summaries:
            st.dataframe(_summary_frame(summaries), width="stretch", hide_index=True)
            st.dataframe(_monday_outcome_frame(summaries), width="stretch", hide_index=True)
        stats = build_history_stats()
        if stats:
            st.dataframe(_history_frame(stats), width="stretch", hide_index=True)


def _render_paper_trade_area(rows: list[dict], mapping: dict[str, dict]) -> None:
    with st.expander("手动交易记录（可选） / Paper Trade", expanded=False):
        st.caption("只做手动记录、状态流转和复盘，不连接真实下单 API，不输出套利、买卖或对冲指令。")
        opportunities = _paper_opportunities(rows, mapping)
        if not opportunities:
            st.info("当前没有可展示的 basis 机会。请先配置 Binance 合约映射并刷新价格。")
            return
        best = max(opportunities, key=lambda item: abs(float(item.get("entry_premium_bps") or 0)))
        cols = st.columns(5)
        cols[0].metric("当前状态", _basis_status_text(best.get("status")))
        cols[1].metric("当前最优", str(best.get("ticker") or ""))
        cols[2].metric("entry premium", _bps_text(best.get("entry_premium_bps")))
        cols[3].metric("expected locked", _bps_text(best.get("expected_net_locked_bps")))
        cols[4].metric("warning", str(best.get("warning") or ""))
        st.dataframe(_paper_opportunity_frame(opportunities), width="stretch", hide_index=True)

        trades = load_weekend_basis_trades()
        if trades:
            st.dataframe(_paper_trade_frame(trades), width="stretch", hide_index=True)

        selected = st.selectbox(
            "Paper Trade ticker",
            [str(item.get("ticker") or "") for item in opportunities],
            key="weekend_basis_paper_ticker",
        )
        selected_opp = next((item for item in opportunities if str(item.get("ticker") or "") == selected), opportunities[0])
        col_entry, col_hedge, col_exit = st.columns(3)
        with col_entry:
            st.markdown("**Entry Plan**")
            st.caption(f"Binance 空单限价 ≥ {_money_text(selected_opp.get('min_binance_short_price'))}")
            st.caption(f"当前 bid：{_money_text(selected_opp.get('binance_entry_bid'))}")
            short_price = st.number_input(
                "记录 Binance short 成交价",
                min_value=0.0,
                value=float(_number(selected_opp.get("binance_entry_bid")) or 0.0),
                step=0.01,
                key="weekend_basis_short_price",
            )
            short_qty = st.number_input(
                "Binance short qty",
                min_value=0.0,
                value=0.0,
                step=1.0,
                key="weekend_basis_short_qty",
            )
            if st.button("记录 Binance 空单", width="stretch", key="weekend_basis_record_short"):
                trade = create_weekend_basis_trade(
                    week_id=_paper_week_id(selected_opp),
                    ticker=str(selected_opp.get("ticker") or ""),
                    mapping=mapping.get(str(selected_opp.get("ticker") or "").upper(), {}),
                    broker_anchor_price=float(_number(selected_opp.get("broker_anchor_price")) or 0.0),
                    binance_entry_bid=short_price,
                    binance_entry_ask=_number(selected_opp.get("binance_entry_ask")),
                    binance_short_qty=short_qty,
                    entry_premium_bps=_number(selected_opp.get("entry_premium_bps")),
                    warning=str(selected_opp.get("warning") or ""),
                )
                upsert_weekend_basis_trade(trade)
                st.success("已记录 Binance 空单，状态 SHORT_OPEN。")
        active_trades = [trade for trade in load_weekend_basis_trades() if str(trade.get("status") or "") not in {"CLOSED", "FAILED"}]
        selected_trade = active_trades[-1] if active_trades else {}
        with col_hedge:
            st.markdown("**Hedge Plan**")
            if selected_trade:
                st.caption(f"Broker 买入限价 ≤ {_money_text(selected_opp.get('max_broker_buy_price'))}")
                hedge_ask = st.number_input("记录 broker hedge ask", min_value=0.0, value=0.0, step=0.01, key="weekend_basis_hedge_ask")
                hedge_bid = st.number_input("记录 broker hedge bid", min_value=0.0, value=0.0, step=0.01, key="weekend_basis_hedge_bid")
                broker_shares = st.number_input("broker shares", min_value=0.0, value=0.0, step=1.0, key="weekend_basis_broker_shares")
                if st.button("记录 Broker 买入对冲", width="stretch", key="weekend_basis_record_hedge"):
                    updated = record_broker_hedge(
                        selected_trade,
                        broker_hedge_ask=hedge_ask,
                        broker_hedge_bid=hedge_bid or None,
                        broker_shares=broker_shares or None,
                        binance_same_min_bid=_number(selected_opp.get("binance_entry_bid")),
                        binance_same_min_ask=_number(selected_opp.get("binance_entry_ask")),
                    )
                    upsert_weekend_basis_trade(updated)
                    st.success("已记录 broker hedge，状态 HEDGE_LOCKED。")
            else:
                st.caption("先记录 Binance 空单后，才会进入 hedge 记录。")
        with col_exit:
            st.markdown("**Exit Plan**")
            if selected_trade:
                st.caption("只有双腿都退出后才计算 realized_pnl。")
                binance_exit = st.number_input("Binance exit ask", min_value=0.0, value=0.0, step=0.01, key="weekend_basis_exit_ask")
                broker_exit = st.number_input("Broker exit bid", min_value=0.0, value=0.0, step=0.01, key="weekend_basis_exit_bid")
                if st.button("记录双腿退出", width="stretch", key="weekend_basis_record_exit"):
                    closed = close_weekend_basis_trade(selected_trade, binance_exit_ask=binance_exit, broker_exit_bid=broker_exit)
                    upsert_weekend_basis_trade(closed)
                    st.success("已记录双腿退出，状态 CLOSED。")
            else:
                st.caption("暂无打开中的 paper trade。")


def _render_mapping_tab(rows: list[dict], mapping: dict[str, dict], mapping_counts: dict[str, int]) -> None:
    st.subheader("映射管理")
    status_counts = _mapping_management_counts(rows, mapping)
    cols = st.columns(5)
    cols[0].metric("本地 mapping 总数", status_counts["local_mapping_count"])
    cols[1].metric("观察池 mapping 数", status_counts["universe_mapping_count"])
    cols[2].metric("confirmed", status_counts["confirmed_count"])
    cols[3].metric("candidate", status_counts["candidate_count"])
    cols[4].metric("无 mapping", status_counts["no_mapping_count"])
    st.info(
        "价格由 Binance API 自动读取；用户只维护股票代码到 Binance 合约代码的映射。"
        f"local 配置路径：{DEFAULT_LOCAL_MAPPING_PATH.as_posix()}，local 不提交 git，candidate 不等于 confirmed。"
    )
    st.dataframe(_mapping_management_frame(rows, mapping), width="stretch", hide_index=True)
    _render_mapping_editor(mapping, rows, mapping_counts, DEFAULT_LOCAL_MAPPING_PATH)
    _render_mapping_audit_area(rows, mapping, DEFAULT_LOCAL_MAPPING_PATH)
    _render_mapping_diagnostics(mapping)


def _render_mapping_tab(rows: list[dict], mapping: dict[str, dict], mapping_counts: dict[str, int]) -> None:
    st.subheader("映射管理")
    records = _mapping_management_records(rows, mapping)
    off_universe_records = _off_universe_mapping_records(rows, mapping)
    status_counts = _mapping_management_counts(rows, mapping)
    cols = st.columns(5)
    cols[0].metric("可用映射", status_counts["usable_count"])
    cols[1].metric("需处理", status_counts["review_count"])
    cols[2].metric("无映射", status_counts["no_mapping_count"])
    cols[3].metric("人工锁定", status_counts["manual_locked_count"])
    cols[4].metric("观察池覆盖数", status_counts["universe_mapping_count"])
    st.info(
        "系统会自动匹配 Binance 合约。价格可用且偏差正常的映射会自动参与回测；只有价格异常或合约无效时才需要人工处理。"
        f"\n\nlocal 配置路径：{DEFAULT_LOCAL_MAPPING_PATH.as_posix()}，local 不提交 git。"
    )
    if off_universe_records:
        st.caption(
            f"已隐藏 {len(off_universe_records)} 条不在当前观察名单的本地映射；它们不会参与周末价差回测。"
        )
    adoptable = [record for record in records if record.get("state_key") in {"auto_available", "auto_unchecked"}]
    if st.button("一键采用全部自动可用映射", width="stretch", key="weekend_adopt_all_auto_mappings", disabled=not bool(adoptable)):
        updated_count = _adopt_mapping_records(adoptable, DEFAULT_LOCAL_MAPPING_PATH)
        st.success(f"已人工锁定 {updated_count} 条自动可用映射。刷新页面后生效。")
    show_all = st.checkbox("显示全部映射", value=False, key="weekend_mapping_show_all")
    visible_records = records if show_all else [record for record in records if record.get("state_group") in {"review", "missing"}]
    if not visible_records:
        st.success("当前没有需要人工处理的映射。自动可用和人工锁定映射会直接参与回测。")
    else:
        st.dataframe(_mapping_management_frame(visible_records), width="stretch", hide_index=True)
    _render_mapping_actions(records, DEFAULT_LOCAL_MAPPING_PATH)
    _render_mapping_editor(mapping, rows, mapping_counts, DEFAULT_LOCAL_MAPPING_PATH)
    if off_universe_records:
        with st.expander("查看已移出观察名单的本地映射", expanded=False):
            st.dataframe(_mapping_management_frame(off_universe_records), width="stretch", hide_index=True)
    _render_mapping_diagnostics(mapping)


def _render_mapping_audit_area(rows: list[dict], mapping: dict[str, dict], local_mapping_path: Path) -> None:
    with st.expander("Mapping Audit / 映射确认", expanded=False):
        st.caption(
            "Audit 只会把候选映射标记为 verified_ready。只有你点击确认后，local mapping 才会写入 confirmed。"
        )
        mapped_tickers = [
            str(row.get("ticker") or "").strip().upper()
            for row in rows
            if str(row.get("ticker") or "").strip().upper() in mapping
            and mapping.get(str(row.get("ticker") or "").strip().upper(), {}).get("binance_symbol")
        ]
        mapped_tickers = list(dict.fromkeys([ticker for ticker in mapped_tickers if ticker]))
        col_run, col_count = st.columns([1, 2])
        if col_run.button("运行 Mapping Audit", width="stretch", key="weekend_mapping_audit_run"):
            if not mapped_tickers:
                st.session_state["weekend_mapping_audit_rows"] = []
                st.warning("当前观察池没有可审计的 local mapping。")
            else:
                with st.spinner("正在校验 Binance symbol、价格比例、周末数据和流动性..."):
                    st.session_state["weekend_mapping_audit_rows"] = audit_weekend_basis_mappings(
                        mapped_tickers,
                        mapping=mapping,
                    )
        col_count.caption(f"待审计映射：{len(mapped_tickers)}")
        audit_rows = list(st.session_state.get("weekend_mapping_audit_rows") or [])
        if not audit_rows:
            st.info("尚未运行 Mapping Audit。candidate / verified_ready 都不会进入 Backfill strict statistics。")
            return
        st.dataframe(_mapping_audit_frame(audit_rows), width="stretch", hide_index=True)
        for audit_row in audit_rows:
            ticker = str(audit_row.get("ticker") or "").strip().upper()
            if not ticker:
                continue
            status = str(audit_row.get("audit_status") or "").strip().lower()
            left, right = st.columns(2)
            if status == "verified_ready":
                if left.button(f"确认 {ticker} mapping", key=f"weekend_mapping_confirm_{ticker}", width="stretch"):
                    confirm_weekend_basis_mapping(ticker, audit_row, path=local_mapping_path, confirmed_by="manual_ui")
                    st.success(f"{ticker} 已写入 confirmed。刷新页面后可进入 Backfill strict statistics。")
            if right.button(f"标记 {ticker} rejected", key=f"weekend_mapping_reject_{ticker}", width="stretch"):
                reject_weekend_basis_mapping(ticker, audit_row, path=local_mapping_path, rejected_by="manual_ui")
                st.warning(f"{ticker} 已标记 rejected；请修正 symbol / multiplier 后重新审计。")


def _mapping_audit_frame(rows: list[dict]) -> pd.DataFrame:
    columns = [
        ("ticker", "ticker"),
        ("broker_symbol", "broker_symbol"),
        ("binance_symbol", "binance_symbol"),
        ("current_confidence", "current_confidence"),
        ("audit_status", "audit_status"),
        ("median_ratio", "median_ratio"),
        ("median_abs_deviation_bps", "median_abs_deviation_bps"),
        ("max_abs_deviation_bps", "max_abs_deviation_bps"),
        ("sample_count", "sample_count"),
        ("weekend_data_ok", "weekend_data_ok"),
        ("liquidity_status", "liquidity_status"),
        ("warning", "warning"),
        ("action", "action"),
    ]
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=[label for _, label in columns])
    display = pd.DataFrame()
    for key, label in columns:
        display[label] = frame.get(key)
    for key in ("median_ratio", "median_abs_deviation_bps", "max_abs_deviation_bps"):
        display[key] = display[key].map(_plain_decimal_text)
    return display


def _filter_rows(
    rows: list[dict],
    *,
    scope: str,
    confirmed_only: bool,
    focus_only: bool,
    abnormal_only: bool,
) -> list[dict]:
    if scope == "暂无 mapping":
        result = [row for row in rows if not row.get("binance_symbol")]
    elif scope == "全部观察池":
        result = list(rows)
    else:
        result = _default_live_rows(rows)
    if confirmed_only:
        result = [row for row in result if row.get("mapping_confidence") == "confirmed"]
    if abnormal_only:
        result = [row for row in result if row.get("alert_level") == "ABNORMAL"]
    elif focus_only:
        result = [row for row in result if row.get("alert_level") in {"FOCUS", "ABNORMAL"}]
    return result


def _paper_opportunities(rows: list[dict], mapping: dict[str, dict]) -> list[dict]:
    opportunities: list[dict] = []
    now = datetime.now(timezone.utc)
    for row in rows:
        ticker = str(row.get("ticker") or "").strip().upper()
        if not ticker or not row.get("binance_symbol"):
            continue
        bid = _number(row.get("binance_bid"))
        ask = _number(row.get("binance_ask"))
        quote_rows = []
        if bid is not None and ask is not None:
            quote_rows.append(
                {
                    "ts": _parse_utc_time(row.get("updated_at")) or now,
                    "bid": bid,
                    "ask": ask,
                    "depth_usd": row.get("binance_volume_24h"),
                    "source": row.get("source") or "weekend_spread_row",
                }
            )
        anchor = _number(row.get("afterhours_reference_price") or row.get("regular_close_price") or row.get("friday_close"))
        opportunity = build_basis_opportunity(
            ticker=ticker,
            mapping=mapping.get(ticker, {}),
            broker_anchor_price=anchor,
            binance_quotes=quote_rows,
            now=now,
        )
        opportunity.update(
            {
                "week_id": _paper_week_id(row),
                "mapping_status": row.get("mapping_confidence") or opportunity.get("mapping_status"),
                "data_quality": opportunity.get("data_quality") or row.get("status"),
                "time_to_broker_open": row.get("time_to_broker_open") or "",
            }
        )
        opportunities.append(opportunity)
    return opportunities


def _paper_opportunity_frame(rows: list[dict]) -> pd.DataFrame:
    columns = [
        ("ticker", "ticker"),
        ("status", "status"),
        ("entry_premium_bps", "entry_premium_bps"),
        ("relative_high_rank", "relative_high_rank"),
        ("pullback_bps", "pullback_bps"),
        ("min_binance_short_price", "min_binance_short_price"),
        ("max_broker_buy_price", "max_broker_buy_price"),
        ("expected_net_locked_bps", "expected_net_locked_bps"),
        ("time_to_broker_open", "time_to_broker_open"),
        ("mapping_status", "mapping_status"),
        ("data_quality", "data_quality"),
        ("warning", "warning"),
    ]
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=[label for _, label in columns])
    display = pd.DataFrame()
    for key, label in columns:
        display[label] = frame.get(key)
    display["status"] = display["status"].map(_basis_status_text)
    for col in ("entry_premium_bps", "pullback_bps", "expected_net_locked_bps"):
        display[col] = display[col].map(_bps_text)
    display["relative_high_rank"] = display["relative_high_rank"].map(_percent_text)
    for col in ("min_binance_short_price", "max_broker_buy_price"):
        display[col] = display[col].map(_money_text)
    display["data_quality"] = display["data_quality"].map(_data_quality_text)
    return display


def _paper_trade_frame(rows: list[dict]) -> pd.DataFrame:
    columns = [
        ("trade_id", "trade_id"),
        ("ticker", "ticker"),
        ("status", "status"),
        ("binance_entry_price", "binance_entry_price"),
        ("broker_hedge_price", "broker_hedge_price"),
        ("net_locked_bps", "net_locked_bps"),
        ("residual_basis_bps", "residual_basis_bps"),
        ("realized_pnl_bps", "realized_pnl_bps"),
        ("updated_at", "updated_at"),
    ]
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=[label for _, label in columns])
    display = pd.DataFrame()
    for key, label in columns:
        display[label] = frame.get(key)
    display["status"] = display["status"].map(_basis_status_text)
    for col in ("binance_entry_price", "broker_hedge_price"):
        display[col] = display[col].map(_money_text)
    for col in ("net_locked_bps", "residual_basis_bps", "realized_pnl_bps"):
        display[col] = display[col].map(_bps_text)
    display["updated_at"] = display["updated_at"].map(_short_hkt_time)
    return display


def _paper_week_id(row: dict) -> str:
    date_text = str(row.get("friday_close_date") or row.get("regular_close_date") or "").strip()
    if date_text:
        try:
            parsed = datetime.fromisoformat(date_text)
            iso = parsed.date().isocalendar()
            return f"{iso.year}-W{iso.week:02d}"
        except ValueError:
            pass
    iso = datetime.now(timezone.utc).date().isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _default_live_rows(rows: list[dict]) -> list[dict]:
    filtered = [
        row
        for row in rows
        if row.get("binance_symbol") or row.get("spread_pct") is not None or row.get("alert_level") in {"FOCUS", "ABNORMAL"}
    ]
    return sorted(filtered, key=_realtime_sort_key)


def _realtime_sort_key(row: dict) -> tuple[int, float, str]:
    priority = {"focus": 0, "review": 1, "normal": 2, "unavailable": 3}
    spread = _number(row.get("spread_vs_afterhours_pct"))
    if spread is None:
        spread = _number(row.get("spread_pct"))
    return (priority.get(_realtime_row_status_key(row), 4), -(abs(spread) if spread is not None else -1), str(row.get("ticker") or ""))


def _realtime_status_counts(rows: list[dict]) -> dict[str, int]:
    counts = {"normal": 0, "focus": 0, "review": 0, "unavailable": 0}
    for row in rows:
        key = _realtime_row_status_key(row)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _realtime_row_status_key(row: dict) -> str:
    status = str(row.get("status") or "").upper()
    spread = _number(row.get("spread_vs_afterhours_pct"))
    if spread is None:
        spread = _number(row.get("spread_pct"))
    has_price = _number(row.get("adjusted_binance_price") or row.get("binance_last_price")) is not None
    has_anchor = _number(row.get("afterhours_reference_price")) is not None
    if status in {"INVALID_SYMBOL", "BINANCE_UNAVAILABLE", "PRICE_UNAVAILABLE", "PRICE_NOT_LOADED"} or not has_price or not has_anchor:
        return "unavailable"
    if _row_mapping_needs_review(row) or _row_anchor_needs_review(row):
        return "review"
    if spread is not None and abs(spread) >= 8:
        return "review"
    if spread is not None and abs(spread) >= 2:
        return "focus"
    return "normal"


def _realtime_row_status_label(row: dict) -> str:
    return {
        "normal": "正常",
        "focus": "重点关注",
        "review": "异常复核",
        "unavailable": "不可用",
    }.get(_realtime_row_status_key(row), "待复核")


def _realtime_row_status_reason(row: dict) -> str:
    key = _realtime_row_status_key(row)
    if key == "focus":
        return "偏离超过观察阈值，数据质量可用，值得优先查看。"
    if key == "review":
        if _row_mapping_needs_review(row):
            return "偏离较大或映射未完全确认，请先复核 Binance 映射。"
        if _row_anchor_needs_review(row):
            return "偏离较大或盘后锚点质量不足，请先复核锚点来源。"
        return "偏离过大，需要确认映射、锚点和行情源。"
    if key == "unavailable":
        if _number(row.get("afterhours_reference_price")) is None:
            return "缺少盘后锚点，暂不能判断相对盘后偏离。"
        return "Binance 价格不可用或合约无效。"
    return "映射、锚点和 Binance 价格可用，偏离未超过重点阈值。"


def _mapping_display_label_for_row(row: dict) -> str:
    confidence = str(row.get("mapping_confidence") or "").strip().lower()
    risk_note = str(row.get("mapping_risk") or row.get("risk_note") or "")
    if confidence == "confirmed":
        return "人工锁定"
    if confidence in {"candidate", "auto_available"}:
        if "ticker+USDT" in risk_note or "自动生成" in risk_note:
            return "自动可用"
        return "自动匹配"
    if confidence in {"unverified", "verified_ready", "stale"}:
        return "需复核"
    if confidence == "rejected":
        return "无映射"
    return "自动匹配" if row.get("binance_symbol") else "无映射"


def _anchor_display_label_for_row(row: dict) -> str:
    if _number(row.get("afterhours_reference_price")) is not None:
        if str(row.get("primary_spread_anchor") or "") == "REGULAR_CLOSE_FALLBACK":
            return "常规收盘回退"
        if str(row.get("afterhours_anchor_status") or "") == "FINAL":
            return "盘后锚点"
        return "盘后锚点待验证"
    if row.get("binance_symbol"):
        return "锚点缺失"
    return "未请求"


def _row_mapping_needs_review(row: dict) -> bool:
    return _mapping_display_label_for_row(row) in {"需复核", "无映射"}


def _row_anchor_needs_review(row: dict) -> bool:
    return _anchor_display_label_for_row(row) in {"常规收盘回退", "盘后锚点待验证", "锚点缺失"}


def _mapping_counts(rows: list[dict], mapping: dict[str, dict]) -> dict[str, int]:
    local_mapping_count = sum(1 for item in mapping.values() if item.get("enabled", True) and item.get("binance_symbol"))
    universe_mapping_count = sum(1 for row in rows if row.get("binance_symbol"))
    price_row_count = sum(
        1
        for row in rows
        if row.get("binance_symbol") and row.get("status") == "OK" and row.get("spread_pct") is not None
    )
    return {
        "local_mapping_count": local_mapping_count,
        "universe_mapping_count": universe_mapping_count,
        "price_row_count": price_row_count,
        "universe_total": len(rows),
    }


def _afterhours_counts(rows: list[dict]) -> dict[str, int]:
    mapped_rows = [row for row in rows if row.get("binance_symbol")]
    available = sum(1 for row in mapped_rows if row.get("afterhours_reference_price") is not None)
    return {
        "mapped": len(mapped_rows),
        "available": available,
        "missing": max(len(mapped_rows) - available, 0),
    }


def _afterhours_anchor_status_text(rows: list[dict], counts: dict[str, int]) -> str:
    mapped = int(counts.get("mapped") or 0)
    if mapped <= 0:
        return "无请求"
    mapped_rows = [row for row in rows if row.get("binance_symbol")]
    available = int(counts.get("available") or 0)
    final_count = sum(1 for row in mapped_rows if str(row.get("afterhours_anchor_status") or "") == "FINAL")
    provisional_count = sum(1 for row in mapped_rows if str(row.get("afterhours_anchor_status") or "") == "PROVISIONAL")
    fallback_count = sum(1 for row in mapped_rows if str(row.get("primary_spread_anchor") or "") == "REGULAR_CLOSE_FALLBACK")
    cache_count = sum(1 for row in mapped_rows if str(row.get("afterhours_cache_status") or "") in {"CACHE_HIT", "CACHE_FALLBACK"})
    parts = [f"可用 {available} / {mapped}"]
    if final_count:
        parts.append(f"FINAL {final_count}")
    if provisional_count:
        parts.append(f"PROVISIONAL {provisional_count}")
    if cache_count:
        parts.append(f"已缓存 {cache_count}")
    if fallback_count:
        parts.append(f"收盘回退 {fallback_count}")
    return "｜".join(parts)


def _mapping_record_from_row(row: dict, config: dict | None) -> dict:
    config = config or {}
    ticker = str(row.get("ticker") or "").upper()
    symbol = str(row.get("binance_symbol") or config.get("binance_symbol") or "").upper()
    binance_price = _number(row.get("adjusted_binance_price") or row.get("binance_last_price"))
    stock_ref = _mapping_stock_reference_price(row)
    diff_pct = abs(binance_price / stock_ref - 1.0) * 100.0 if binance_price is not None and stock_ref else None
    state_key, state_label, state_group = _mapping_state_for_row(row, config, symbol, binance_price, stock_ref, diff_pct)
    return {
        "ticker": ticker,
        "binance_symbol": symbol,
        "binance_price": binance_price,
        "stock_ref_price": stock_ref,
        "price_diff_pct": diff_pct,
        "state_key": state_key,
        "state_label": state_label,
        "state_group": state_group,
        "risk_note": str(row.get("mapping_risk") or config.get("risk_note") or ""),
        "config": dict(config),
        "row": dict(row),
    }


def _mapping_record_from_config(ticker: str, config: dict | None) -> dict:
    config = config or {}
    symbol = str(config.get("binance_symbol") or "").upper()
    if not config.get("enabled", True) or str(config.get("mapping_confidence") or "").lower() == "rejected":
        state_key, state_label, state_group = "missing", "无映射", "missing"
    elif str(config.get("mapping_confidence") or "").lower() == "confirmed":
        state_key, state_label, state_group = "manual_locked", "人工锁定", "locked"
    elif symbol:
        state_key, state_label, state_group = "needs_review", "需确认", "review"
    else:
        state_key, state_label, state_group = "missing", "无映射", "missing"
    return {
        "ticker": ticker,
        "binance_symbol": symbol,
        "binance_price": None,
        "stock_ref_price": None,
        "price_diff_pct": None,
        "state_key": state_key,
        "state_label": state_label,
        "state_group": state_group,
        "risk_note": str(config.get("risk_note") or ""),
        "config": dict(config),
        "row": {},
    }


def _off_universe_mapping_records(rows: list[dict], mapping: dict[str, dict]) -> list[dict]:
    universe_tickers = {str(row.get("ticker") or "").upper() for row in rows if row.get("ticker")}
    records: list[dict] = []
    for ticker, config in sorted((mapping or {}).items()):
        normalized = str(ticker or "").upper()
        if not normalized or normalized in universe_tickers:
            continue
        record = _mapping_record_from_config(normalized, config)
        record.update(
            {
                "state_key": "off_watchlist",
                "state_label": "已移出观察名单",
                "state_group": "off_watchlist",
            }
        )
        records.append(record)
    return records


def _mapping_state_for_row(
    row: dict,
    config: dict,
    symbol: str,
    binance_price: float | None,
    stock_ref: float | None,
    diff_pct: float | None,
) -> tuple[str, str, str]:
    confidence = str(config.get("mapping_confidence") or row.get("mapping_confidence") or "").strip().lower()
    if not config.get("enabled", True) or confidence == "rejected":
        return ("missing", "无映射", "missing")
    if confidence == "confirmed":
        return ("manual_locked", "人工锁定", "locked")
    status = str(row.get("status") or "").upper()
    if not symbol or status == "NO_MAPPING":
        return ("missing", "无映射", "missing")
    if status == "INVALID_SYMBOL" or binance_price is None:
        return ("missing", "无映射", "missing")
    if status == "UNIT_UNCONFIRMED":
        return ("needs_review", "需确认", "review")
    if stock_ref is None:
        return ("auto_unchecked", "自动可用，未做价格校验", "usable")
    if diff_pct is not None and diff_pct <= 30:
        return ("auto_available", "自动可用", "usable")
    return ("needs_review", "需确认", "review")


def _is_auto_mapping_config(config: dict | None) -> bool:
    config = config or {}
    confidence = str(config.get("mapping_confidence") or "").strip().lower()
    risk_note = str(config.get("risk_note") or "")
    return confidence == "candidate" and ("ticker+USDT" in risk_note or "自动生成" in risk_note)


def _mapping_stock_reference_price(row: dict) -> float | None:
    return _first_number(
        row,
        (
            "afterhours_reference_price",
            "regular_close_price",
            "friday_close",
            "friday_close_price",
        ),
    )


def _adopt_mapping_records(records: list[dict], path: Path) -> int:
    updated = 0
    for record in records:
        ticker = str(record.get("ticker") or "").upper()
        symbol = str(record.get("binance_symbol") or "").upper()
        if not ticker or not symbol:
            continue
        config = dict(record.get("config") or {})
        upsert_local_binance_symbol_mapping(
            ticker,
            symbol,
            market_type=str(config.get("market_type") or "usdm_futures"),
            mapping_confidence="confirmed",
            unit_multiplier=float(_number(config.get("unit_multiplier")) or 1.0),
            risk_note=str(config.get("risk_note") or "人工锁定映射"),
            path=path,
        )
        updated += 1
    return updated


def _render_mapping_actions(records: list[dict], local_mapping_path: Path) -> None:
    if not records:
        return
    with st.expander("处理映射", expanded=False):
        labels = [f"{record.get('ticker')} · {record.get('binance_symbol') or '无映射'} · {record.get('state_label')}" for record in records]
        selected_label = st.selectbox("选择股票", labels, key="weekend_mapping_action_target")
        selected = records[labels.index(selected_label)]
        ticker = str(selected.get("ticker") or "").upper()
        current_symbol = str(selected.get("binance_symbol") or "").upper()
        cols = st.columns([1, 1, 2])
        if cols[0].button("采用", key=f"weekend_mapping_adopt_{ticker}", width="stretch", disabled=not bool(current_symbol)):
            _adopt_mapping_records([selected], local_mapping_path)
            st.success(f"{ticker} 已设为人工锁定。刷新页面后生效。")
        if cols[1].button("忽略", key=f"weekend_mapping_ignore_{ticker}", width="stretch"):
            config = dict(selected.get("config") or {})
            symbol = current_symbol or f"{ticker}USDT"
            upsert_local_binance_symbol_mapping(
                ticker,
                symbol,
                market_type=str(config.get("market_type") or "usdm_futures"),
                mapping_confidence="rejected",
                unit_multiplier=float(_number(config.get("unit_multiplier")) or 1.0),
                risk_note="已忽略，不参与周末价差",
                enabled=False,
                path=local_mapping_path,
            )
            st.warning(f"{ticker} 已标记为不参与周末价差。刷新页面后生效。")
        new_symbol = cols[2].text_input("修改 Binance symbol", value=current_symbol, key=f"weekend_mapping_edit_symbol_{ticker}")
        if st.button("保存修改", key=f"weekend_mapping_save_symbol_{ticker}", width="stretch"):
            if not new_symbol.strip():
                st.error("请填写 Binance symbol。")
            else:
                config = dict(selected.get("config") or {})
                upsert_local_binance_symbol_mapping(
                    ticker,
                    new_symbol,
                    market_type=str(config.get("market_type") or "usdm_futures"),
                    mapping_confidence="confirmed",
                    unit_multiplier=float(_number(config.get("unit_multiplier")) or 1.0),
                    risk_note=str(config.get("risk_note") or "人工修改并锁定"),
                    path=local_mapping_path,
                )
                st.success(f"{ticker} 已修改并人工锁定为 {new_symbol.strip().upper()}。刷新页面后生效。")
        risk_note = str(selected.get("risk_note") or "").strip()
        if risk_note:
            st.caption(risk_note)


def _mapping_management_records(rows: list[dict], mapping: dict[str, dict]) -> list[dict]:
    return [_mapping_record_from_row(row, mapping.get(str(row.get("ticker") or "").upper(), {})) for row in rows]


def _mapping_management_counts(rows: list[dict], mapping: dict[str, dict]) -> dict[str, int]:
    counts = _mapping_counts(rows, mapping)
    records = _mapping_management_records(rows, mapping)
    off_universe_records = _off_universe_mapping_records(rows, mapping)
    counts.update(
        {
            "usable_count": sum(1 for record in records if record.get("state_group") in {"usable", "locked"}),
            "review_count": sum(1 for record in records if record.get("state_group") == "review"),
            "manual_locked_count": sum(1 for record in records if record.get("state_group") == "locked"),
            "no_mapping_count": sum(1 for record in records if record.get("state_group") == "missing"),
            "off_universe_mapping_count": len(off_universe_records),
        }
    )
    return counts


def _should_show_empty_mapping_state(mapping_counts: dict[str, int], scope: str) -> bool:
    return mapping_counts.get("universe_mapping_count", 0) <= 0 and scope != "暂无 mapping"


def _empty_mapping_message(mapping_counts: dict[str, int], local_mapping_path: Path) -> str:
    lines = [
        "当前观察池暂无 Binance 映射。",
        "Binance 价格可通过 API 自动读取，但需要先配置股票代码到 Binance 合约代码的映射。",
        f"本地配置文件：{local_mapping_path.as_posix()}",
        "示例：NVDA -> NVDAUSDT / usdm_futures / candidate",
    ]
    if mapping_counts.get("local_mapping_count", 0) > 0:
        lines.append("本地配置有 mapping，但不属于当前观察池。")
    return "\n\n".join(lines)


def _off_universe_mapping_note(mapping_counts: dict[str, int]) -> str:
    if mapping_counts.get("local_mapping_count", 0) <= 0:
        return "本地未配置映射"
    if mapping_counts.get("local_mapping_count", 0) > 0 and mapping_counts.get("universe_mapping_count", 0) == 0:
        return "本地配置有 mapping，但不属于当前观察池"
    return "本地 mapping 与观察池匹配正常"


def _render_empty_mapping_state(mapping_counts: dict[str, int], local_mapping_path: Path) -> None:
    st.info(_empty_mapping_message(mapping_counts, local_mapping_path))


def _render_no_mapping_expander(rows: list[dict]) -> None:
    no_mapping_rows = [row for row in rows if not row.get("binance_symbol")]
    if not no_mapping_rows:
        return
    with st.expander(f"查看暂无 mapping 股票（{len(no_mapping_rows)}）", expanded=False):
        st.dataframe(_no_mapping_frame(no_mapping_rows), width="stretch", hide_index=True)


def _render_mapping_editor(
    mapping: dict[str, dict],
    rows: list[dict],
    mapping_counts: dict[str, int],
    local_mapping_path: Path,
) -> None:
    expanded = mapping_counts.get("universe_mapping_count", 0) <= 0
    with st.expander("添加 / 更新 Binance 映射", expanded=expanded):
        st.caption("这里只保存 ticker -> Binance symbol 映射；实时价格仍由 Binance API 自动读取。")
        tickers = [str(row.get("ticker") or "").upper() for row in rows if row.get("ticker")]
        if not tickers:
            st.caption("观察池为空，暂无可配置 ticker。")
            return
        if st.button("一键生成观察池合约候选映射", width="stretch", key="weekend_default_mapping"):
            result = upsert_default_usdm_futures_mappings(tickers, path=local_mapping_path)
            st.success(
                f"已新增 {result['created']} 条候选映射，跳过已有 {result['skipped']} 条；"
                "默认格式为 TICKERUSDT，价格仍由 Binance API 自动读取。"
            )
            st.caption("如 Binance 未上线个别合约，后续会显示 symbol 无效，可单独修改。")
        ticker = st.selectbox("观察池 ticker", tickers, index=0, key="weekend_mapping_ticker")
        existing = mapping.get(str(ticker or "").upper(), {})
        symbol_value = str(existing.get("binance_symbol") or "")
        market_value = str(existing.get("market_type") or "usdm_futures")
        confidence_value = str(existing.get("mapping_confidence") or "candidate")
        market_options = ["usdm_futures"]
        confidence_options = ["candidate", "unverified", "verified_ready", "confirmed", "rejected", "stale"]
        symbol = st.text_input("Binance symbol", value=symbol_value, placeholder="例如 NVDAUSDT", key="weekend_mapping_symbol")
        market_type = st.selectbox(
            "市场类型",
            market_options,
            index=market_options.index(market_value) if market_value in market_options else 0,
            key="weekend_mapping_market",
        )
        mapping_confidence = st.selectbox(
            "映射置信",
            confidence_options,
            index=confidence_options.index(confidence_value) if confidence_value in confidence_options else 0,
            key="weekend_mapping_confidence",
        )
        unit_multiplier = st.number_input(
            "单位倍率",
            min_value=0.000001,
            value=float(existing.get("unit_multiplier") or 1),
            step=1.0,
            key="weekend_mapping_multiplier",
        )
        risk_note = st.text_input(
            "风险备注",
            value=str(existing.get("risk_note") or "候选 symbol 不代表真实美股映射关系，需要人工确认。"),
            key="weekend_mapping_risk_note",
        )
        if st.button("保存到 local mapping", width="stretch", key="weekend_mapping_save"):
            try:
                updated = upsert_local_binance_symbol_mapping(
                    str(ticker or ""),
                    symbol,
                    market_type=market_type,
                    mapping_confidence=mapping_confidence,
                    unit_multiplier=unit_multiplier,
                    risk_note=risk_note,
                    path=local_mapping_path,
                )
            except ValueError as exc:
                st.warning(_mapping_editor_error_text(str(exc)))
            else:
                st.success(f"已保存 {str(ticker).upper()} -> {symbol.strip().upper()}；刷新价格后将由 Binance API 自动读取。")
                st.caption(f"local mapping 当前共 {len(updated)} 条。")


def _render_mapping_editor(
    mapping: dict[str, dict],
    rows: list[dict],
    mapping_counts: dict[str, int],
    local_mapping_path: Path,
) -> None:
    expanded = mapping_counts.get("universe_mapping_count", 0) <= 0
    with st.expander("添加 / 更新 Binance 映射", expanded=expanded):
        st.caption("这里只维护股票代码到 Binance 合约代码的映射；价格仍由 Binance API 自动读取。")
        tickers = [str(row.get("ticker") or "").upper() for row in rows if row.get("ticker")]
        if not tickers:
            st.caption("观察池为空，暂无可配置股票。")
            return
        if st.button("一键生成观察池合约候选映射", width="stretch", key="weekend_default_mapping"):
            result = upsert_default_usdm_futures_mappings(tickers, path=local_mapping_path)
            st.success(
                f"已新增 {result['created']} 条自动候选映射，跳过已有 {result['skipped']} 条；"
                "默认格式为 TICKERUSDT，价格由 Binance API 自动读取。"
            )
        ticker = st.selectbox("观察池股票", tickers, index=0, key="weekend_mapping_ticker")
        existing = mapping.get(str(ticker or "").upper(), {})
        symbol_value = str(existing.get("binance_symbol") or "")
        market_value = str(existing.get("market_type") or "usdm_futures")
        confidence_value = str(existing.get("mapping_confidence") or "candidate")
        state_options = {
            "自动候选": "candidate",
            "人工锁定": "confirmed",
            "需确认": "verified_ready",
            "忽略": "rejected",
        }
        state_label = next((label for label, value in state_options.items() if value == confidence_value), "自动候选")
        state_labels = list(state_options)
        symbol = st.text_input("Binance 合约", value=symbol_value, placeholder="例如 NVDAUSDT", key="weekend_mapping_symbol")
        market_type = st.selectbox(
            "市场类型",
            ["usdm_futures"],
            index=0 if market_value == "usdm_futures" else 0,
            key="weekend_mapping_market",
        )
        selected_state = st.selectbox(
            "映射处理方式",
            state_labels,
            index=state_labels.index(state_label),
            key="weekend_mapping_confidence_cn",
        )
        unit_multiplier = st.number_input(
            "单位倍数",
            min_value=0.000001,
            value=float(existing.get("unit_multiplier") or 1),
            step=1.0,
            key="weekend_mapping_multiplier",
        )
        risk_note = st.text_input(
            "备注",
            value=str(existing.get("risk_note") or "自动生成映射，价格正常即可自动参与回测。"),
            key="weekend_mapping_risk_note",
        )
        if st.button("保存到 local mapping", width="stretch", key="weekend_mapping_save"):
            try:
                updated = upsert_local_binance_symbol_mapping(
                    str(ticker or ""),
                    symbol,
                    market_type=market_type,
                    mapping_confidence=state_options[selected_state],
                    unit_multiplier=unit_multiplier,
                    risk_note=risk_note,
                    enabled=selected_state != "忽略",
                    path=local_mapping_path,
                )
            except ValueError as exc:
                st.warning(_mapping_editor_error_text(str(exc)))
            else:
                st.success(f"已保存 {str(ticker).upper()} -> {symbol.strip().upper()}。刷新页面后生效。")
                st.caption(f"local mapping 当前共 {len(updated)} 条。")


def _mapping_editor_error_text(error_code: str) -> str:
    return {
        "ticker_required": "请选择观察池 ticker。",
        "binance_symbol_required": "请填写 Binance symbol，例如 NVDAUSDT。",
    }.get(error_code, "映射保存失败，请检查输入。")


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
    columns = [
        ("ticker", "Ticker"),
        ("friday_close", "周五收盘"),
        ("friday_close_date", "收盘日期"),
    ]
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=[label for _, label in columns])
    display = pd.DataFrame()
    for key, label in columns:
        display[label] = frame.get(key)
    display["周五收盘"] = display["周五收盘"].map(_money_text)
    display["收盘日期"] = display["收盘日期"].replace("", "暂缺")
    return display


def _summary_frame(rows: list[dict]) -> pd.DataFrame:
    columns = [
        ("ticker", "Ticker"),
        ("max_premium_pct", "已记录最大溢价"),
        ("max_discount_pct", "已记录最大折价"),
        ("max_abs_spread_pct", "最大绝对价差"),
        ("max_abs_spread_direction", "最大绝对方向"),
        ("sample_count", "sample_count"),
        ("data_quality", "data_quality"),
    ]
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=[label for _, label in columns])
    display = pd.DataFrame()
    for key, label in columns:
        display[label] = frame.get(key)
    for col in ("已记录最大溢价", "已记录最大折价", "最大绝对价差"):
        display[col] = display[col].map(_percent_text)
    return display


def _monday_outcome_frame(rows: list[dict]) -> pd.DataFrame:
    columns = [
        ("ticker", "Ticker"),
        ("max_abs_spread_pct", "max_abs_spread_pct"),
        ("monday_gap_pct", "monday_gap_pct"),
        ("direction_hit", "direction_hit"),
        ("capture_ratio", "capture_ratio"),
        ("net_edge_pct", "net_edge_pct"),
        ("outcome_status", "outcome_status"),
    ]
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=[label for _, label in columns])
    display = pd.DataFrame()
    for key, label in columns:
        display[label] = frame.get(key)
    for col in ("max_abs_spread_pct", "monday_gap_pct", "net_edge_pct"):
        display[col] = display[col].map(_percent_text)
    display["capture_ratio"] = display["capture_ratio"].map(_ratio_text)
    return display


def _history_frame(rows: list[dict]) -> pd.DataFrame:
    columns = [
        ("ticker", "Ticker"),
        ("sample_weeks", "sample_weeks"),
        ("hit_count", "hit_count"),
        ("partial_count", "partial_count"),
        ("miss_count", "miss_count"),
        ("hit_rate", "hit_rate"),
        ("avg_max_abs_spread_pct", "avg_peak_spread"),
        ("avg_capture_ratio", "avg_capture_ratio"),
        ("avg_net_edge_pct", "avg_net_edge"),
        ("common_failure_reason", "common_failure_reason"),
    ]
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=[label for _, label in columns])
    display = pd.DataFrame()
    for key, label in columns:
        display[label] = frame.get(key)
    display["hit_rate"] = display["hit_rate"].map(_ratio_text)
    display["avg_peak_spread"] = display["avg_peak_spread"].map(_percent_text)
    display["avg_capture_ratio"] = display["avg_capture_ratio"].map(_ratio_text)
    display["avg_net_edge"] = display["avg_net_edge"].map(_percent_text)
    return display


def _backtest_frame(rows: list[dict]) -> pd.DataFrame:
    columns = [
        ("week_id", "周次"),
        ("ticker", "Ticker"),
        ("status", "状态"),
        ("entry_premium_bps", "入场溢价"),
        ("relative_high_rank", "相对高位"),
        ("pullback_bps", "回落幅度"),
        ("net_locked_bps", "锁仓收益"),
        ("residual_basis_bps", "剩余基差"),
        ("oracle_weekend_high_premium_bps", "事后峰值"),
        ("data_quality", "数据质量"),
        ("warning", "排除 / 提醒"),
    ]
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=[label for _, label in columns])
    display = pd.DataFrame()
    for key, label in columns:
        if key == "warning":
            display[label] = frame.apply(lambda row: _backtest_row_warning(row.to_dict()), axis=1)
        else:
            display[label] = frame.get(key)
    for bps_col in ("入场溢价", "回落幅度", "锁仓收益", "剩余基差", "事后峰值"):
        display[bps_col] = display[bps_col].map(_bps_text)
    display["相对高位"] = display["相对高位"].map(_percent_text)
    display["状态"] = display["状态"].map(_basis_status_text)
    display["数据质量"] = display["数据质量"].map(_data_quality_text)
    return display


def _backfill_frame_v2(rows: list[dict]) -> pd.DataFrame:
    columns = [
        ("week_id", "week_id"),
        ("ticker", "ticker"),
        ("broker_symbol", "broker_symbol"),
        ("binance_symbol", "binance_symbol"),
        ("mapping_status", "mapping_status"),
        ("data_mode", "data_mode"),
        ("friday_anchor_price", "friday_anchor_price"),
        ("sunday_max_premium_bps", "sunday_max_premium_bps"),
        ("sunday_max_ts", "sunday_max_ts"),
        ("sunday_relative_high_premium_bps", "sunday_relative_high_premium_bps"),
        ("broker_overnight_open_ts", "broker_overnight_open_ts"),
        ("broker_overnight_open_price", "broker_overnight_open_price"),
        ("open_residual_basis_bps", "open_residual_basis_bps"),
        ("premium_decay_bps", "premium_decay_bps"),
        ("max_adverse_bps", "max_adverse_bps"),
        ("data_quality", "data_quality"),
        ("warning", "warning"),
    ]
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=[label for _, label in columns])
    display = pd.DataFrame()
    for key, label in columns:
        display[label] = frame.get(key)
    for time_col in ("sunday_max_ts", "broker_overnight_open_ts"):
        display[time_col] = display[time_col].map(_short_hkt_time)
    for price_col in ("friday_anchor_price", "broker_overnight_open_price"):
        display[price_col] = display[price_col].map(_money_text)
    for bps_col in (
        "sunday_max_premium_bps",
        "sunday_relative_high_premium_bps",
        "open_residual_basis_bps",
        "premium_decay_bps",
        "max_adverse_bps",
    ):
        display[bps_col] = display[bps_col].map(_bps_text)
    display["mapping_status"] = display["mapping_status"].map(_backfill_mapping_status_text)
    display["data_quality"] = display["data_quality"].map(_data_quality_text)
    display["warning"] = frame.apply(lambda row: _backtest_row_warning(row.to_dict()), axis=1)
    return display


def _backfill_frame(rows: list[dict]) -> pd.DataFrame:
    columns = [
        ("week_id", "week_id"),
        ("ticker", "ticker"),
        ("rule_name", "rule"),
        ("entry_window", "window"),
        ("data_mode", "data_mode"),
        ("entry_ts", "entry_ts"),
        ("net_locked_bps", "net_locked_bps"),
        ("max_adverse_bps", "max_adverse_bps"),
        ("warning", "warning"),
    ]
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=[label for _, label in columns])
    display = pd.DataFrame()
    for key, label in columns:
        display[label] = frame.get(key)
    display["entry_ts"] = display["entry_ts"].map(_short_hkt_time)
    for bps_col in ("net_locked_bps", "max_adverse_bps"):
        display[bps_col] = display[bps_col].map(_bps_text)
    display["warning"] = frame.apply(lambda row: _backtest_row_warning(row.to_dict()), axis=1)
    return display


def _backfill_detail_frame(rows: list[dict]) -> pd.DataFrame:
    columns = [
        ("week_id", "week_id"),
        ("ticker", "ticker"),
        ("anchor_ts", "anchor"),
        ("anchor_price", "anchor_price"),
        ("entry_ts", "signal / entry"),
        ("broker_hedge_ts", "hedge"),
        ("entry_premium_bps", "entry_premium"),
        ("residual_basis_bps", "residual_basis"),
        ("time_unhedged_minutes", "unhedged_min"),
        ("data_quality", "data_quality"),
    ]
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=[label for _, label in columns])
    display = pd.DataFrame()
    for key, label in columns:
        display[label] = frame.get(key)
    for time_col in ("anchor", "signal / entry", "hedge"):
        display[time_col] = display[time_col].map(_short_hkt_time)
    for bps_col in ("entry_premium", "residual_basis"):
        display[bps_col] = display[bps_col].map(_bps_text)
    display["anchor_price"] = display["anchor_price"].map(_money_text)
    display["data_quality"] = display["data_quality"].map(_data_quality_text)
    return display


def _backtest_row_warning(row: dict) -> str:
    quality = str(row.get("data_quality") or "")
    error = str(row.get("error_message") or "")
    note = str(row.get("result_note") or "")
    warning = str(row.get("warning") or "")
    if quality in {"DATA_UNAVAILABLE", "BINANCE_KLINE_UNAVAILABLE"}:
        return f"Binance K 线不可用：{error}" if error else "Binance K 线不可用"
    if quality in {"NO_BROKER_OVERNIGHT_BAR", "MISSING_STOCK_FIRST_BAR"}:
        anchor = str(row.get("stock_open_anchor_label") or "开盘")
        start = _short_hkt_time(row.get("stock_bar_requested_start"))
        end = _short_hkt_time(row.get("stock_bar_requested_end"))
        returned = int(_number(row.get("stock_bar_returned_count")) or 0)
        return warning or f"缺少美股端第一根有效 1m bar；锚点：{anchor}；请求区间：{start} - {end}；返回 {returned} 根"
    if quality == "HOLIDAY_OR_NO_SESSION":
        return warning or "遇到美国假期或无有效交易时段"
    if quality == "DEGRADED_5M":
        return warning or "使用 5m bar 替代 1m，样本仅作降级观察"
    if quality == "STALE_OR_MISALIGNED":
        return warning or "Binance 与 broker 报价时间未对齐"
    if quality == "ESTIMATED_EXECUTION":
        return warning or "估算执行，仅观察"
    if quality in {"WIDE_SPREAD", "LOW_DEPTH"}:
        return warning or _data_quality_text(quality)
    if quality == "INVALID":
        return error or "无效样本"
    if quality in {"UNCONFIRMED_MAPPING", "OBSERVE_ONLY"}:
        return warning or note or "未确认映射，仅观察"
    return warning or note


def _mapping_management_frame(rows: list[dict], mapping: dict[str, dict]) -> pd.DataFrame:
    universe_tickers = {str(row.get("ticker") or "").upper() for row in rows if row.get("ticker")}
    table_rows: list[dict] = []
    for row in rows:
        table_rows.append(
            {
                "ticker": str(row.get("ticker") or "").upper(),
                "binance_symbol": str(row.get("binance_symbol") or ""),
                "market_type": str(row.get("binance_market_type") or row.get("market_type") or ""),
                "mapping_confidence": str(row.get("mapping_confidence") or ""),
                "validation_status": str(row.get("mapping_status") or ""),
                "last_price_status": _last_price_status(row),
                "risk_note": str(row.get("mapping_risk") or row.get("risk_note") or ""),
            }
        )
    for ticker, config in sorted(mapping.items()):
        normalized = str(ticker or "").upper()
        if normalized in universe_tickers:
            continue
        table_rows.append(
            {
                "ticker": normalized,
                "binance_symbol": str(config.get("binance_symbol") or ""),
                "market_type": str(config.get("market_type") or ""),
                "mapping_confidence": str(config.get("mapping_confidence") or ""),
                "validation_status": "不在观察池",
                "last_price_status": "未请求",
                "risk_note": str(config.get("risk_note") or ""),
            }
        )
    columns = [
        ("ticker", "ticker"),
        ("binance_symbol", "binance_symbol"),
        ("market_type", "market_type"),
        ("mapping_confidence", "mapping_confidence"),
        ("validation_status", "validation_status"),
        ("last_price_status", "last_price_status"),
        ("risk_note", "risk_note"),
    ]
    frame = pd.DataFrame(table_rows)
    if frame.empty:
        return pd.DataFrame(columns=[label for _, label in columns])
    display = pd.DataFrame()
    for key, label in columns:
        display[label] = frame.get(key)
    display["binance_symbol"] = display["binance_symbol"].replace("", "暂无映射")
    display["mapping_confidence"] = display["mapping_confidence"].replace("", "no_mapping")
    return display


def _mapping_management_frame(records: list[dict], mapping: dict[str, dict] | None = None) -> pd.DataFrame:
    if mapping is not None:
        records = _mapping_management_records(records, mapping)
    columns = [
        "股票",
        "Binance 合约",
        "Binance 价格",
        "股票参考价",
        "价格差异%",
        "映射状态",
        "操作",
    ]
    if not records:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(
        [
            {
                "股票": record.get("ticker"),
                "Binance 合约": record.get("binance_symbol") or "无映射",
                "Binance 价格": _money_text(record.get("binance_price")) if _number(record.get("binance_price")) is not None else "缺价格",
                "股票参考价": _money_text(record.get("stock_ref_price")) if _number(record.get("stock_ref_price")) is not None else "未校验",
                "价格差异%": _percent_text(record.get("price_diff_pct")) if _number(record.get("price_diff_pct")) is not None else "未校验",
                "映射状态": record.get("state_label"),
                "操作": _mapping_action_hint(record),
            }
            for record in records
        ],
        columns=columns,
    )


def _mapping_action_hint(record: dict) -> str:
    group = str(record.get("state_group") or "")
    if group == "usable":
        return "可直接回测；可采用为人工锁定"
    if group == "locked":
        return "已人工锁定"
    if group == "off_watchlist":
        return "不参与当前周末价差"
    if group == "missing":
        return "修改或忽略"
    return "采用 / 忽略 / 修改"


def _render_row_details(rows: list[dict]) -> None:
    if not rows:
        return
    for row in rows:
        with st.expander(f"{str(row.get('ticker') or '').upper()} 查看详情", expanded=False):
            col_anchor, col_binance, col_note = st.columns(3)
            with col_anchor:
                st.markdown("**美股锚点**")
                st.caption(f"美股盘后锚点：{_money_text(row.get('afterhours_reference_price'))}")
                st.caption(f"常规收盘价：{_money_text(row.get('regular_close_price') or row.get('friday_close'))}")
                st.caption(f"相对盘后：{_afterhours_spread_text(row.get('spread_vs_afterhours_pct'))}")
                st.caption(f"相对收盘：{_percent_text(row.get('spread_vs_regular_close_pct'))}")
                st.caption(f"锚点来源：{_afterhours_source_text(row.get('afterhours_reference_source'))}")
                st.caption(f"锚点时间：{_short_hkt_time(row.get('afterhours_reference_time'))}")
                st.caption(f"锚点状态：{_anchor_display_label_for_row(row)}")
                st.caption(f"缺失原因：{_afterhours_reason_text(row.get('afterhours_missing_reason'))}")
            with col_binance:
                st.markdown("**Binance 行情**")
                st.caption(f"Binance symbol：{str(row.get('binance_symbol') or '无映射')}")
                st.caption(f"Binance 最新价格：{_money_text(row.get('binance_last_price'))}")
                st.caption(f"Binance 更新时间：{_short_hkt_time(row.get('updated_at'))}")
                st.caption(f"bid：{_money_text(row.get('binance_bid'))}")
                st.caption(f"ask：{_money_text(row.get('binance_ask'))}")
                st.caption(f"bid-ask spread：{_percent_text(row.get('binance_spread_pct'))}")
                st.caption(f"24h volume：{_plain_number(row.get('binance_volume_24h'))}")
                st.caption(f"funding：{_funding_text(row.get('funding_rate'))}")
            with col_note:
                st.markdown("**数据备注**")
                st.caption(f"映射状态：{_mapping_display_label_for_row(row)}")
                st.caption(f"数据状态：{_realtime_row_status_label(row)}")
                st.caption(f"状态说明：{_realtime_row_status_reason(row)}")
                note = str(row.get("mapping_risk") or row.get("risk_note") or "").strip()
                liquidity = str(row.get("liquidity_warning") or "").strip()
                error = str(row.get("error") or "").strip()
                if note:
                    st.caption(f"完整备注：{note}")
                if liquidity:
                    st.caption(f"流动性备注：{liquidity}")
                if error:
                    st.caption(f"错误原因：{_localized_realtime_error(error)}")


def _render_mapping_diagnostics(mapping: dict[str, dict]) -> None:
    with st.expander("映射诊断", expanded=False):
        validate = st.button("校验 symbol 映射", width="stretch", key="weekend_validate_mapping")
        diagnostics = build_mapping_diagnostics(
            load_watchlist(),
            mapping=mapping,
            validate=validate,
            include_candidates=validate,
        )
        st.dataframe(_diagnostics_frame(diagnostics), width="stretch", hide_index=True)
        if validate:
            st.caption("候选 symbol 仅表示 Binance 上存在相似代码，不代表真实映射美股，需要人工确认。")


def _diagnostics_frame(rows: list[dict]) -> pd.DataFrame:
    columns = [
        ("ticker", "Ticker"),
        ("configured_symbol", "配置 symbol"),
        ("market_type", "市场"),
        ("mapping_confidence", "映射置信"),
        ("validation_status", "校验状态"),
        ("last_validated_at", "校验时间"),
        ("price_available", "价格"),
        ("book_available", "买卖盘"),
        ("volume_available", "成交量"),
        ("funding_available", "资金费率"),
        ("candidate_scan_status", "候选扫描"),
        ("candidate_scan_message", "候选说明"),
        ("risk_note", "风险备注"),
        ("candidates", "候选"),
    ]
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=[label for _, label in columns])
    display = pd.DataFrame()
    for key, label in columns:
        display[label] = frame.get(key)
    for col in ("价格", "买卖盘", "成交量", "资金费率"):
        display[col] = display[col].map(lambda value: "可用" if bool(value) else "暂缺")
    display["映射置信"] = display["映射置信"].map(_mapping_confidence_label)
    display["候选"] = display["候选"].map(_candidate_text)
    display["配置 symbol"] = display["配置 symbol"].replace("", "暂无映射")
    display["校验时间"] = display["校验时间"].replace("", "未校验")
    return display


def _mapping_confidence_label(value: object) -> str:
    text = str(value or "").strip().lower()
    return {
        "confirmed": "人工锁定",
        "candidate": "自动候选",
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
    priced = [row for row in rows if row.get("spread_pct") is not None]
    if not priced:
        return None
    return max(priced, key=lambda row: abs(float(row.get("spread_pct") or 0)))


def _strongest_signal_warning(row: dict) -> str:
    if str(row.get("mapping_confidence") or "") != "confirmed":
        return "映射未确认，不能作为正式套利信号。"
    if str(row.get("primary_spread_anchor") or "") == "REGULAR_CLOSE_FALLBACK":
        return "缺少最后交易日盘后参考价，当前按最后交易日收盘价临时对比。"
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
        return "映射未确认，需要人工确认后再作为正式观察样本。"
    if str(row.get("primary_spread_anchor") or "") == "REGULAR_CLOSE_FALLBACK":
        return "缺少最后交易日盘后参考价，当前价差以最后交易日正常收盘价为基准。"
    return "仅用于观察，不构成套利建议。"


def _last_price_status(row: dict) -> str:
    if not row.get("binance_symbol"):
        return "暂无映射"
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
    return f"{total} 条本地映射，{confirmed} 条 confirmed"


def _binance_status_text(rows: list[dict], universe_mapping_count: int) -> str:
    if universe_mapping_count <= 0:
        return "等待 mapping"
    if all(str(row.get("error") or "") == "price_not_loaded" for row in rows if row.get("binance_symbol")):
        return "等待刷新"
    if any(row.get("status") == "OK" for row in rows):
        return "可用"
    if any(row.get("status") in {"BINANCE_UNAVAILABLE", "PRICE_UNAVAILABLE"} for row in rows):
        return "数据不可用"
    if any(row.get("status") == "INVALID_SYMBOL" for row in rows):
        return "symbol 异常"
    return "待刷新"


def _market_price_source_status(rows: list[dict], market_type: str) -> str:
    market_rows = [
        row
        for row in rows
        if row.get("binance_symbol") and str(row.get("binance_market_type") or row.get("market_type") or "") == market_type
    ]
    if not market_rows:
        return "无请求"
    if all(str(row.get("error") or "") == "price_not_loaded" for row in market_rows):
        return "无请求"
    if any(row.get("status") == "OK" and row.get("binance_last_price") is not None for row in market_rows):
        return "可用"
    return "不可用"


def _market_data_status(rows: list[dict], market_type: str) -> str:
    market_rows = [
        row
        for row in rows
        if row.get("binance_symbol") and str(row.get("binance_market_type") or row.get("market_type") or "") == market_type
    ]
    if not market_rows:
        return "等待 mapping"
    if any(row.get("status") == "OK" for row in market_rows):
        return "可用"
    if any(row.get("status") == "INVALID_SYMBOL" for row in market_rows):
        return "symbol 无效"
    return "数据不可用"


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
    code = str(value or "").strip()
    return {
        "PROVIDER_MISSING": "未配置盘后数据源",
        "API_KEY_MISSING": "缺少盘后数据源 API key",
        "NOT_FETCHED": "未抓取盘后价",
        "FETCH_FAILED": "盘后接口失败",
        "NO_AFTERHOURS_TRADE": "当日无盘后成交",
        "NO_AFTERHOURS_QUOTE": "无有效 bid/ask",
        "CACHE_MISSING": "盘后缓存缺失",
        "CACHE_CORRUPT": "盘后缓存损坏",
        "CACHE_DATE_MISMATCH": "盘后缓存日期不匹配",
        "STALE_CACHE": "盘后缓存过期",
        "FIELD_NOT_PASSED": "字段未传入",
        "USING_CACHE": "使用缓存盘后价",
    }.get(code, code or "未抓取盘后价")


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
    }.get(code, code or "缺少盘后参考价")


def _afterhours_cache_text(value: object) -> str:
    code = str(value or "").strip()
    return {
        "API_LIVE": "API 实时盘后价",
        "CACHE_HIT": "使用缓存盘后价",
        "CACHE_FALLBACK": "接口失败，使用缓存盘后价",
        "CACHE_MISSING": "盘后缓存缺失",
        "CACHE_CORRUPT": "盘后缓存损坏",
        "CACHE_DATE_MISMATCH": "盘后缓存日期不匹配",
        "NOT_FETCHED": "未抓取盘后价",
    }.get(code, code or "暂缺")


def _afterhours_anchor_badge(row: dict) -> str:
    if _number(row.get("afterhours_reference_price")) is None:
        return "盘后缺失，使用收盘回退"
    status = str(row.get("afterhours_anchor_status") or "").strip().upper()
    cache_status = str(row.get("afterhours_cache_status") or "").strip().upper()
    if status in {"FINAL", "PROVISIONAL"}:
        return status
    if cache_status in {"CACHE_HIT", "CACHE_FALLBACK"}:
        return "已缓存"
    if cache_status == "API_LIVE":
        return "已更新"
    return "已缓存"


def _money_text(value: object) -> str:
    number = _number(value)
    if number is None:
        return "暂缺"
    return f"${number:,.2f}"


def _price_anchor_text(row: dict) -> str:
    afterhours = _number(row.get("afterhours_reference_price"))
    if afterhours is not None:
        return f"盘后 ${afterhours:,.2f}（{_afterhours_anchor_badge(row)}）"
    regular = _number(row.get("regular_close_price") or row.get("friday_close"))
    if regular is None:
        return "暂缺"
    reason = _afterhours_reason_text(row.get("afterhours_missing_reason"))
    return f"收盘 ${regular:,.2f}｜盘后缺失：{reason}"


def _afterhours_spread_text(value: object) -> str:
    number = _number(value)
    if number is None:
        return "—"
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


def _risk_badge_text(row: dict) -> str:
    risks: list[str] = []
    data_source = str(row.get("data_source_text") or "").strip()
    if data_source:
        risks.append(data_source)
    status = str(row.get("status") or "")
    confidence = str(row.get("mapping_confidence") or "")
    if confidence and confidence != "confirmed":
        risks.append("映射未确认，仅观察，不能作为正式交易信号")
    if status == "INVALID_SYMBOL":
        risks.append("symbol 无效")
    if status in {"BINANCE_UNAVAILABLE", "PRICE_UNAVAILABLE"}:
        risks.append("数据不可用")
    if _number(row.get("afterhours_reference_price")) is None and row.get("binance_symbol"):
        risks.append(f"缺少盘后参考价：{_afterhours_reason_text(row.get('afterhours_missing_reason'))}，当前使用周五收盘作为回退参考")
    elif str(row.get("afterhours_cache_status") or "") in {"CACHE_HIT", "CACHE_FALLBACK"}:
        risks.append(_afterhours_cache_text(row.get("afterhours_cache_status")))
    liquidity = str(row.get("liquidity_warning") or "")
    if "成交量不足" in liquidity:
        risks.append("成交量不足")
    if "价差偏宽" in liquidity:
        risks.append("bid-ask 偏宽")
    if not risks:
        risks.append("仅观察")
    return "；".join(dict.fromkeys(risks))


def _localized_realtime_error(value: object) -> str:
    text = str(value or "").strip()
    return {
        "price_not_loaded": "Binance 价格尚未读取",
        "invalid_symbol": "Binance 合约无效",
        "binance_price_missing": "Binance 价格缺失",
        "NO_MAPPING": "无映射",
    }.get(text, text or "无")


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
