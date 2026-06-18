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
from settings import load_watchlist


RISK_NOTICE = (
    "V1 仅用于周末价差观察和历史统计，不构成套利建议。Binance 映射价格不等于真实美股可成交价格；"
    "价差可能来自流动性、点差、资金费率、映射误差或币种单位差异。"
)
LARGE_WEEKEND_PREMIUM_PCT = 1.5

TAB_REALTIME = "实时观察"
TAB_BACKTEST = "历史回测"
TAB_MAPPING = "映射管理"
HKT = ZoneInfo("Asia/Hong_Kong")


def render() -> None:
    st.markdown(
        """
        <section class="zhx-page-head">
          <div>
            <span class="zhx-eyebrow">ZHX RESEARCH</span>
            <h1>周末价差观察台</h1>
            <p>实时观察周末映射价差，记录峰值，并在周一做信号验证。</p>
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


def _render_realtime_tab(
    watchlist: list[str],
    mapping: dict[str, dict],
) -> tuple[list[dict], dict[str, int]]:
    st.subheader("实时观察")
    refresh_options = _render_realtime_action_bar()
    rows, cache_status = _build_weekend_spread_rows_with_feedback(watchlist, mapping=mapping, refresh_options=refresh_options)
    st.session_state["weekend_realtime_rows"] = rows
    st.session_state["weekend_realtime_cache_status"] = cache_status

    mapping_counts = _mapping_counts(rows, mapping)

    _render_primary_kpis(rows, mapping_counts)
    _render_data_status_cards(rows, mapping_counts, DEFAULT_LOCAL_MAPPING_PATH, cache_status)
    _render_strongest_signal(rows, mapping_counts)

    main_rows = _default_live_rows(rows)
    if _should_show_empty_mapping_state(mapping_counts, "重点/有数据"):
        _render_empty_mapping_state(mapping_counts, DEFAULT_LOCAL_MAPPING_PATH)
    elif main_rows:
        st.dataframe(_live_frame(main_rows), width="stretch", hide_index=True)
        _render_row_details(main_rows)
    else:
        st.info("当前没有可展示的实时价差。若已有映射，请刷新价格或查看映射状态。")

    _render_paper_trade_area(rows, mapping)
    _render_no_mapping_expander(rows)
    return rows, mapping_counts


def _render_realtime_action_bar() -> dict[str, bool]:
    col_cache, col_refresh, col_anchor, col_force_anchor, col_note = st.columns([1, 1, 1, 1, 3])
    use_cache = col_cache.button("使用缓存", width="stretch", key="weekend_spread_use_cache")
    refresh = col_refresh.button("刷新 Binance 价格", width="stretch", key="weekend_spread_refresh")
    anchor_refresh = col_anchor.button("更新周五盘后锚点", width="stretch", key="weekend_spread_anchor_refresh")
    force_anchor = col_force_anchor.button("强制重建锚点", width="stretch", key="weekend_spread_force_anchor_refresh")
    col_note.caption("Binance 价格和周五盘后锚点已解耦：刷新 Binance 不会重新请求盘后数据。")
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
        status_slot.caption(f"Updating Friday afterhours anchors: {total} symbols.")

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
          <span class="zhx-eyebrow">当前最强信号</span>
          <h3>{escape(str(row.get("ticker") or ""))} · {escape(spread)}</h3>
          <p>{escape(str(row.get("spread_direction") or ""))}｜{escape(str(row.get("alert_level_cn") or ""))}｜映射：{escape(str(row.get("mapping_confidence") or "unknown"))}</p>
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
    st.subheader("周一验证")
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
        "周一验证价（非 Binance 实时价）",
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
            st.warning("请输入有效的周一验证价。")
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
        st.info("暂无历史验证记录。记录周末样本并完成周一验证后，这里会显示命中率和平均捕捉比例。")
        return
    st.dataframe(_history_frame(stats), width="stretch", hide_index=True)


def _render_backtest_tab(watchlist: list[str], mapping: dict[str, dict]) -> None:
    st.subheader("历史回测")
    st.warning(
        "这是历史观察回测，不构成套利建议。周末高点未必能成交；"
        "USDT-M 合约数据不可用时不能计算观察收益；映射未确认时结果仅作观察。"
    )
    include_unconfirmed = st.checkbox(
        "包含未确认映射",
        value=False,
        key="weekend_backtest_include_unconfirmed",
        help="未确认映射仅观察，默认不纳入正式胜率。",
    )
    all_tickers = [str(ticker or "").upper() for ticker in watchlist if str(ticker or "").strip()]
    anchors = _backtest_anchor_mapping(all_tickers, weeks=4)
    preliminary = build_weekend_backtest_preflight(
        all_tickers,
        mapping=mapping,
        anchors=anchors,
        include_unconfirmed=include_unconfirmed,
    )
    options = ["全部已映射"] + list(preliminary.get("eligible_tickers") or [])
    anchor_options = {
        "premarket": "盘前 04:00 ET",
        "regular_open": "正式开盘 09:30 ET",
        "overnight": "券商夜盘 20:00 ET",
    }
    anchor_labels = list(anchor_options.values())
    anchor_by_label = {label: key for key, label in anchor_options.items()}
    cols = st.columns(4)
    selected = cols[0].selectbox("标的", options, key="weekend_backtest_ticker")
    weeks = int(cols[1].number_input("回测周数", min_value=1, max_value=12, value=4, step=1, key="weekend_backtest_weeks"))
    open_window = int(cols[2].selectbox("开盘窗口（分钟）", [5, 15, 30], index=0, key="weekend_backtest_open_window"))
    selected_anchor_label = cols[3].selectbox("开盘锚点", anchor_labels, index=0, key="weekend_backtest_open_anchor")
    opening_anchor = anchor_by_label.get(selected_anchor_label, "premarket")
    run_tickers = list(preliminary.get("eligible_tickers") or []) if selected == "全部已映射" else [selected]
    anchors = _backtest_anchor_mapping(run_tickers or all_tickers, weeks=weeks)
    preflight = build_weekend_backtest_preflight(
        run_tickers,
        mapping=mapping,
        anchors=anchors,
        include_unconfirmed=include_unconfirmed,
        ticker_filter="" if selected == "全部已映射" else selected,
    )
    _render_backtest_preflight(preflight)
    if not preflight.get("can_run"):
        st.warning(_backtest_block_text(str(preflight.get("primary_block_reason") or "")))
    op_cols = st.columns([2, 1, 2])
    run_clicked = op_cols[0].button(
        "运行近 4 周回测",
        width="stretch",
        key="weekend_run_backtest",
        disabled=not bool(preflight.get("can_run")),
    )
    clear_clicked = op_cols[1].button("清空本次结果", width="stretch", key="weekend_clear_backtest_view")
    with op_cols[2].expander("查看排除原因", expanded=False):
        excluded = list(preflight.get("excluded") or preliminary.get("excluded") or [])
        st.dataframe(_backtest_exclusion_frame(excluded), width="stretch", hide_index=True)
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
            mapping=mapping,
            anchors=anchors,
            include_unconfirmed=include_unconfirmed,
            ticker_filter="" if selected == "全部已映射" else selected,
        )
        afterhours_anchor_note = _historical_afterhours_anchor_summary_text(anchors)
        progress_bar = st.progress(0.0)
        status_slot = st.empty()
        status_slot.caption(f"正在运行历史回测：{len(tickers)} 个标的，{weeks} 周。")
        results = run_weekend_basis_backtest(
            tickers,
            mapping=mapping,
            anchors=anchors,
            weeks=weeks,
            open_window_minutes=open_window,
            opening_anchor=opening_anchor,
        )
        progress_bar.progress(1.0)
        failed = [
            row
            for row in results
            if str(row.get("data_quality") or "")
            in {
                "BINANCE_KLINE_UNAVAILABLE",
                "NO_BROKER_OVERNIGHT_BAR",
                "MISSING_STOCK_FIRST_BAR",
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
        mapping=mapping,
        include_unconfirmed=include_unconfirmed,
    )
    if not results:
        if not preflight.get("can_run"):
            st.info(f"没有可回测标的：{_backtest_block_text(str(preflight.get('primary_block_reason') or 'NO_MAPPING'))}")
        elif cached_result.get("error_message"):
            st.warning(f"上次运行失败：{cached_result.get('error_message')}")
        else:
            st.info("尚未运行历史回测。配置映射后点击“运行近 4 周回测”。默认使用盘前 04:00 ET 后第一根有效价格；也可以切换为正式开盘或券商夜盘。")
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
        if not include_unconfirmed and confidence != "confirmed":
            continue
        filtered.append(row)
    return filtered


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
        "NO_PRICE_ANCHOR": "缺少价格锚点：请先刷新实时观察或更新周五盘后锚点。",
        "PROVIDER_ERROR": "数据源错误，请稍后重试。",
    }.get(reason, reason or "当前没有可回测标的。")


def _backtest_mode_text(value: object) -> str:
    text = str(value or "").strip().lower()
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
        if quality in {"NO_BROKER_OVERNIGHT_BAR", "MISSING_STOCK_FIRST_BAR"}:
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
        ("近4周样本数", int(summary.get("sample_count") or 0), ""),
        ("平均价差", summary.get("avg_price_diff"), "money_diff"),
        ("平均溢价%", summary.get("avg_premium_pct"), "percent"),
        ("最大溢价%", summary.get("max_premium_pct"), "percent"),
        ("最新一周溢价%", summary.get("latest_week_avg_premium_pct"), "percent"),
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
        if kind == "money_diff":
            col.metric(label, _signed_money_text(value, missing="暂无数据"))
        elif kind == "percent":
            col.metric(label, _review_percent_text(value))
        else:
            col.metric(label, value)
    st.caption(
        f"正式有效样本 {quality_counts['ok']}｜观察样本 {quality_counts['observe']}｜"
        f"降级样本 {quality_counts['degraded']}｜排除样本 {quality_counts['excluded']}"
    )


def _weekend_review_quality_counts(review_rows: list[dict]) -> dict[str, int]:
    counts = {"ok": 0, "observe": 0, "degraded": 0, "excluded": 0}
    for row in review_rows:
        quality = str(row.get("data_quality") or "").strip().upper()
        if quality == "OK":
            counts["ok"] += 1
        elif quality == "OBSERVE_ONLY":
            counts["observe"] += 1
        elif quality.startswith("DEGRADED"):
            counts["degraded"] += 1
        else:
            counts["excluded"] += 1
    return counts


def _weekend_review_empty_reason(review_rows: list[dict]) -> str:
    if not review_rows:
        return "当前没有可计入正式统计的样本。请先配置有效 mapping 并运行回测。"
    qualities = {str(row.get("data_quality") or "").strip().upper() for row in review_rows}
    raw_qualities = {
        str((row.get("raw_row") or {}).get("data_quality") or row.get("data_quality") or "").strip().upper()
        for row in review_rows
    }
    if raw_qualities & {"MISSING_STOCK_FIRST_BAR", "NO_BROKER_OVERNIGHT_BAR"}:
        return (
            "当前没有可计入正式统计的样本。主要原因：缺少美股端第一根有效 1m bar。"
            "请尝试扩大开盘窗口、切换开盘锚点为 premarket/regular_open，或检查券商历史数据权限。"
        )
    if raw_qualities & {"HOLIDAY_OR_NO_SESSION"}:
        return "当前没有可计入正式统计的样本。主要原因：遇到美国假期或非交易日，请检查交易日历或扩大窗口。"
    if qualities == {"MAPPING_MISSING"}:
        return "当前没有可计入正式统计的样本。主要原因：映射未确认或未配置；请先在 mapping.local 中确认映射关系。"
    if qualities == {"CONTRACT_MISSING"}:
        return "当前没有可计入正式统计的样本。主要原因：USDT-M 合约历史数据缺失。"
    if qualities == {"STOCK_MISSING"}:
        return "当前没有可计入正式统计的样本。主要原因：缺少美股历史锚点或开盘参考价。"
    return "当前没有可计入正式统计的样本。请展开数据质量详情，查看每周请求区间、返回 bar 数量和排除原因。"


def _render_weekend_review_table(review_rows: list[dict]) -> None:
    frame = _weekend_review_frame(_ok_weekend_review_rows(review_rows))
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
        anchor_price = _first_number(
            row,
            (
                "friday_anchor_price",
                "anchor_price",
                "broker_anchor_price",
                "regular_close_price",
                "friday_close_price",
                "friday_close",
            ),
        )
        premium_pct = _weekend_review_premium_pct(row, anchor_price)
        binance_price = _weekend_review_binance_price(row, anchor_price, premium_pct)
        price_diff = binance_price - anchor_price if anchor_price is not None and binance_price is not None else None
        data_quality = _weekend_review_data_quality(row, anchor_price, binance_price, premium_pct)
        record = {
            "week_id": week_id,
            "ticker": ticker,
            "stock_reference_date": _weekend_review_stock_reference_date(row),
            "stock_price": anchor_price,
            "contract_sample_time": _weekend_review_contract_sample_time(row),
            "binance_price": binance_price,
            "price_diff": price_diff,
            "premium_pct": premium_pct,
            "data_quality": data_quality,
            "status": _weekend_review_status(data_quality, premium_pct),
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
    latest_weeks = set(_latest_week_ids(ok_rows, limit=4))
    scoped = [row for row in ok_rows if row.get("week_id") in latest_weeks] if latest_weeks else list(ok_rows)
    valid = [row for row in scoped if _number(row.get("price_diff")) is not None and _number(row.get("premium_pct")) is not None]
    if not valid:
        return {
            "sample_count": 0,
            "avg_price_diff": None,
            "avg_premium_pct": None,
            "max_premium_pct": None,
            "latest_week_avg_premium_pct": None,
        }
    premiums = [float(_number(row.get("premium_pct")) or 0.0) for row in valid]
    diffs = [float(_number(row.get("price_diff")) or 0.0) for row in valid]
    latest_week = _latest_week_ids(valid, limit=1)
    latest_rows = [row for row in valid if latest_week and row.get("week_id") == latest_week[0]]
    latest_premiums = [float(_number(row.get("premium_pct")) or 0.0) for row in latest_rows]
    return {
        "sample_count": len(valid),
        "avg_price_diff": sum(diffs) / len(diffs),
        "avg_premium_pct": sum(premiums) / len(premiums),
        "max_premium_pct": max(premiums),
        "latest_week_avg_premium_pct": sum(latest_premiums) / len(latest_premiums) if latest_premiums else None,
    }


def _weekend_review_frame(review_rows: list[dict]) -> pd.DataFrame:
    columns = ["周次", "股票", "美股参考日", "美股价格", "合约采样时间", "合约价格", "价差", "溢价%", "数据质量", "状态"]
    if not review_rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(
        [
            {
                "周次": row.get("week_id"),
                "股票": row.get("ticker"),
                "美股参考日": row.get("stock_reference_date") or "暂无数据",
                "美股价格": row.get("stock_price"),
                "合约采样时间": row.get("contract_sample_time") or "暂无数据",
                "合约价格": row.get("binance_price"),
                "价差": row.get("price_diff"),
                "溢价%": row.get("premium_pct"),
                "数据质量": _data_quality_text(row.get("data_quality")),
                "状态": row.get("status"),
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

    styler = frame.style.format(
        {
            "美股价格": lambda value: _money_text(value) if _number(value) is not None else "暂无数据",
            "合约价格": lambda value: _money_text(value) if _number(value) is not None else "暂无数据",
            "价差": lambda value: _signed_money_text(value, missing="暂无数据"),
            "溢价%": lambda value: _review_percent_text(value),
        }
    )
    if hasattr(styler, "map"):
        return styler.map(color_value, subset=["价差", "溢价%"])

    def color_frame(data: pd.DataFrame) -> pd.DataFrame:
        if hasattr(data, "map"):
            return data.map(color_value)
        return data.applymap(color_value)

    return styler.apply(color_frame, subset=["价差", "溢价%"], axis=None)


def _ok_weekend_review_rows(review_rows: list[dict]) -> list[dict]:
    return [row for row in review_rows if str(row.get("data_quality") or "").strip().upper() == "OK"]


def _weekend_review_data_quality(
    row: dict,
    anchor_price: float | None,
    binance_price: float | None,
    premium_pct: float | None,
) -> str:
    quality = str(row.get("data_quality") or "").strip().upper()
    status = str(row.get("status") or "").strip().upper()
    mapping_status = str(row.get("mapping_status") or "").strip().upper()
    cache_status = str(row.get("kline_cache_status") or row.get("cache_status") or "").strip().upper()
    if quality in {"OBSERVE_ONLY", "UNCONFIRMED_MAPPING"} or mapping_status == "CANDIDATE_OBSERVATION":
        return "OBSERVE_ONLY"
    if quality.startswith("DEGRADED"):
        return "DEGRADED"
    if quality in {"BLOCK_MAPPING", "NO_MAPPING", "MAPPING_MISSING"} or status == "BLOCK_MAPPING":
        return "MAPPING_MISSING"
    if anchor_price is None or anchor_price <= 0 or quality in {"NO_PRICE_ANCHOR", "STOCK_MISSING", "MISSING_STOCK_FIRST_BAR", "NO_BROKER_OVERNIGHT_BAR", "HOLIDAY_OR_NO_SESSION"}:
        return "STOCK_MISSING"
    if binance_price is None or binance_price <= 0 or quality in {"BINANCE_KLINE_UNAVAILABLE", "CONTRACT_MISSING", "DATA_UNAVAILABLE"}:
        return "CONTRACT_MISSING"
    if cache_status in {"STALE", "STALE_CACHE", "CACHE_FALLBACK"} or quality in {"STALE_CACHE", "STALE_OR_MISALIGNED"}:
        return "STALE_CACHE"
    if premium_pct is None or quality in {"INVALID", "MISSING", "DATA_INSUFFICIENT", "INVALID_PRICE"}:
        return "INVALID_PRICE"
    if quality in {"", "OK"}:
        return "OK"
    return "INVALID_PRICE"


def _weekend_review_status(data_quality: str, premium_pct: float | None) -> str:
    if data_quality != "OK" or premium_pct is None:
        return "数据不完整"
    if abs(premium_pct) >= LARGE_WEEKEND_PREMIUM_PCT:
        return "价差较大"
    return "可观察"


def _weekend_review_rank(row: dict) -> tuple[int, float]:
    premium = _number(row.get("premium_pct"))
    anchor = _number(row.get("stock_price"))
    binance = _number(row.get("binance_price"))
    valid = 1 if row.get("data_quality") == "OK" and premium is not None and anchor is not None and binance is not None else 0
    return (valid, abs(float(premium or 0.0)))


def _weekend_review_stock_reference_date(row: dict) -> str:
    for key in ("regular_close_date", "friday_close_date", "anchor_ts", "weekend_window_start"):
        value = str(row.get(key) or "").strip()
        if value:
            return value[:10]
    return ""


def _weekend_review_contract_sample_time(row: dict) -> str:
    for key in (
        "oracle_weekend_high_time",
        "weekend_peak_time",
        "binance_entry_ts",
        "entry_ts",
        "sample_time",
        "updated_at",
    ):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


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
    target = window.start_et.date()
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
        return "历史盘后锚点：未找到可读取的历史周五收盘记录。"
    note = f"历史盘后锚点：已读取 {afterhours}/{total}，其中缓存 {cache}；回退正常收盘 {fallback}。"
    if fallback and reasons:
        primary = sorted(reasons.items(), key=lambda item: item[1], reverse=True)[0]
        note += f"主要回退原因：{_afterhours_reason_text(primary[0])}（{primary[1]} 周）。"
    return note


def _render_backtest_advanced_records() -> None:
    with st.expander("高级 / 前瞻记录", expanded=False):
        st.caption("这些记录用于前瞻观察和周一验证，不是历史回测主流程。")
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
    return [
        row
        for row in rows
        if row.get("binance_symbol") or row.get("spread_pct") is not None or row.get("alert_level") in {"FOCUS", "ABNORMAL"}
    ]


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


def _mapping_management_counts(rows: list[dict], mapping: dict[str, dict]) -> dict[str, int]:
    counts = _mapping_counts(rows, mapping)
    local_items = [item for item in mapping.values() if item.get("enabled", True) and item.get("binance_symbol")]
    counts.update(
        {
            "confirmed_count": sum(1 for item in local_items if item.get("mapping_confidence") == "confirmed"),
            "candidate_count": sum(
                1
                for item in local_items
                if item.get("mapping_confidence") in {"candidate", "unverified", "verified_ready", "stale"}
            ),
            "no_mapping_count": sum(1 for row in rows if not row.get("binance_symbol")),
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


def _mapping_editor_error_text(error_code: str) -> str:
    return {
        "ticker_required": "请选择观察池 ticker。",
        "binance_symbol_required": "请填写 Binance symbol，例如 NVDAUSDT。",
    }.get(error_code, "映射保存失败，请检查输入。")


def _live_frame(rows: list[dict]) -> pd.DataFrame:
    columns = [
        "Ticker",
        "价格锚点",
        "Binance 最新",
        "vs 盘后",
        "vs 收盘",
        "状态",
        "风险",
        "更新时间",
    ]
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=columns)
    display = pd.DataFrame()
    display["Ticker"] = frame.get("ticker")
    display["价格锚点"] = frame.apply(lambda row: _price_anchor_text(row.to_dict()), axis=1)
    display["Binance 最新"] = frame.get("binance_last_price").map(_money_text)
    display["vs 盘后"] = frame.get("spread_vs_afterhours_pct").map(_afterhours_spread_text)
    display["vs 收盘"] = frame.get("spread_vs_regular_close_pct").map(_percent_text)
    display["状态"] = frame.get("alert_level_cn").replace("", "暂缺")
    display["风险"] = frame.apply(lambda row: _risk_badge_text(row.to_dict()), axis=1)
    display["更新时间"] = frame.get("updated_at").map(_short_hkt_time)
    return display


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


def _render_row_details(rows: list[dict]) -> None:
    if not rows:
        return
    for row in rows:
        with st.expander(f"{str(row.get('ticker') or '').upper()} 行详情", expanded=False):
            col_anchor, col_binance, col_risk = st.columns(3)
            with col_anchor:
                st.markdown("**盘后锚点**")
                st.caption(f"周五收盘：{_money_text(row.get('regular_close_price') or row.get('friday_close'))}")
                st.caption(f"盘后参考价：{_money_text(row.get('afterhours_reference_price'))}")
                st.caption(f"盘后来源：{_afterhours_source_text(row.get('afterhours_reference_source'))}")
                st.caption(f"盘后质量：{_data_quality_text(row.get('afterhours_data_quality') or 'MISSING')}")
                st.caption(f"锚点状态：{_afterhours_anchor_badge(row)}")
                st.caption(f"抓取时间：{_short_hkt_time(row.get('afterhours_fetched_at'))}")
                st.caption(f"确认时间：{_short_hkt_time(row.get('afterhours_finalized_at'))}")
                st.caption(f"缺失原因：{_afterhours_reason_text(row.get('afterhours_missing_reason'))}")
                st.caption(f"缓存状态：{_afterhours_cache_text(row.get('afterhours_cache_status'))}")
                st.caption(f"盘后时间：{_short_hkt_time(row.get('afterhours_reference_time'))}")
            with col_binance:
                st.markdown("**Binance 行情**")
                st.caption(f"bid：{_money_text(row.get('binance_bid'))}")
                st.caption(f"ask：{_money_text(row.get('binance_ask'))}")
                st.caption(f"bid-ask spread：{_percent_text(row.get('binance_spread_pct'))}")
                st.caption(f"24h volume：{_plain_number(row.get('binance_volume_24h'))}")
                st.caption(f"funding：{_funding_text(row.get('funding_rate'))}")
            with col_risk:
                st.markdown("**风险说明**")
                st.caption(f"mapping_confidence：{str(row.get('mapping_confidence') or 'unknown')}")
                st.caption(str(row.get("mapping_risk") or ""))
                st.caption(str(row.get("liquidity_warning") or ""))
                st.caption(f"raw_error：{str(row.get('error') or '')}")


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
    display["候选"] = display["候选"].map(_candidate_text)
    display["配置 symbol"] = display["配置 symbol"].replace("", "暂无映射")
    display["校验时间"] = display["校验时间"].replace("", "未校验")
    return display


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
        return "缺少周五盘后参考价，当前按周五收盘价临时对比。"
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
        return "缺少周五盘后参考价，当前价差以周五正常收盘价为基准。"
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
