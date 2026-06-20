from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import time
from typing import Any, Iterable
from uuid import uuid4

from data.binance_provider import BinanceHTTPPriceProvider, BinancePriceProvider, CachedBinancePriceProvider
from data.weekend_spread_research import append_monitor_ticks, research_path_for_snapshot
from settings import PROJECT_ROOT


DEFAULT_MONITOR_SNAPSHOT_PATH = PROJECT_ROOT / "data" / "cache" / "weekend_spread_monitor_snapshots.json"
DEFAULT_MONITOR_STATUS_PATH = PROJECT_ROOT / "data" / "cache" / "weekend_spread_monitor_status.json"
DEFAULT_MONITOR_LOCK_PATH = PROJECT_ROOT / "data" / "cache" / "weekend_spread_monitor.lock"
DEFAULT_MONITOR_LOG_PATH = PROJECT_ROOT / ".cache" / "weekend_spread_monitor.log"
DEFAULT_MONITOR_INTERVAL_MINUTES = 3
DEFAULT_MONITOR_TASK_NAME = "facai_weekend_spread_monitor"
MONITOR_LOCK_STALE_MINUTES = 10
MONITOR_MODE_MANUAL_ONCE = "manual_once"
MONITOR_MODE_SCHEDULER = "scheduler"
MONITOR_MODE_LOOP_PROCESS = "loop_process"
MONITOR_MODE_STOPPED = "stopped"
MONITOR_MODE_UNKNOWN = "unknown"
MONITOR_RUN_SOURCE_MANUAL = "manual"
MONITOR_RUN_SOURCE_SCHEDULER = "scheduler"
MONITOR_RUN_SOURCE_LOOP = "loop"
HEALTH_OK = "正常"
HEALTH_MANUAL_COMPLETE = "手动扫描完成"
HEALTH_TASK_RUNNING = "任务监控运行中"
HEALTH_LOOP_RUNNING = "后台进程运行中"
HEALTH_NOT_STARTED = "未启动"
HEALTH_PAUSED = "已暂停"
HEALTH_STALE = "疑似失效"
HEALTH_FAILED = "最近失败"
HEALTH_UNKNOWN = "状态未知"
HEALTH_SCANNING = "扫描中"
MONITOR_SOURCE = "BINANCE_USDT_M"
TREND_WAITING = "等待下一轮比较"
TREND_WAIT_MORE = "等待更多样本"
TREND_STABLE = "价差稳定"
TREND_PREMIUM_EXPAND = "溢价扩大"
TREND_PREMIUM_CONVERGE = "溢价收敛"
TREND_DISCOUNT_EXPAND = "折价扩大"
TREND_DISCOUNT_CONVERGE = "折价收敛"
TREND_REVERSAL = "方向反转"

PRIORITY_HIGH = "高优先级"
PRIORITY_MEDIUM = "中优先级"
PRIORITY_LOW = "低优先级"
PRIORITY_WATCH = "仅观察"
PRIORITY_INSUFFICIENT = "数据不足"


def run_monitor_scan(
    source_rows: Iterable[dict[str, Any]],
    *,
    price_provider: BinancePriceProvider | None = None,
    price_map: dict[str, float] | None = None,
    snapshot_path: Path = DEFAULT_MONITOR_SNAPSHOT_PATH,
    status_path: Path | None = None,
    lock_path: Path | None = None,
    log_path: Path | None = None,
    now: datetime | None = None,
    symbols: Iterable[str] | None = None,
    premium_alert_pct: float = 2.0,
    extreme_premium_pct: float = 5.0,
    price_change_alert_pct: float = 1.0,
    premium_change_alert_pct: float = 1.0,
    interval_minutes: float = DEFAULT_MONITOR_INTERVAL_MINUTES,
    monitor_mode: str = "manual",
    task_name: str = DEFAULT_MONITOR_TASK_NAME,
    persist_ticks: bool = True,
    research_db_path: Path | None = None,
    use_lock: bool = True,
    update_status: bool = True,
    source: str | None = None,
) -> dict[str, Any]:
    snapshot_path = Path(snapshot_path)
    status_path = Path(status_path) if status_path is not None else _status_path_for_snapshot(snapshot_path)
    lock_path = Path(lock_path) if lock_path is not None else _lock_path_for_snapshot(snapshot_path)
    log_path = Path(log_path) if log_path is not None else _log_path_for_snapshot(snapshot_path)
    scan_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    monitor_mode = normalize_monitor_mode(monitor_mode, source=source)
    run_source = monitor_source_for_mode(monitor_mode, source=source)
    run_id = uuid4().hex
    started_at_monotonic = time.monotonic()
    lock_acquired = False
    if use_lock:
        lock_result = acquire_monitor_lock(lock_path, now=scan_time)
        if not lock_result.get("acquired"):
            run = {
                "run_id": run_id,
                "scan_time": scan_time.isoformat(),
                "interval_minutes": interval_minutes,
                "rows": [],
                "summary": {
                    "scan_time": scan_time.isoformat(),
                    "valid_count": 0,
                    "anchor_missing_count": 0,
                    "ignored_count": 0,
                    "price_missing_count": 0,
                    "skipped_due_to_lock": True,
                    "reason": lock_result.get("reason") or "已有扫描正在进行，本轮跳过。",
                },
                "source": MONITOR_SOURCE,
                "run_source": run_source,
                "created_at": scan_time.isoformat(),
                "skipped_due_to_lock": True,
            }
            append_monitor_log(
                f"run_id={run['run_id']} source={run_source} status=skipped reason={run['summary']['reason']}",
                path=log_path,
                at=scan_time,
            )
            return run
        lock_acquired = True
    if update_status:
        mark_monitor_scan_started(
            status_path=status_path,
            interval_minutes=interval_minutes,
            monitor_mode=monitor_mode,
            source=run_source,
            task_name=task_name,
            started_at=scan_time,
        )
    append_monitor_log(
        f"run_id={run_id} source={run_source} mode={monitor_mode} status=started",
        path=log_path,
        at=scan_time,
    )
    try:
        run = _run_monitor_scan_unlocked(
            source_rows,
            price_provider=price_provider,
            price_map=price_map,
            snapshot_path=snapshot_path,
            now=scan_time,
            symbols=symbols,
            premium_alert_pct=premium_alert_pct,
            extreme_premium_pct=extreme_premium_pct,
            price_change_alert_pct=price_change_alert_pct,
            premium_change_alert_pct=premium_change_alert_pct,
            interval_minutes=interval_minutes,
            persist_ticks=persist_ticks,
            research_db_path=research_db_path,
            run_id=run_id,
        )
    except Exception as exc:
        duration_seconds = time.monotonic() - started_at_monotonic
        if update_status:
            mark_monitor_scan_failure(
                exc,
                status_path=status_path,
                interval_minutes=interval_minutes,
                monitor_mode=monitor_mode,
                source=run_source,
                task_name=task_name,
                failed_at=scan_time,
            )
        append_monitor_log(f"run_id={run_id} source={run_source} status=failed duration_seconds={duration_seconds:.2f} error={exc}", path=log_path, at=scan_time)
        raise
    finally:
        if lock_acquired:
            release_monitor_lock(lock_path)
    duration_seconds = time.monotonic() - started_at_monotonic
    if update_status:
        mark_monitor_scan_success(
            run,
            status_path=status_path,
            interval_minutes=interval_minutes,
            monitor_mode=monitor_mode,
            source=run_source,
            task_name=task_name,
            finished_at=scan_time,
        )
    summary = dict(run.get("summary") or {})
    append_monitor_log(
        (
            f"run_id={run.get('run_id')} source={run_source} status=success "
            f"duration_seconds={duration_seconds:.2f} valid={summary.get('valid_count', 0)} "
            f"ignored={summary.get('ignored_count', 0)} "
            f"anchor_missing={summary.get('anchor_missing_count', 0)} "
            f"price_missing={summary.get('price_missing_count', 0)} "
            f"error_count={1 if summary.get('tick_persist_error') else 0}"
        ),
        path=log_path,
        at=scan_time,
    )
    return run


def _run_monitor_scan_unlocked(
    source_rows: Iterable[dict[str, Any]],
    *,
    price_provider: BinancePriceProvider | None = None,
    price_map: dict[str, float] | None = None,
    snapshot_path: Path = DEFAULT_MONITOR_SNAPSHOT_PATH,
    now: datetime | None = None,
    symbols: Iterable[str] | None = None,
    premium_alert_pct: float = 2.0,
    extreme_premium_pct: float = 5.0,
    price_change_alert_pct: float = 1.0,
    premium_change_alert_pct: float = 1.0,
    interval_minutes: float = DEFAULT_MONITOR_INTERVAL_MINUTES,
    persist_ticks: bool = True,
    research_db_path: Path | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    scan_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    selected_symbols = {str(item or "").strip().upper() for item in (symbols or []) if str(item or "").strip()}
    rows = [_normalize_source_row(row) for row in source_rows or []]
    if selected_symbols:
        rows = [row for row in rows if row["ticker"] in selected_symbols or row["binance_symbol"] in selected_symbols]

    state = read_monitor_state(snapshot_path)
    previous_by_ticker = _latest_rows_by_ticker(state)
    history_by_ticker = _history_rows_by_ticker(state)
    prices = {str(key or "").strip().upper(): float(value) for key, value in (price_map or {}).items() if _number(value) is not None}
    if price_map is None:
        prices = fetch_bulk_usdm_prices(price_provider or CachedBinancePriceProvider(BinanceHTTPPriceProvider(), ttl_seconds=45))

    monitor_rows: list[dict[str, Any]] = []
    skipped = {"ignored": 0, "anchor_missing": 0, "price_missing": 0, "unavailable": 0}
    for row in rows:
        if row["ignored"]:
            skipped["ignored"] += 1
            continue
        if row["excluded"]:
            skipped["unavailable"] += 1
            continue
        if not row["ticker"] or not row["binance_symbol"]:
            skipped["unavailable"] += 1
            continue
        anchor_price = _number(row.get("anchor_price"))
        if anchor_price is None or anchor_price <= 0:
            skipped["anchor_missing"] += 1
            continue
        binance_price = prices.get(row["binance_symbol"])
        if binance_price is None or binance_price <= 0:
            skipped["price_missing"] += 1
            continue
        previous = previous_by_ticker.get(row["ticker"]) or {}
        monitor_rows.append(
            _build_monitor_row(
                row,
                binance_price=binance_price,
                previous=previous,
                previous_history=history_by_ticker.get(row["ticker"], []),
                scan_time=scan_time,
                premium_alert_pct=premium_alert_pct,
                extreme_premium_pct=extreme_premium_pct,
                price_change_alert_pct=price_change_alert_pct,
                premium_change_alert_pct=premium_change_alert_pct,
            )
        )

    run_id = run_id or uuid4().hex
    for item in monitor_rows:
        item["run_id"] = run_id
    run = {
        "run_id": run_id,
        "scan_time": scan_time.isoformat(),
        "interval_minutes": interval_minutes,
        "rows": sorted(monitor_rows, key=lambda item: (-abs(_number(item.get("premium_pct")) or 0), item.get("ticker") or "")),
        "summary": summarize_monitor_rows(monitor_rows, skipped=skipped, scan_time=scan_time),
        "source": MONITOR_SOURCE,
        "created_at": scan_time.isoformat(),
    }
    if persist_ticks:
        try:
            append_monitor_ticks(
                run["rows"],
                db_path=research_db_path or research_path_for_snapshot(snapshot_path),
                run_id=run_id,
                scan_time=scan_time,
            )
        except Exception as exc:
            run["summary"]["tick_persist_error"] = str(exc)
    append_monitor_run(run, snapshot_path)
    return run


def fetch_bulk_usdm_prices(provider: BinancePriceProvider) -> dict[str, float]:
    providers = [provider]
    wrapped = getattr(provider, "provider", None)
    if wrapped is not None:
        providers.append(wrapped)
    for candidate in providers:
        getter = getattr(candidate, "_get_market_payload", None)
        if not callable(getter):
            continue
        try:
            payload = getter("usdm_futures", "price", {})
        except Exception:
            continue
        if not isinstance(payload, list):
            continue
        prices: dict[str, float] = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "").strip().upper()
            price = _number(item.get("price"))
            if symbol and price is not None and price > 0:
                prices[symbol] = price
        if prices:
            return prices
    return {}


def read_monitor_state(path: Path = DEFAULT_MONITOR_SNAPSHOT_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "runs": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "runs": [], "corrupted": True, "message": "监控快照损坏，请重新扫描。"}
    if not isinstance(payload, dict):
        return {"version": 1, "runs": [], "corrupted": True, "message": "监控快照损坏，请重新扫描。"}
    runs = payload.get("runs")
    if not isinstance(runs, list):
        runs = []
    return {"version": 1, "runs": [run for run in runs if isinstance(run, dict)]}


def append_monitor_run(run: dict[str, Any], path: Path = DEFAULT_MONITOR_SNAPSHOT_PATH, *, keep_runs: int = 200) -> dict[str, Any]:
    state = read_monitor_state(path)
    runs = list(state.get("runs") or [])
    runs.append(dict(run))
    state = {"version": 1, "runs": runs[-keep_runs:]}
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(path, state)
    return state


def latest_monitor_run(path: Path = DEFAULT_MONITOR_SNAPSHOT_PATH) -> dict[str, Any] | None:
    runs = read_monitor_state(path).get("runs") or []
    return runs[-1] if runs else None


def recent_monitor_runs(path: Path = DEFAULT_MONITOR_SNAPSHOT_PATH, *, limit: int = 10) -> list[dict[str, Any]]:
    runs = list(read_monitor_state(path).get("runs") or [])
    return list(reversed(runs[-limit:]))


def read_monitor_status(path: Path = DEFAULT_MONITOR_STATUS_PATH) -> dict[str, Any]:
    if not Path(path).exists():
        return {}
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError):
        return {"health_status": HEALTH_UNKNOWN, "health_reason": "监控状态文件损坏。"}
    return payload if isinstance(payload, dict) else {"health_status": HEALTH_UNKNOWN, "health_reason": "监控状态文件格式异常。"}


def write_monitor_status(payload: dict[str, Any], path: Path = DEFAULT_MONITOR_STATUS_PATH) -> dict[str, Any]:
    data = dict(payload or {})
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(path, data)
    return data


def normalize_monitor_mode(monitor_mode: str | None = None, *, source: str | None = None) -> str:
    source_text = str(source or "").strip().lower()
    mode_text = str(monitor_mode or "").strip().lower()
    if source_text == MONITOR_RUN_SOURCE_MANUAL:
        return MONITOR_MODE_MANUAL_ONCE
    if source_text == MONITOR_RUN_SOURCE_SCHEDULER:
        return MONITOR_MODE_SCHEDULER
    if source_text == MONITOR_RUN_SOURCE_LOOP:
        return MONITOR_MODE_LOOP_PROCESS
    if mode_text in {"manual", MONITOR_MODE_MANUAL_ONCE}:
        return MONITOR_MODE_MANUAL_ONCE
    if mode_text == MONITOR_MODE_SCHEDULER:
        return MONITOR_MODE_SCHEDULER
    if mode_text in {"loop", MONITOR_MODE_LOOP_PROCESS}:
        return MONITOR_MODE_LOOP_PROCESS
    if mode_text == MONITOR_MODE_STOPPED:
        return MONITOR_MODE_STOPPED
    if mode_text:
        return mode_text
    return MONITOR_MODE_UNKNOWN


def monitor_source_for_mode(monitor_mode: str | None = None, *, source: str | None = None) -> str:
    source_text = str(source or "").strip().lower()
    if source_text in {MONITOR_RUN_SOURCE_MANUAL, MONITOR_RUN_SOURCE_SCHEDULER, MONITOR_RUN_SOURCE_LOOP}:
        return source_text
    mode = normalize_monitor_mode(monitor_mode)
    if mode == MONITOR_MODE_SCHEDULER:
        return MONITOR_RUN_SOURCE_SCHEDULER
    if mode == MONITOR_MODE_LOOP_PROCESS:
        return MONITOR_RUN_SOURCE_LOOP
    return MONITOR_RUN_SOURCE_MANUAL


def is_monitor_snapshot_fresh(last_success_at: object, interval_minutes: float, now: datetime | None = None) -> bool | None:
    last_success = _parse_utc_time(last_success_at)
    if last_success is None:
        return None
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    elapsed = (current - last_success).total_seconds() / 60
    return elapsed <= float(interval_minutes or DEFAULT_MONITOR_INTERVAL_MINUTES) * 2.5


def mark_monitor_scan_started(
    *,
    status_path: Path,
    interval_minutes: float,
    monitor_mode: str,
    source: str | None = None,
    task_name: str,
    started_at: datetime,
) -> dict[str, Any]:
    monitor_mode = normalize_monitor_mode(monitor_mode, source=source)
    run_source = monitor_source_for_mode(monitor_mode, source=source)
    payload = read_monitor_status(status_path)
    payload.update(
        {
            "monitor_mode": monitor_mode,
            "source": run_source,
            "enabled": monitor_mode != MONITOR_MODE_MANUAL_ONCE,
            "interval_minutes": interval_minutes,
            "task_name": task_name,
            "last_started_at": started_at.isoformat(),
            "health_status": HEALTH_SCANNING,
            "health_reason": "正在执行本轮扫描。",
        }
    )
    return write_monitor_status(payload, status_path)


def mark_monitor_scan_success(
    run: dict[str, Any],
    *,
    status_path: Path,
    interval_minutes: float,
    monitor_mode: str,
    source: str | None = None,
    task_name: str,
    finished_at: datetime,
) -> dict[str, Any]:
    summary = dict(run.get("summary") or {})
    monitor_mode = normalize_monitor_mode(monitor_mode, source=source)
    run_source = monitor_source_for_mode(monitor_mode, source=source)
    is_manual = monitor_mode == MONITOR_MODE_MANUAL_ONCE
    next_expected = None if is_manual else finished_at + timedelta(minutes=float(interval_minutes or DEFAULT_MONITOR_INTERVAL_MINUTES))
    payload = read_monitor_status(status_path)
    payload.update(
        {
            "monitor_mode": monitor_mode,
            "source": run_source,
            "enabled": not is_manual,
            "interval_minutes": interval_minutes,
            "task_name": task_name,
            "last_finished_at": finished_at.isoformat(),
            "last_success_at": finished_at.isoformat(),
            "last_scan_run_id": run.get("run_id") or "",
            "last_scan_valid_count": int(summary.get("valid_count") or 0),
            "last_scan_skipped_count": int(summary.get("ignored_count") or 0) + int(summary.get("anchor_missing_count") or 0),
            "last_scan_error_count": 1 if summary.get("tick_persist_error") else 0,
            "next_expected_at": "" if next_expected is None else next_expected.isoformat(),
            "consecutive_failures": 0,
            "last_error": "",
            "health_status": HEALTH_MANUAL_COMPLETE if is_manual else HEALTH_OK,
            "health_reason": "这是一次手动扫描，不会自动继续。" if is_manual else "最近一轮扫描成功。",
        }
    )
    return write_monitor_status(payload, status_path)


def mark_monitor_scan_failure(
    exc: Exception | str,
    *,
    status_path: Path,
    interval_minutes: float,
    monitor_mode: str,
    source: str | None = None,
    task_name: str,
    failed_at: datetime,
) -> dict[str, Any]:
    monitor_mode = normalize_monitor_mode(monitor_mode, source=source)
    run_source = monitor_source_for_mode(monitor_mode, source=source)
    payload = read_monitor_status(status_path)
    failures = int(payload.get("consecutive_failures") or 0) + 1
    payload.update(
        {
            "monitor_mode": monitor_mode,
            "source": run_source,
            "enabled": monitor_mode != MONITOR_MODE_MANUAL_ONCE,
            "interval_minutes": interval_minutes,
            "task_name": task_name,
            "last_failure_at": failed_at.isoformat(),
            "last_finished_at": failed_at.isoformat(),
            "last_error": str(exc),
            "consecutive_failures": failures,
            "health_status": HEALTH_FAILED,
            "health_reason": f"最近扫描失败，连续失败 {failures} 次。",
        }
    )
    return write_monitor_status(payload, status_path)


def evaluate_monitor_health(
    status: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
    scheduler_exists: bool | None = None,
) -> dict[str, Any]:
    payload = dict(status or {})
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    monitor_mode = normalize_monitor_mode(payload.get("monitor_mode"), source=payload.get("source"))
    if not payload:
        return {
            "health_status": HEALTH_NOT_STARTED,
            "health_reason": "尚未安装或运行周末价差监控。",
            "minutes_since_success": None,
            "next_expected_at": "",
        }
    if scheduler_exists is False and monitor_mode == MONITOR_MODE_SCHEDULER:
        return {**payload, "health_status": HEALTH_NOT_STARTED, "health_reason": "任务计划不存在，请重新安装 3 分钟监控任务。"}
    failures = int(payload.get("consecutive_failures") or 0)
    if failures >= 3:
        return {**payload, "health_status": HEALTH_FAILED, "health_reason": f"连续失败 {failures} 次，请查看最近错误。"}
    interval = float(payload.get("interval_minutes") or DEFAULT_MONITOR_INTERVAL_MINUTES)
    last_success = _parse_utc_time(payload.get("last_success_at"))
    if last_success is None:
        if payload.get("last_failure_at") or failures:
            return {**payload, "health_status": HEALTH_FAILED, "health_reason": "尚无成功扫描，最近一次运行失败。"}
        if payload.get("enabled") is False and monitor_mode != MONITOR_MODE_MANUAL_ONCE:
            return {**payload, "health_status": HEALTH_PAUSED, "health_reason": "监控任务已暂停。"}
        return {**payload, "health_status": HEALTH_NOT_STARTED, "health_reason": "尚未产生成功扫描。"}
    elapsed = (current - last_success).total_seconds() / 60
    if monitor_mode == MONITOR_MODE_MANUAL_ONCE:
        return {
            **payload,
            "monitor_mode": monitor_mode,
            "source": MONITOR_RUN_SOURCE_MANUAL,
            "minutes_since_success": elapsed,
            "next_expected_at": "",
            "health_status": HEALTH_MANUAL_COMPLETE,
            "health_reason": "当前展示的是手动扫描结果。系统不会自动更新，除非安装 3 分钟监控任务。",
        }
    if payload.get("enabled") is False:
        return {**payload, "health_status": HEALTH_PAUSED, "health_reason": "监控任务已暂停。"}
    next_expected = last_success + timedelta(minutes=interval)
    enriched = {
        **payload,
        "monitor_mode": monitor_mode,
        "source": monitor_source_for_mode(monitor_mode, source=payload.get("source")),
        "minutes_since_success": elapsed,
        "next_expected_at": payload.get("next_expected_at") or next_expected.isoformat(),
    }
    if is_monitor_snapshot_fresh(last_success, interval, now=current):
        if monitor_mode == MONITOR_MODE_SCHEDULER:
            return {**enriched, "health_status": HEALTH_TASK_RUNNING, "health_reason": "3 分钟监控任务运行中，页面正在读取最近快照。"}
        if monitor_mode == MONITOR_MODE_LOOP_PROCESS:
            return {**enriched, "health_status": HEALTH_LOOP_RUNNING, "health_reason": "后台监控进程运行中，页面正在读取最近快照。"}
        return {**enriched, "health_status": HEALTH_OK, "health_reason": "最近成功扫描仍在预期间隔内。"}
    return {
        **enriched,
        "health_status": HEALTH_STALE,
        "health_reason": f"最近成功扫描已超过 {interval * 2.5:.1f} 分钟预期间隔，监控可能已经停止。",
    }


def acquire_monitor_lock(path: Path = DEFAULT_MONITOR_LOCK_PATH, *, now: datetime | None = None, stale_minutes: float = MONITOR_LOCK_STALE_MINUTES) -> dict[str, Any]:
    path = Path(path)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if path.exists():
        payload = read_monitor_status(path)
        started = _parse_utc_time(payload.get("started_at"))
        if started is not None and (current - started).total_seconds() / 60 <= stale_minutes:
            return {"acquired": False, "reason": "已有扫描正在进行，本轮跳过。", "pid": payload.get("pid")}
        try:
            path.unlink()
        except OSError:
            return {"acquired": False, "reason": "扫描锁仍被占用，本轮跳过。"}
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(path, {"pid": os.getpid(), "started_at": current.isoformat()})
    return {"acquired": True, "pid": os.getpid(), "started_at": current.isoformat()}


def release_monitor_lock(path: Path = DEFAULT_MONITOR_LOCK_PATH) -> None:
    try:
        Path(path).unlink()
    except FileNotFoundError:
        return
    except OSError:
        return


def append_monitor_log(message: str, *, path: Path = DEFAULT_MONITOR_LOG_PATH, at: datetime | None = None) -> None:
    timestamp = (at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{timestamp} {message}\n")
    except OSError:
        return


def _status_path_for_snapshot(snapshot_path: Path) -> Path:
    if Path(snapshot_path) == DEFAULT_MONITOR_SNAPSHOT_PATH:
        return DEFAULT_MONITOR_STATUS_PATH
    return Path(snapshot_path).with_name("weekend_spread_monitor_status.json")


def _lock_path_for_snapshot(snapshot_path: Path) -> Path:
    if Path(snapshot_path) == DEFAULT_MONITOR_SNAPSHOT_PATH:
        return DEFAULT_MONITOR_LOCK_PATH
    return Path(snapshot_path).with_name("weekend_spread_monitor.lock")


def _log_path_for_snapshot(snapshot_path: Path) -> Path:
    if Path(snapshot_path) == DEFAULT_MONITOR_SNAPSHOT_PATH:
        return DEFAULT_MONITOR_LOG_PATH
    return Path(snapshot_path).with_name("weekend_spread_monitor.log")


def summarize_monitor_rows(rows: list[dict[str, Any]], *, skipped: dict[str, int] | None = None, scan_time: datetime | None = None) -> dict[str, Any]:
    skipped = skipped or {}
    return {
        "scan_time": (scan_time or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(),
        "valid_count": len(rows),
        "anchor_missing_count": int(skipped.get("anchor_missing") or 0),
        "ignored_count": int(skipped.get("ignored") or 0),
        "price_missing_count": int(skipped.get("price_missing") or 0),
        "extreme_count": sum(1 for row in rows if row.get("status") == "极端价差"),
        "direction_reversal_count": sum(1 for row in rows if row.get("premium_trend_label") == TREND_REVERSAL),
        "attention_count": sum(
            1
            for row in rows
            if row.get("status")
            in {
                "重点关注",
                "极端价差",
                TREND_PREMIUM_EXPAND,
                TREND_DISCOUNT_EXPAND,
                TREND_REVERSAL,
            }
        ),
        "top": build_monitor_top(rows),
    }


def build_monitor_top(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any] | None]:
    priced = [row for row in rows if _number(row.get("premium_pct")) is not None]
    with_prev = [row for row in rows if _number(row.get("binance_change_since_last_pct")) is not None]
    with_delta = [row for row in rows if _number(row.get("premium_change_since_last_pct_point")) is not None]
    premium_rows = [row for row in with_delta if (_number(row.get("premium_pct")) or 0) > 0]
    prioritized = sorted((build_monitor_priority(row) for row in rows), key=_priority_sort_key)
    return {
        "max_premium": max(priced, key=lambda row: _number(row.get("premium_pct")) or float("-inf"), default=None),
        "max_discount": min(priced, key=lambda row: _number(row.get("premium_pct")) or float("inf"), default=None),
        "max_binance_change": max(with_prev, key=lambda row: _number(row.get("binance_change_since_last_pct")) or float("-inf"), default=None),
        "fastest_premium_expand": max(with_delta, key=lambda row: _number(row.get("premium_change_since_last_pct_point")) or float("-inf"), default=None),
        "fastest_premium_converge": min(
            premium_rows or with_delta,
            key=lambda row: _number(row.get("premium_change_since_last_pct_point")) or float("inf"),
            default=None,
        ),
        "direction_reversal_count": sum(1 for row in rows if row.get("premium_trend_label") == TREND_REVERSAL),
        "top_priority": prioritized[0] if prioritized else None,
        "high_priority_count": sum(1 for row in prioritized if row.get("monitor_priority_label") == PRIORITY_HIGH),
        "medium_priority_count": sum(1 for row in prioritized if row.get("monitor_priority_label") == PRIORITY_MEDIUM),
    }


def monitor_history_rows(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    for run in runs or []:
        summary = dict(run.get("summary") or {})
        top = dict(summary.get("top") or {})
        history.append(
            {
                "scan_time": run.get("scan_time") or summary.get("scan_time") or "",
                "valid_count": summary.get("valid_count") or 0,
                "max_premium": _top_label(top.get("max_premium"), "premium_pct"),
                "max_discount": _top_label(top.get("max_discount"), "premium_pct"),
                "max_since_last_change": _top_label(top.get("max_binance_change"), "binance_change_since_last_pct"),
                "max_premium_expand": _top_label(top.get("fastest_premium_expand"), "premium_change_since_last_pct_point", suffix=" pct"),
                "direction_reversal_count": top.get("direction_reversal_count") or 0,
                "attention_count": summary.get("attention_count") or 0,
            }
        )
    return history


def _build_monitor_row(
    row: dict[str, Any],
    *,
    binance_price: float,
    previous: dict[str, Any],
    previous_history: list[dict[str, Any]],
    scan_time: datetime,
    premium_alert_pct: float,
    extreme_premium_pct: float,
    price_change_alert_pct: float,
    premium_change_alert_pct: float,
) -> dict[str, Any]:
    anchor_price = float(row["anchor_price"])
    premium_pct = (binance_price / anchor_price - 1) * 100
    previous_price = _number(previous.get("binance_price"))
    previous_premium = _number(previous.get("premium_pct"))
    previous_scan_time = _parse_utc_time(previous.get("scan_time"))
    elapsed_minutes = (scan_time - previous_scan_time).total_seconds() / 60 if previous_scan_time else None
    binance_change = (binance_price / previous_price - 1) * 100 if previous_price and previous_price > 0 else None
    premium_change = premium_pct - previous_premium if previous_premium is not None else None
    regular_close_price = _number(row.get("regular_close_price"))
    vs_regular_close_pct = (binance_price / regular_close_price - 1) * 100 if regular_close_price and regular_close_price > 0 else None
    trend_label = classify_premium_trend(previous_premium, premium_pct, premium_change)
    premium_change_9m, trend_9m = _multi_round_trend(previous_history, premium_pct, lookback_rounds=3)
    premium_change_15m, trend_15m = _multi_round_trend(previous_history, premium_pct, lookback_rounds=5)
    result = {
        "run_id": "",
        "scan_time": scan_time.isoformat(),
        "ticker": row["ticker"],
        "binance_symbol": row["binance_symbol"],
        "anchor_price": anchor_price,
        "anchor_time": row.get("anchor_time") or "",
        "binance_price": binance_price,
        "premium_pct": premium_pct,
        "regular_close_price": regular_close_price,
        "vs_regular_close_pct": vs_regular_close_pct,
        "atr14_pct": _number(row.get("atr14_pct")),
        "avg_range_20d_pct": _number(row.get("avg_range_20d_pct") or row.get("avg_range_20d")),
        "avg_range_20d": _number(row.get("avg_range_20d") or row.get("avg_range_20d_pct")),
        "spread_atr_ratio": _number(row.get("spread_atr_ratio")),
        "spread_range_ratio": _number(row.get("spread_range_ratio")),
        "spread_reasonableness": row.get("spread_reasonableness") or row.get("spread_reasonableness_label") or "",
        "spread_reasonableness_label": row.get("spread_reasonableness_label") or row.get("spread_reasonableness") or "",
        "volatility_status": row.get("volatility_status") or "",
        "news_label": row.get("news_label") or "",
        "previous_binance_price": previous_price,
        "binance_15m_change_pct": binance_change,
        "binance_change_since_last_pct": binance_change,
        "previous_premium_pct": previous_premium,
        "premium_15m_change_pct": premium_change,
        "premium_change_since_last_pct": premium_change,
        "premium_change_since_last_pct_point": premium_change,
        "premium_change_3m_pct_point": premium_change,
        "premium_change_9m_pct_point": premium_change_9m,
        "premium_change_15m_pct_point": premium_change_15m,
        "premium_trend_label": trend_label,
        "trend_3m_label": trend_label,
        "trend_9m_label": trend_9m,
        "trend_15m_label": trend_15m,
        "previous_scan_time": previous.get("scan_time") or "",
        "elapsed_minutes": elapsed_minutes,
        "status": _monitor_status(
            premium_pct,
            binance_change,
            premium_change,
            trend_label,
            premium_alert_pct=premium_alert_pct,
            extreme_premium_pct=extreme_premium_pct,
            price_change_alert_pct=price_change_alert_pct,
            premium_change_alert_pct=premium_change_alert_pct,
        ),
        "source": MONITOR_SOURCE,
        "created_at": scan_time.isoformat(),
        "is_watchlist": bool(row.get("is_watchlist")),
        "is_position": bool(row.get("is_position")),
        "is_core": bool(row.get("is_core") or row.get("is_core_position")),
    }
    result.update(build_monitor_priority(result))
    return result


def build_monitor_priority(row: dict[str, Any]) -> dict[str, Any]:
    """Score a monitor row for observation priority only; this is not a trade signal."""

    premium = _number(row.get("premium_pct"))
    anchor = _number(row.get("anchor_price"))
    binance = _number(row.get("binance_price"))
    ratio = _priority_volatility_ratio(row)
    if premium is None or anchor is None or anchor <= 0 or binance is None or binance <= 0:
        return {
            **dict(row),
            "monitor_priority_score": 0,
            "monitor_priority_label": PRIORITY_INSUFFICIENT,
            "monitor_priority_reason": "缺少盘后锚点或 Binance 价格，无法进入高优先级观察。",
        }

    score = 0
    reason_parts: list[str] = []
    if ratio is None:
        data_score = 3
        reason_parts.append("缺少日常波动参照")
    else:
        data_score = 10
        if ratio < 0.5:
            score += 5
        elif ratio < 1.0:
            score += 15
        elif ratio < 1.5:
            score += 25
        elif ratio < 2.0:
            score += 30
        else:
            score += 35
        reason_parts.append(f"约 {ratio:.1f} 天日常波动")

    trend_score, trend_reason = _priority_trend_score(row)
    score += trend_score
    if trend_reason:
        reason_parts.append(trend_reason)

    news_score, news_reason = _priority_news_score(row)
    score += news_score
    if news_reason:
        reason_parts.append(news_reason)

    relevance_score, relevance_reason = _priority_relevance_score(row)
    score += relevance_score
    if relevance_reason:
        reason_parts.append(relevance_reason)

    score += data_score
    if data_score == 10:
        reason_parts.append("锚点、Binance 价格和波动数据可用")

    score = max(0, min(100, int(round(score))))
    label = _priority_label(score, ratio)
    if label == PRIORITY_HIGH:
        summary = "价差、趋势和解释缺口共同推高观察优先级。"
    elif label == PRIORITY_MEDIUM:
        summary = "价差或趋势值得观察，但还不是最高优先级。"
    elif label == PRIORITY_LOW:
        summary = "有一定价差或趋势，但相对日常波动并不突出。"
    elif label == PRIORITY_WATCH:
        summary = "当前仅作普通观察。"
    else:
        summary = "数据不足，暂时无法判断优先级。"
    return {
        **dict(row),
        "monitor_priority_score": score,
        "monitor_priority_label": label,
        "monitor_priority_reason": f"{summary}原因：{'；'.join(reason_parts)}。" if reason_parts else summary,
    }


def _priority_volatility_ratio(row: dict[str, Any]) -> float | None:
    ratio = _number(row.get("spread_atr_ratio"))
    if ratio is not None:
        return abs(ratio)
    range_ratio = _number(row.get("spread_range_ratio"))
    if range_ratio is not None:
        return abs(range_ratio)
    premium = _number(row.get("premium_pct"))
    avg_range = _number(row.get("avg_range_20d_pct") or row.get("avg_range_20d"))
    if premium is None or avg_range is None or avg_range <= 0:
        return None
    return abs(premium) / avg_range


def _priority_trend_score(row: dict[str, Any]) -> tuple[int, str]:
    trend = str(row.get("premium_trend_label") or "")
    trend_9m = str(row.get("trend_9m_label") or "")
    trend_15m = str(row.get("trend_15m_label") or "")
    expanding = {TREND_PREMIUM_EXPAND, TREND_DISCOUNT_EXPAND}
    converging = {TREND_PREMIUM_CONVERGE, TREND_DISCOUNT_CONVERGE}
    if trend in expanding and trend_9m in expanding:
        return 25, "近 9 分钟持续扩大"
    if trend in expanding and trend_15m in expanding:
        return 25, "近 15 分钟持续扩大"
    if trend in expanding:
        return 15, f"{trend}"
    if trend == TREND_REVERSAL:
        return 10, "价差方向反转"
    if trend in converging:
        return -10, f"{trend}"
    if trend == TREND_STABLE:
        return 3, "价差稳定"
    return 0, "趋势等待下一轮" if trend == TREND_WAITING else ""


def _priority_news_score(row: dict[str, Any]) -> tuple[int, str]:
    label = str(row.get("news_label") or row.get("closed_market_news_label") or "").strip()
    if not label or label == "未检查":
        return 0, "休市新闻未检查"
    if "观点文章" in label:
        return 8, "主要为观点文章"
    if "无新闻解释" in label or "无重大新闻" in label or label == "无新闻":
        return 15, "暂无重大休市新闻解释"
    if "新闻方向一致" in label or "重大新闻" in label or "有新闻解释" in label:
        return 0, "存在新闻解释，错价优先级降低"
    if "数据不足" in label:
        return 0, "休市新闻数据不足"
    return 5, f"休市新闻：{label}"


def _priority_relevance_score(row: dict[str, Any]) -> tuple[int, str]:
    if row.get("is_position") or row.get("is_core"):
        return 15, "持仓或核心仓"
    if row.get("is_watchlist"):
        return 10, "观察池标的"
    return 0, ""


def _priority_label(score: int, ratio: float | None) -> str:
    if ratio is None and score < 25:
        return PRIORITY_INSUFFICIENT
    if score >= 75:
        return PRIORITY_HIGH
    if score >= 50:
        return PRIORITY_MEDIUM
    if score >= 25:
        return PRIORITY_LOW
    return PRIORITY_WATCH


def _priority_sort_key(row: dict[str, Any]) -> tuple[int, float, float, str]:
    label_rank = {
        PRIORITY_HIGH: 0,
        PRIORITY_MEDIUM: 1,
        PRIORITY_LOW: 2,
        PRIORITY_WATCH: 3,
        PRIORITY_INSUFFICIENT: 4,
    }
    return (
        label_rank.get(str(row.get("monitor_priority_label") or ""), 5),
        -float(_number(row.get("monitor_priority_score")) or 0),
        -abs(_number(row.get("premium_pct")) or 0),
        str(row.get("ticker") or ""),
    )


def _monitor_status(
    premium_pct: float,
    binance_change_pct: float | None,
    premium_change_pct: float | None,
    trend_label: str,
    *,
    premium_alert_pct: float,
    extreme_premium_pct: float,
    price_change_alert_pct: float,
    premium_change_alert_pct: float,
) -> str:
    if abs(premium_pct) >= extreme_premium_pct:
        return "极端价差"
    if trend_label in {TREND_PREMIUM_EXPAND, TREND_PREMIUM_CONVERGE, TREND_DISCOUNT_EXPAND, TREND_DISCOUNT_CONVERGE, TREND_REVERSAL}:
        return trend_label
    if trend_label == TREND_WAITING:
        return TREND_WAITING
    if trend_label == TREND_STABLE:
        return TREND_STABLE
    if premium_change_pct is not None and abs(premium_change_pct) >= premium_change_alert_pct:
        return "重点关注"
    if binance_change_pct is not None and abs(binance_change_pct) >= price_change_alert_pct:
        return "重点关注"
    if abs(premium_pct) >= premium_alert_pct:
        return "重点关注"
    return "正常"


def classify_premium_trend(
    previous_premium_pct: float | None,
    current_premium_pct: float | None,
    change_pct_point: float | None,
    *,
    stable_threshold_pct_point: float = 0.20,
) -> str:
    if previous_premium_pct is None or current_premium_pct is None or change_pct_point is None:
        return TREND_WAITING
    if previous_premium_pct * current_premium_pct < 0:
        return TREND_REVERSAL
    if abs(change_pct_point) < stable_threshold_pct_point:
        return TREND_STABLE
    if current_premium_pct > 0 and change_pct_point >= stable_threshold_pct_point:
        return TREND_PREMIUM_EXPAND
    if current_premium_pct > 0 and change_pct_point <= -stable_threshold_pct_point:
        return TREND_PREMIUM_CONVERGE
    if current_premium_pct < 0 and change_pct_point <= -stable_threshold_pct_point:
        return TREND_DISCOUNT_EXPAND
    if current_premium_pct < 0 and change_pct_point >= stable_threshold_pct_point:
        return TREND_DISCOUNT_CONVERGE
    return TREND_STABLE


def _multi_round_trend(
    previous_history: list[dict[str, Any]],
    current_premium_pct: float,
    *,
    lookback_rounds: int,
) -> tuple[float | None, str]:
    if len(previous_history) < lookback_rounds:
        return None, TREND_WAIT_MORE
    baseline = previous_history[-lookback_rounds]
    baseline_premium = _number(baseline.get("premium_pct"))
    if baseline_premium is None:
        return None, TREND_WAIT_MORE
    change = current_premium_pct - baseline_premium
    return change, classify_premium_trend(baseline_premium, current_premium_pct, change)


def _normalize_source_row(row: dict[str, Any]) -> dict[str, Any]:
    other_tradfi = _is_other_tradfi_source(row)
    manual_locked = _is_manual_locked_source(row)
    return {
        "ticker": str(row.get("ticker") or "").strip().upper(),
        "binance_symbol": str(row.get("binance_symbol") or "").strip().upper(),
        "anchor_price": _number(row.get("afterhours_reference_price") or row.get("anchor_price")),
        "anchor_time": _normalize_time_text(row.get("afterhours_reference_time") or row.get("anchor_time") or ""),
        "regular_close_price": _number(row.get("regular_close_price") or row.get("friday_close")),
        "atr14_pct": _number(row.get("atr14_pct")),
        "avg_range_20d_pct": _number(row.get("avg_range_20d_pct") or row.get("avg_range_20d")),
        "avg_range_20d": _number(row.get("avg_range_20d") or row.get("avg_range_20d_pct")),
        "spread_atr_ratio": _number(row.get("spread_atr_ratio")),
        "spread_range_ratio": _number(row.get("spread_range_ratio")),
        "spread_reasonableness": str(row.get("spread_reasonableness") or row.get("spread_reasonableness_label") or ""),
        "spread_reasonableness_label": str(row.get("spread_reasonableness_label") or row.get("spread_reasonableness") or ""),
        "volatility_status": str(row.get("volatility_status") or ""),
        "news_label": str(row.get("closed_market_news_label") or row.get("news_label") or ""),
        "ignored": bool(row.get("ignored")),
        "excluded": other_tradfi and not manual_locked,
        "is_watchlist": bool(row.get("is_watchlist")),
        "is_position": bool(row.get("is_position")),
        "is_core": bool(row.get("is_core") or row.get("is_core_position")),
    }


def _is_manual_locked_source(row: dict[str, Any]) -> bool:
    quality = str(row.get("mapping_quality") or row.get("mapping_status") or "").strip()
    confidence = str(row.get("mapping_confidence") or row.get("mapping_status") or "").strip().lower()
    return bool(row.get("manually_locked")) or quality == "人工锁定" or confidence in {"confirmed", "人工锁定"}


def _is_other_tradfi_source(row: dict[str, Any]) -> bool:
    quality = str(row.get("mapping_quality") or row.get("mapping_status") or "").strip()
    bucket = str(row.get("tradfi_bucket") or "").strip().upper()
    underlying = str(row.get("underlying_type") or "").strip().upper()
    category = str(row.get("binance_category") or "").strip().upper()
    note = str(row.get("mapping_risk") or row.get("risk_note") or row.get("mapping_quality_reason") or row.get("reason") or "").upper()
    return (
        quality == "其他 TradFi"
        or bucket == "OTHER_TRADFI"
        or underlying in {"COIN", "COMMODITY", "KR_EQUITY", "INDEX", "PREMARKET"}
        or any(token in category for token in ("其他 TRADFI", "商品", "指数", "RWA", "KR EQUITY"))
        or any(token in note for token in ("其他 TRADFI", "非美股", "商品", "指数", "RWA", "KR EQUITY"))
    )


def _latest_rows_by_ticker(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    runs = list(state.get("runs") or [])
    if not runs:
        return {}
    rows = runs[-1].get("rows") if isinstance(runs[-1], dict) else []
    return {str(row.get("ticker") or "").strip().upper(): dict(row) for row in rows or [] if isinstance(row, dict)}


def _history_rows_by_ticker(state: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    history: dict[str, list[dict[str, Any]]] = {}
    for run in state.get("runs") or []:
        if not isinstance(run, dict):
            continue
        for row in run.get("rows") or []:
            if not isinstance(row, dict):
                continue
            ticker = str(row.get("ticker") or "").strip().upper()
            if ticker:
                history.setdefault(ticker, []).append(dict(row))
    return history


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    temp_path = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    last_error: OSError | None = None
    for attempt in range(5):
        try:
            os.replace(temp_path, path)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.05 * (attempt + 1))
    if last_error is not None:
        raise last_error


def _parse_utc_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        if text.isdigit():
            try:
                timestamp = int(text)
                if timestamp > 10_000_000_000:
                    timestamp = timestamp / 1000
                return datetime.fromtimestamp(timestamp, tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_time_text(value: Any) -> str:
    parsed = _parse_utc_time(value)
    return parsed.isoformat() if parsed is not None else str(value or "")


def _top_label(row: Any, metric_key: str, *, suffix: str = "%") -> str:
    if not isinstance(row, dict):
        return "暂无"
    ticker = str(row.get("ticker") or "").strip().upper() or "UNKNOWN"
    value = _number(row.get(metric_key))
    if value is None:
        return ticker
    return f"{ticker} {value:+.2f}{suffix}"


def _number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None
