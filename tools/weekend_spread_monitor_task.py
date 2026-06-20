from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.weekend_spread_monitor import (
    DEFAULT_MONITOR_INTERVAL_MINUTES,
    DEFAULT_MONITOR_STATUS_PATH,
    DEFAULT_MONITOR_TASK_NAME,
    HEALTH_NOT_STARTED,
    HEALTH_OK,
    HEALTH_PAUSED,
    read_monitor_status,
    write_monitor_status,
)


def main() -> int:
    args = _parse_args()
    result = run_task_command(args.command, interval_minutes=args.interval_minutes)
    print(result.get("message") or result)
    return 0 if result.get("ok") else 1


def run_task_command(
    command: str,
    *,
    task_name: str = DEFAULT_MONITOR_TASK_NAME,
    interval_minutes: float = DEFAULT_MONITOR_INTERVAL_MINUTES,
    status_path: Path = DEFAULT_MONITOR_STATUS_PATH,
) -> dict[str, Any]:
    command = str(command or "").strip().lower()
    if command == "run-once":
        return _run_once(interval_minutes=interval_minutes)
    if os.name != "nt":
        return {"ok": False, "unsupported": True, "message": "当前环境不支持任务计划，请使用手动扫描或命令行启动。"}
    if command == "install":
        result = _install_task(task_name=task_name, interval_minutes=interval_minutes)
        if result.get("ok"):
            _update_task_status(
                status_path,
                enabled=True,
                interval_minutes=interval_minutes,
                task_name=task_name,
                health_status=HEALTH_NOT_STARTED,
                health_reason="任务计划已安装，等待下一轮扫描。",
                command=result.get("task_command") or "",
            )
        return result
    if command == "pause":
        result = _change_task(task_name=task_name, enable=False)
        if result.get("ok"):
            _update_task_status(
                status_path,
                enabled=False,
                interval_minutes=interval_minutes,
                task_name=task_name,
                health_status=HEALTH_PAUSED,
                health_reason="监控任务已暂停。",
            )
        return result
    if command == "resume":
        result = _change_task(task_name=task_name, enable=True)
        if result.get("ok"):
            _update_task_status(
                status_path,
                enabled=True,
                interval_minutes=interval_minutes,
                task_name=task_name,
                health_status=HEALTH_OK,
                health_reason="监控任务已恢复，等待下一轮扫描。",
            )
        return result
    if command == "remove":
        result = _remove_task(task_name=task_name)
        if result.get("ok"):
            _update_task_status(
                status_path,
                enabled=False,
                interval_minutes=interval_minutes,
                task_name=task_name,
                health_status=HEALTH_NOT_STARTED,
                health_reason="监控任务已移除。",
            )
        return result
    if command == "status":
        return _query_task(task_name=task_name)
    return {"ok": False, "message": f"未知命令：{command}"}


def _install_task(*, task_name: str, interval_minutes: float) -> dict[str, Any]:
    task_command = _scheduled_task_command()
    result = subprocess.run(
        [
            "schtasks",
            "/Create",
            "/TN",
            task_name,
            "/SC",
            "MINUTE",
            "/MO",
            str(max(1, int(round(interval_minutes)))),
            "/TR",
            task_command,
            "/F",
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    ok = result.returncode == 0
    return {
        "ok": ok,
        "task_name": task_name,
        "task_command": task_command,
        "message": "已安装 3 分钟监控任务。" if ok else _task_error_message(result),
    }


def _change_task(*, task_name: str, enable: bool) -> dict[str, Any]:
    result = subprocess.run(
        ["schtasks", "/Change", "/TN", task_name, "/ENABLE" if enable else "/DISABLE"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    ok = result.returncode == 0
    return {"ok": ok, "task_name": task_name, "message": ("已恢复监控任务。" if enable else "已暂停监控任务。") if ok else _task_error_message(result)}


def _remove_task(*, task_name: str) -> dict[str, Any]:
    result = subprocess.run(["schtasks", "/Delete", "/TN", task_name, "/F"], capture_output=True, text=True, timeout=20)
    ok = result.returncode == 0
    return {"ok": ok, "task_name": task_name, "message": "已移除监控任务。" if ok else _task_error_message(result)}


def _query_task(*, task_name: str) -> dict[str, Any]:
    result = subprocess.run(["schtasks", "/Query", "/TN", task_name, "/FO", "LIST", "/V"], capture_output=True, text=True, timeout=20)
    ok = result.returncode == 0
    return {
        "ok": ok,
        "exists": ok,
        "task_name": task_name,
        "raw": result.stdout if ok else result.stderr,
        "message": "任务计划存在。" if ok else "任务计划不存在。",
    }


def _run_once(*, interval_minutes: float) -> dict[str, Any]:
    python_exe = _python_executable()
    script = ROOT / "tools" / "weekend_spread_monitor.py"
    result = subprocess.run(
        [str(python_exe), str(script), "--once", "--all", "--interval-minutes", str(interval_minutes)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=180,
    )
    ok = result.returncode == 0
    return {"ok": ok, "message": "已完成一次监控扫描。" if ok else _task_error_message(result), "stdout": result.stdout, "stderr": result.stderr}


def _scheduled_task_command() -> str:
    python_exe = _python_executable()
    script = ROOT / "tools" / "weekend_spread_monitor.py"
    return f'cmd /c "cd /d {ROOT} && {python_exe} {script} --once --all"'


def _python_executable() -> Path:
    bundled = ROOT / ".venv" / "Scripts" / "python.exe"
    if bundled.exists():
        return bundled
    return Path(sys.executable)


def _update_task_status(
    path: Path,
    *,
    enabled: bool,
    interval_minutes: float,
    task_name: str,
    health_status: str,
    health_reason: str,
    command: str = "",
) -> None:
    payload = read_monitor_status(path)
    payload.update(
        {
            "monitor_mode": "scheduler",
            "enabled": enabled,
            "interval_minutes": interval_minutes,
            "task_name": task_name,
            "health_status": health_status,
            "health_reason": health_reason,
            "command": command or payload.get("command") or "",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    write_monitor_status(payload, path)


def _task_error_message(result: subprocess.CompletedProcess[str]) -> str:
    text = (result.stderr or result.stdout or "").strip()
    return text or "任务计划操作失败。"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage the weekend spread monitor scheduled task.")
    parser.add_argument("command", choices=["install", "pause", "resume", "remove", "status", "run-once"])
    parser.add_argument("--interval-minutes", type=float, default=DEFAULT_MONITOR_INTERVAL_MINUTES)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
