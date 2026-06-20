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
    MONITOR_MODE_SCHEDULER,
    MONITOR_RUN_SOURCE_SCHEDULER,
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
    validation = _validate_task_paths()
    if not validation.get("ok"):
        return validation
    existing = _query_task(task_name=task_name)
    action = _scheduled_task_action(interval_minutes=interval_minutes, source="scheduler")
    task_command = _scheduled_task_command(interval_minutes=interval_minutes)
    result = _run_hidden(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", _register_task_script(task_name=task_name, action=action, interval_minutes=interval_minutes)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    ok = result.returncode == 0
    replaced = bool(existing.get("exists"))
    return {
        "ok": ok,
        "task_name": task_name,
        "task_command": task_command,
        "silent_mode": action["window_mode"] == "pythonw",
        "hidden_window": True,
        "replaced_existing": replaced,
        "run_mode_label": "Windows 任务计划 · 静默模式" if action["window_mode"] == "pythonw" else "Windows 任务计划 · 隐藏窗口模式",
        "message": ("已替换为静默后台任务。" if replaced else "已安装 3 分钟静默监控任务。") if ok else _task_error_message(result),
    }


def _change_task(*, task_name: str, enable: bool) -> dict[str, Any]:
    result = _run_hidden(
        ["schtasks", "/Change", "/TN", task_name, "/ENABLE" if enable else "/DISABLE"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    ok = result.returncode == 0
    return {"ok": ok, "task_name": task_name, "message": ("已恢复监控任务。" if enable else "已暂停监控任务。") if ok else _task_error_message(result)}


def _remove_task(*, task_name: str) -> dict[str, Any]:
    result = _run_hidden(["schtasks", "/Delete", "/TN", task_name, "/F"], capture_output=True, text=True, timeout=20)
    ok = result.returncode == 0
    return {"ok": ok, "task_name": task_name, "message": "已移除监控任务。" if ok else _task_error_message(result)}


def _query_task(*, task_name: str) -> dict[str, Any]:
    result = _run_hidden(["schtasks", "/Query", "/TN", task_name, "/FO", "LIST", "/V"], capture_output=True, text=True, timeout=20)
    ok = result.returncode == 0
    return {
        "ok": ok,
        "exists": ok,
        "task_name": task_name,
        "raw": result.stdout if ok else result.stderr,
        "message": "任务计划存在。" if ok else "任务计划不存在。",
    }


def _run_once(*, interval_minutes: float) -> dict[str, Any]:
    log_path = ROOT / ".cache" / "weekend_spread_monitor.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log_handle:
        result = _run_hidden(
            _monitor_subprocess_command(interval_minutes=interval_minutes, source="manual"),
            cwd=str(ROOT),
            stdout=log_handle,
            stderr=log_handle,
            text=True,
            timeout=180,
        )
    ok = result.returncode == 0
    return {"ok": ok, "message": "已完成一次静默监控扫描。" if ok else _task_error_message(result), "stdout": "", "stderr": ""}


def _scheduled_task_command(*, interval_minutes: float = DEFAULT_MONITOR_INTERVAL_MINUTES) -> str:
    action = _scheduled_task_action(interval_minutes=interval_minutes, source="scheduler")
    return f'"{action["execute"]}" {action["arguments"]}'


def _scheduled_task_action(*, interval_minutes: float = DEFAULT_MONITOR_INTERVAL_MINUTES, source: str = "scheduler") -> dict[str, str]:
    python_exe = _python_executable(prefer_windowless=True)
    return {
        "execute": str(python_exe),
        "arguments": _monitor_arguments(interval_minutes=interval_minutes, source=source, relative_script=True),
        "working_directory": str(ROOT),
        "window_mode": "pythonw" if python_exe.name.lower() == "pythonw.exe" else "hidden_window",
    }


def _monitor_subprocess_command(*, interval_minutes: float = DEFAULT_MONITOR_INTERVAL_MINUTES, source: str = "manual") -> list[str]:
    return [
        str(_python_executable(prefer_windowless=True)),
        str(ROOT / "tools" / "weekend_spread_monitor.py"),
        "--once",
        "--all",
        "--source",
        source,
        "--quiet",
        "--interval-minutes",
        str(interval_minutes),
    ]


def _monitor_arguments(*, interval_minutes: float, source: str, relative_script: bool) -> str:
    script = "tools\\weekend_spread_monitor.py" if relative_script else str(ROOT / "tools" / "weekend_spread_monitor.py")
    return f"{script} --once --all --source {source} --quiet --interval-minutes {interval_minutes:g}"


def _python_executable(*, prefer_windowless: bool = False) -> Path:
    scripts_dir = ROOT / ".venv" / "Scripts"
    if os.name == "nt" and prefer_windowless:
        pythonw = scripts_dir / "pythonw.exe"
        if pythonw.exists():
            return pythonw
    bundled = scripts_dir / "python.exe"
    if bundled.exists():
        return bundled
    return Path(sys.executable)


def _register_task_script(*, task_name: str, action: dict[str, str], interval_minutes: float) -> str:
    interval = max(1, int(round(interval_minutes)))
    return "\n".join(
        [
            f"$Action = New-ScheduledTaskAction -Execute '{_ps_quote(action['execute'])}' -Argument '{_ps_quote(action['arguments'])}' -WorkingDirectory '{_ps_quote(action['working_directory'])}'",
            f"$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes {interval}) -RepetitionDuration (New-TimeSpan -Days 3650)",
            "$Settings = New-ScheduledTaskSettingsSet -Hidden -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 2)",
            f"Register-ScheduledTask -TaskName '{_ps_quote(task_name)}' -Action $Action -Trigger $Trigger -Settings $Settings -Description 'facai weekend spread monitor silent task' -Force | Out-Null",
        ]
    )


def _ps_quote(value: str) -> str:
    return str(value).replace("'", "''")


def _run_hidden(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    if flags:
        kwargs.setdefault("creationflags", flags)
    return subprocess.run(command, **kwargs)


def _validate_task_paths() -> dict[str, Any]:
    python_exe = _python_executable(prefer_windowless=True)
    script = ROOT / "tools" / "weekend_spread_monitor.py"
    missing = []
    if not ROOT.exists():
        missing.append(str(ROOT))
    if not python_exe.exists():
        missing.append(str(python_exe))
    if not script.exists():
        missing.append(str(script))
    if missing:
        return {"ok": False, "message": "监控任务路径不存在：" + "；".join(missing), "missing_paths": missing}
    return {"ok": True}


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
    command_text = str(command or payload.get("command") or "")
    silent_mode = "pythonw.exe" in command_text.lower() or "--quiet" in command_text
    run_mode_label = "Windows 任务计划 · 静默模式" if "pythonw.exe" in command_text.lower() else "Windows 任务计划 · 隐藏窗口模式"
    payload.update(
        {
            "monitor_mode": MONITOR_MODE_SCHEDULER,
            "source": MONITOR_RUN_SOURCE_SCHEDULER,
            "enabled": enabled,
            "interval_minutes": interval_minutes,
            "task_name": task_name,
            "health_status": health_status,
            "health_reason": health_reason,
            "command": command_text,
            "silent_mode": silent_mode,
            "hidden_window": True,
            "run_mode_label": run_mode_label,
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
