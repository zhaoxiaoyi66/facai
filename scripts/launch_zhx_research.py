from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


DEFAULT_PORT = 8501
DEFAULT_ROOT = Path(r"C:\dev\facai")
ENTRY_CANDIDATES = ("app.py", "streamlit_app.py", "main.py", r"ui\dashboard.py")
STARTUP_TIMEOUT_SECONDS = 30


def main() -> int:
    port = _resolve_port()
    url = f"http://localhost:{port}"

    try:
        project_root = _find_project_root()
        entry_file = _find_entry_file(project_root)
        python_exe = project_root / ".venv" / "Scripts" / "python.exe"
        _require_file(python_exe, "Python virtual environment")
    except RuntimeError as exc:
        print(f"[ZHX Research] {exc}", file=sys.stderr)
        return 1

    print(f"[ZHX Research] Project: {project_root}")
    print(f"[ZHX Research] Entry: {entry_file.relative_to(project_root)}")
    print(f"[ZHX Research] URL: {url}")

    if _is_http_ready(url):
        print("[ZHX Research] Streamlit is already running. Opening browser.")
        webbrowser.open(url, new=2)
        return 0

    if _is_tcp_port_open("127.0.0.1", port):
        print(
            f"[ZHX Research] Port {port} is open but did not return an HTTP response.",
            file=sys.stderr,
        )
        print("[ZHX Research] Close the process using this port, then try again.", file=sys.stderr)
        return 1

    log_path = Path(tempfile.gettempdir()) / "zhx_research_streamlit.log"
    command = [
        str(python_exe),
        "-m",
        "streamlit",
        "run",
        str(entry_file),
        "--server.port",
        str(port),
        "--server.headless",
        "true",
    ]

    print("[ZHX Research] Starting Streamlit...")
    try:
        log_file = log_path.open("a", encoding="utf-8", errors="replace")
        log_file.write("\n\n=== ZHX Research launcher start ===\n")
        log_file.flush()
        process = subprocess.Popen(
            command,
            cwd=project_root,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=_creation_flags(),
        )
    except OSError as exc:
        print(f"[ZHX Research] Failed to start Streamlit: {exc}", file=sys.stderr)
        return 1

    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if _is_http_ready(url):
            print("[ZHX Research] Streamlit is ready. Opening browser.")
            print(f"[ZHX Research] Log: {log_path}")
            webbrowser.open(url, new=2)
            log_file.close()
            return 0

        exit_code = process.poll()
        if exit_code is not None:
            log_file.close()
            print(f"[ZHX Research] Streamlit exited early with code {exit_code}.", file=sys.stderr)
            _print_log_tail(log_path)
            return 1

        time.sleep(0.5)

    log_file.close()
    print(
        f"[ZHX Research] Streamlit did not respond within {STARTUP_TIMEOUT_SECONDS} seconds.",
        file=sys.stderr,
    )
    print(f"[ZHX Research] Check the log: {log_path}", file=sys.stderr)
    _print_log_tail(log_path)
    return 1


def _resolve_port() -> int:
    raw_port = os.environ.get("ZHX_RESEARCH_PORT", "").strip()
    if not raw_port:
        return DEFAULT_PORT
    try:
        port = int(raw_port)
    except ValueError:
        print(f"[ZHX Research] Invalid ZHX_RESEARCH_PORT={raw_port!r}; using {DEFAULT_PORT}.")
        return DEFAULT_PORT
    if not 1 <= port <= 65535:
        print(f"[ZHX Research] Port out of range: {port}; using {DEFAULT_PORT}.")
        return DEFAULT_PORT
    return port


def _find_project_root() -> Path:
    candidates: list[Path] = []

    env_root = os.environ.get("ZHX_RESEARCH_ROOT", "").strip()
    if env_root:
        candidates.append(Path(env_root))

    candidates.append(Path.cwd())

    script_path = Path(__file__).resolve()
    candidates.extend([script_path.parent, script_path.parent.parent])

    executable_path = Path(sys.executable).resolve()
    candidates.extend([executable_path.parent, executable_path.parent.parent])

    candidates.append(DEFAULT_ROOT)

    for candidate in _unique_paths(candidates):
        if _looks_like_project_root(candidate):
            return candidate

    checked = ", ".join(str(path) for path in _unique_paths(candidates))
    raise RuntimeError(f"Could not locate project root. Checked: {checked}")


def _find_entry_file(project_root: Path) -> Path:
    for relative_path in ENTRY_CANDIDATES:
        candidate = project_root / relative_path
        if candidate.is_file():
            return candidate
    choices = ", ".join(ENTRY_CANDIDATES)
    raise RuntimeError(f"Could not find a Streamlit entry file. Expected one of: {choices}")


def _looks_like_project_root(path: Path) -> bool:
    return path.is_dir() and (path / "app.py").is_file() and (path / ".venv" / "Scripts" / "python.exe").is_file()


def _unique_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(resolved)
    return unique


def _require_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise RuntimeError(f"{label} not found: {path}")


def _is_http_ready(url: str) -> bool:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "ZHX-Research-Launcher"})
        with urllib.request.urlopen(request, timeout=2) as response:
            return 200 <= response.status < 500
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _is_tcp_port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def _creation_flags() -> int:
    return getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


def _print_log_tail(log_path: Path, max_lines: int = 40) -> None:
    if not log_path.is_file():
        return
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        print(f"[ZHX Research] Could not read log file: {exc}", file=sys.stderr)
        return

    tail = lines[-max_lines:]
    if not tail:
        return
    print("[ZHX Research] Last Streamlit log lines:", file=sys.stderr)
    for line in tail:
        print(line, file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
