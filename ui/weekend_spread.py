from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from data.equity_afterhours_provider import CachedAfterhoursProvider, NullAfterhoursProvider, default_afterhours_provider
from data.binance_provider import DEFAULT_BINANCE_CACHE_PATH, normalize_market_type
from data.weekend_spread_backtest import (
    build_weekend_backtest_preflight,
    clear_backtest_view_state,
    load_backtest_results,
    run_weekend_peak_short_backtest,
    save_backtest_results,
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
        status_slot.warning("Refresh failed; using last successful cache.")
        cache_status = dict(cached)
        cache_status["cache_state"] = "REFRESH_FAILED"
        cache_status["cache_message"] = error_message
        return fallback_rows, cache_status
    if is_provider_failure(rows):
        error_message = _refresh_error_text(rows)
        fallback_rows = build_weekend_spread_rows(
            watchlist,
            mapping=mapping,
            provider=_CacheOnlyBinanceProvider(),
            afterhours_provider=CachedAfterhoursProvider(NullAfterhoursProvider()),
            force_refresh=False,
        )
        if has_successful_price(fallback_rows):
            fallback_rows = annotate_cached_rows(fallback_rows, cache_state="REFRESH_FAILED", generated_at="")
            status_slot.warning("Refresh failed; using last good Binance price cache.")
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
    def __init__(self, *, cache_path: Path = DEFAULT_BINANCE_CACHE_PATH, ttl_seconds: int = 86_400) -> None:
        self.cache_path = cache_path
        self.ttl_seconds = ttl_seconds

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
        if updated_at is None or datetime.now(timezone.utc) - updated_at > timedelta(seconds=self.ttl_seconds):
            return None
        return {
            "symbol": str(raw.get("symbol") or cache_key.split(":", 1)[-1]),
            "last_price": raw.get("last_price"),
            "bid": raw.get("bid"),
            "ask": raw.get("ask"),
            "volume_24h": raw.get("volume_24h"),
            "funding_rate": raw.get("funding_rate"),
            "updated_at": str(raw.get("updated_at") or ""),
            "source": str(raw.get("source") or "binance_price_cache"),
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
            st.info("当前没有可拉取 Binance 价格的映射。先配置 ticker -> binance_symbol 后，系统会自动读取价格。")
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
        "USDT-M 合约数据不可用时不能计算观察收益；mapping 未 confirmed 时结果仅作观察。"
    )
    include_unconfirmed = st.checkbox(
        "包含未确认映射",
        value=False,
        key="weekend_backtest_include_unconfirmed",
        help="candidate 映射仅观察，默认不纳入正式胜率。",
    )
    anchors = _backtest_anchor_mapping()
    all_tickers = [str(ticker or "").upper() for ticker in watchlist if str(ticker or "").strip()]
    preliminary = build_weekend_backtest_preflight(
        all_tickers,
        mapping=mapping,
        anchors=anchors,
        include_unconfirmed=include_unconfirmed,
    )
    options = ["全部已映射"] + list(preliminary.get("eligible_tickers") or [])
    cols = st.columns(6)
    selected = cols[0].selectbox("ticker", options, key="weekend_backtest_ticker")
    weeks = int(cols[1].number_input("weeks", min_value=1, max_value=12, value=4, step=1, key="weekend_backtest_weeks"))
    open_window = int(cols[2].selectbox("open_window", [5, 15, 30], index=0, key="weekend_backtest_open_window"))
    fee_pct = cols[3].number_input("fee_pct", min_value=0.0, value=0.10, step=0.01, key="weekend_backtest_fee")
    slippage_pct = cols[4].number_input("slippage_pct", min_value=0.0, value=0.10, step=0.01, key="weekend_backtest_slippage")
    funding_pct = cols[5].number_input("funding_pct", value=0.00, step=0.01, key="weekend_backtest_funding")
    run_tickers = list(preliminary.get("eligible_tickers") or []) if selected == "全部已映射" else [selected]
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
    if clear_clicked:
        st.session_state["weekend_backtest_results"] = []
        st.session_state["weekend_backtest_cache"] = clear_backtest_view_state()
        st.info("已清空本次页面结果；不会删除已保存的历史回测缓存。")
    if run_clicked:
        tickers = list(preflight.get("eligible_tickers") or [])
        progress_bar = st.progress(0.0)
        status_slot = st.empty()
        status_slot.caption(f"正在运行历史回测：{len(tickers)} 个标的，{weeks} 周。")
        results = run_weekend_peak_short_backtest(
            tickers,
            mapping=mapping,
            anchors=anchors,
            weeks=weeks,
            open_window_minutes=open_window,
            fee_pct=fee_pct,
            slippage_pct=slippage_pct,
            funding_pct=funding_pct,
        )
        progress_bar.progress(1.0)
        failed = [row for row in results if str(row.get("data_quality") or "") in {"DATA_UNAVAILABLE", "INVALID"}]
        error_message = _backtest_error_message(failed)
        saved = save_backtest_results(
            results,
            preflight=preflight,
            params={
                "ticker": selected,
                "weeks": weeks,
                "open_window": open_window,
                "fee_pct": fee_pct,
                "slippage_pct": slippage_pct,
                "funding_pct": funding_pct,
                "include_unconfirmed": include_unconfirmed,
            },
            error_message=error_message,
        )
        st.session_state["weekend_backtest_results"] = results
        st.session_state["weekend_backtest_cache"] = saved
        if error_message:
            status_slot.warning(error_message)
        else:
            status_slot.success(f"回测完成：{len(results)} 条结果。")
    cached_result = dict(st.session_state.get("weekend_backtest_cache") or load_backtest_results())
    results = list(st.session_state.get("weekend_backtest_results") or cached_result.get("rows") or [])
    if not results:
        if not preflight.get("can_run"):
            st.info(f"没有可回测标的：{_backtest_block_text(str(preflight.get('primary_block_reason') or 'NO_MAPPING'))}")
        elif cached_result.get("error_message"):
            st.warning(f"上次运行失败：{cached_result.get('error_message')}")
        else:
            st.info("尚未运行历史回测。配置映射后点击“运行近 4 周回测”。美股夜盘开盘参考使用 Sunday 20:00 ET。")
        _render_backtest_advanced_records()
        return
    last_run_at = str(cached_result.get("last_run_at") or "")
    if last_run_at:
        st.caption(f"last_run_at：{_short_hkt_time(last_run_at)}")
    if include_unconfirmed:
        st.caption("观察回测：包含未确认映射，结果不计为正式胜率。")
    _render_backtest_kpis(results)
    st.dataframe(_backtest_frame(results), width="stretch", hide_index=True)
    _render_backtest_advanced_records()


def _render_backtest_preflight(preflight: dict[str, object]) -> None:
    cols = st.columns(4)
    cols[0].metric("可回测标的", int(preflight.get("eligible_count") or 0))
    cols[1].metric("已排除标的", int(preflight.get("excluded_count") or 0))
    cols[2].metric("当前模式", str(preflight.get("mode") or "confirmed only"))
    cols[3].metric("数据源状态", "USDT-M Futures")


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


def _backtest_exclusion_frame(rows: list[dict]) -> pd.DataFrame:
    columns = [
        ("ticker", "ticker"),
        ("symbol", "symbol"),
        ("market_type", "market_type"),
        ("mapping_status", "mapping_status"),
        ("exclusion_reason", "exclusion_reason"),
    ]
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=[label for _, label in columns])
    display = pd.DataFrame()
    for key, label in columns:
        display[label] = frame.get(key)
    return display


def _backtest_error_message(rows: list[dict]) -> str:
    if not rows:
        return ""
    reasons = []
    for row in rows:
        quality = str(row.get("data_quality") or "")
        raw_error = str(row.get("error_message") or "")
        if quality == "DATA_UNAVAILABLE":
            reasons.append(f"{row.get('ticker')}: BINANCE_KLINE_UNAVAILABLE {raw_error}".strip())
        elif quality == "INVALID":
            reasons.append(f"{row.get('ticker')}: {raw_error or 'INVALID'}")
        elif raw_error:
            reasons.append(f"{row.get('ticker')}: {raw_error}")
    return "；".join(reasons[:5])


def _render_backtest_kpis(rows: list[dict]) -> None:
    summary = summarize_backtest_results(rows)
    cols = st.columns(8)
    cols[0].metric("近4周样本数", int(summary.get("sample_weeks") or 0))
    cols[1].metric("平均溢价抹平率", _percent_text(summary.get("avg_premium_decay_ratio")))
    cols[2].metric("平均理论空头收益", _percent_text(summary.get("avg_theoretical_short_return_pct")))
    cols[3].metric("平均净收益", _percent_text(summary.get("avg_net_return_pct")))
    cols[4].metric("正收益周数", int(summary.get("positive_weeks") or 0))
    cols[5].metric("胜率", _ratio_text(summary.get("win_rate")))
    cols[6].metric("最大溢价抹平", _percent_text(summary.get("max_premium_decay_pct")))
    cols[7].metric("最大未抹平风险", _percent_text(summary.get("max_unflattened_risk_pct")))


def _backtest_anchor_mapping() -> dict[str, dict]:
    rows = list(st.session_state.get("weekend_realtime_rows") or [])
    result: dict[str, dict] = {}
    for row in rows:
        ticker = str(row.get("ticker") or "").upper()
        if not ticker:
            continue
        result[ticker] = {
            "afterhours_reference_price": row.get("afterhours_reference_price"),
            "regular_close_price": row.get("regular_close_price") or row.get("friday_close"),
            "friday_close": row.get("friday_close"),
        }
    return result


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
        "价格由 Binance API 自动读取；用户只维护 ticker -> binance_symbol mapping。"
        f"local 配置路径：{DEFAULT_LOCAL_MAPPING_PATH.as_posix()}，local 不提交 git，candidate 不等于 confirmed。"
    )
    st.dataframe(_mapping_management_frame(rows, mapping), width="stretch", hide_index=True)
    _render_mapping_editor(mapping, rows, mapping_counts, DEFAULT_LOCAL_MAPPING_PATH)
    _render_mapping_diagnostics(mapping)


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
        parts.append(f"fallback {fallback_count}")
    return "｜".join(parts)


def _mapping_management_counts(rows: list[dict], mapping: dict[str, dict]) -> dict[str, int]:
    counts = _mapping_counts(rows, mapping)
    local_items = [item for item in mapping.values() if item.get("enabled", True) and item.get("binance_symbol")]
    counts.update(
        {
            "confirmed_count": sum(1 for item in local_items if item.get("mapping_confidence") == "confirmed"),
            "candidate_count": sum(1 for item in local_items if item.get("mapping_confidence") == "candidate"),
            "no_mapping_count": sum(1 for row in rows if not row.get("binance_symbol")),
        }
    )
    return counts


def _should_show_empty_mapping_state(mapping_counts: dict[str, int], scope: str) -> bool:
    return mapping_counts.get("universe_mapping_count", 0) <= 0 and scope != "暂无 mapping"


def _empty_mapping_message(mapping_counts: dict[str, int], local_mapping_path: Path) -> str:
    lines = [
        "当前观察池暂无 Binance 映射。",
        "Binance 价格可通过 API 自动读取，但需要先配置 ticker -> binance_symbol。",
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
        confidence_options = ["candidate", "unverified", "confirmed"]
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
        ("week_id", "week_id"),
        ("ticker", "Ticker"),
        ("weekend_peak_time", "周末高点时间"),
        ("weekend_peak_premium_pct", "周末峰值溢价"),
        ("open_remaining_premium_pct", "开盘剩余溢价"),
        ("premium_decay_pct", "溢价抹平幅度"),
        ("premium_decay_ratio", "溢价抹平率"),
        ("theoretical_short_return_pct", "高点空到开盘理论收益"),
        ("net_short_return_pct", "扣费后收益"),
        ("data_quality", "data_quality"),
        ("warning", "exclusion / warning"),
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
    for percent_col in (
        "周末峰值溢价",
        "开盘剩余溢价",
        "溢价抹平幅度",
        "溢价抹平率",
        "高点空到开盘理论收益",
        "扣费后收益",
    ):
        display[percent_col] = display[percent_col].map(_percent_text)
    display["周末高点时间"] = display["周末高点时间"].replace("", "暂缺")
    return display


def _backtest_row_warning(row: dict) -> str:
    quality = str(row.get("data_quality") or "")
    error = str(row.get("error_message") or "")
    note = str(row.get("result_note") or "")
    if quality == "DATA_UNAVAILABLE":
        return f"BINANCE_KLINE_UNAVAILABLE {error}".strip()
    if quality == "INVALID":
        return error or "INVALID"
    if quality == "UNCONFIRMED_MAPPING":
        return note or "UNCONFIRMED_MAPPING"
    return note


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
                st.caption(f"盘后质量：{str(row.get('afterhours_data_quality') or 'MISSING')}")
                st.caption(f"锚点状态：{_afterhours_anchor_badge(row)}")
                st.caption(f"抓取时间：{_short_hkt_time(row.get('afterhours_fetched_at'))}")
                st.caption(f"finalized_at：{_short_hkt_time(row.get('afterhours_finalized_at'))}")
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
    return "Binance refresh failed"


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
        "NOT_FETCHED": "未抓取盘后价",
    }.get(code, code or "暂缺")


def _afterhours_anchor_badge(row: dict) -> str:
    if _number(row.get("afterhours_reference_price")) is None:
        return "盘后缺失 fallback"
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
        risks.append(f"缺少盘后参考价：{_afterhours_reason_text(row.get('afterhours_missing_reason'))}，当前使用周五收盘作为 fallback")
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
        return float(value)
    except (TypeError, ValueError):
        return None
