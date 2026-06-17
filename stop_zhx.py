from __future__ import annotations

import subprocess
import sys


PORT = 8501


def main() -> int:
    script = rf"""
$port = {PORT}
$connections = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if (-not $connections) {{
    Write-Host '未发现运行中的 ZHX Research 服务。'
    exit 0
}}

$stopped = 0
foreach ($connection in $connections) {{
    $ownerId = [int]$connection.OwningProcess
    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$ownerId" -ErrorAction SilentlyContinue
    $commandLine = [string]($processInfo.CommandLine)
    $name = [string]($processInfo.Name)
    if ($commandLine -match 'streamlit' -or $commandLine -match 'app.py' -or $name -match 'python') {{
        Stop-Process -Id $ownerId -Force -ErrorAction SilentlyContinue
        $stopped += 1
    }} else {{
        Write-Warning "8501 端口被进程 $ownerId 占用，但未识别为 ZHX Research/Streamlit。"
    }}
}}

if ($stopped -gt 0) {{
    Write-Host '已停止 ZHX Research 服务，已释放 8501 端口。'
    exit 0
}}

Write-Host '未发现可停止的 ZHX Research 服务。'
exit 1
"""
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        text=True,
    )
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
