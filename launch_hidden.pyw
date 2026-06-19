from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


LOG_PATH = Path(tempfile.gettempdir()) / "zhx_research_streamlit.log"
BROWSER_PROFILE_DIR = Path(tempfile.gettempdir()) / "zhx_research_chrome_app_profile"
STARTUP_TIMEOUT_SECONDS = 30


def main() -> int:
    project_root = Path(__file__).resolve().parent
    os.chdir(project_root)
    os.environ["ZHX_RESEARCH_NO_WINDOW"] = "1"
    os.environ.setdefault("ZHX_RESEARCH_PORT", "8501")

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8", errors="replace", buffering=1) as log_file:
        log_file.write("\n\n=== ZHX Research hidden launcher ===\n")
        sys.stdout = log_file
        sys.stderr = log_file
        try:
            return _launch_and_monitor(project_root)
        except Exception as exc:
            print(f"[ZHX Research] Hidden launcher failed: {exc}", file=log_file)
            return 1


def _launch_and_monitor(project_root: Path) -> int:
    from scripts.launch_zhx_research import (
        _find_entry_file,
        _is_http_ready,
        _is_tcp_port_open,
        _print_log_tail,
        _require_file,
        _resolve_port,
    )

    port = _resolve_port()
    url = f"http://localhost:{port}"
    entry_file = _find_entry_file(project_root)
    python_exe = project_root / ".venv" / "Scripts" / "python.exe"
    _require_file(python_exe, "Python virtual environment")

    streamlit_process: subprocess.Popen | None = None
    if _is_http_ready(url):
        print("[ZHX Research] Streamlit already running. Opening monitored app window.")
    elif _is_tcp_port_open("127.0.0.1", port):
        print(f"[ZHX Research] Port {port} is open but HTTP is not ready.")
        return 1
    else:
        streamlit_process = _start_streamlit(project_root, python_exe, entry_file, port)
        if not _wait_until_ready(url, streamlit_process):
            print(f"[ZHX Research] Streamlit did not respond within {STARTUP_TIMEOUT_SECONDS} seconds.")
            _print_log_tail(LOG_PATH)
            return 1

    browser_process = _open_monitored_browser(url)
    if browser_process is not None:
        print("[ZHX Research] Chrome app window opened. Closing it will stop the background service.")
        browser_process.wait()
        print("[ZHX Research] Chrome app window closed. Stopping Streamlit service.")
        _stop_streamlit(project_root, python_exe, streamlit_process)
        return 0

    if _should_use_native_webview() and _open_native_webview(url):
        print("[ZHX Research] Native app window closed. Stopping Streamlit service.")
        _stop_streamlit(project_root, python_exe, streamlit_process)
        return 0

    if browser_process is None:
        print("[ZHX Research] Could not start a monitored app window; falling back to default browser.")
        import webbrowser

        webbrowser.open(url, new=2)
        return 0


def _start_streamlit(project_root: Path, python_exe: Path, entry_file: Path, port: int) -> subprocess.Popen:
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
    log_file = LOG_PATH.open("a", encoding="utf-8", errors="replace")
    log_file.write("\n\n=== ZHX Research Streamlit start ===\n")
    log_file.flush()
    return subprocess.Popen(
        command,
        cwd=project_root,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        creationflags=_no_window_flags(),
    )


def _wait_until_ready(url: str, process: subprocess.Popen | None) -> bool:
    from scripts.launch_zhx_research import _is_http_ready

    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if _is_http_ready(url):
            return True
        if process is not None and process.poll() is not None:
            return False
        time.sleep(0.5)
    return False


def _open_monitored_browser(url: str) -> subprocess.Popen | None:
    browser = _find_browser_executable()
    if browser is None:
        return None
    BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    _clear_browser_icon_cache()
    command = [
        str(browser),
        f"--app={url}",
        f"--user-data-dir={BROWSER_PROFILE_DIR}",
        "--window-size=1680,1050",
        "--high-dpi-support=1",
        "--no-first-run",
        "--disable-extensions",
    ]
    return subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=_no_window_flags())


def _clear_browser_icon_cache() -> None:
    """Clear the dedicated app profile favicon cache so page_icon changes appear quickly."""
    for name in ("Favicons", "Favicons-journal", "Favicons-shm", "Favicons-wal"):
        path = BROWSER_PROFILE_DIR / name
        try:
            if path.is_file():
                path.unlink()
        except OSError as exc:
            print(f"[ZHX Research] Could not clear browser icon cache {path}: {exc}")


def _should_use_native_webview() -> bool:
    return os.environ.get("ZHX_RESEARCH_USE_NATIVE_WEBVIEW", "").strip().lower() in {"1", "true", "yes", "on"}


def _open_native_webview(url: str) -> bool:
    try:
        import webview
    except Exception as exc:
        print(f"[ZHX Research] Native WebView is not available: {exc}")
        return False
    try:
        window = webview.create_window(
            "ZHX Research",
            url,
            width=1500,
            height=980,
            min_size=(1120, 720),
            text_select=True,
            confirm_close=False,
        )
        print("[ZHX Research] Opening native WebView app window.")
        webview.start(gui="edgechromium", debug=False)
        return window is not None
    except Exception as exc:
        print(f"[ZHX Research] Native WebView failed, falling back to browser app mode: {exc}")
        return False


def _find_browser_executable() -> Path | None:
    candidates = [
        Path(os.environ.get("PROGRAMFILES", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _stop_streamlit(project_root: Path, python_exe: Path, process: subprocess.Popen | None) -> None:
    stop_script = project_root / "stop_zhx.py"
    if stop_script.is_file():
        subprocess.run([str(python_exe), str(stop_script)], cwd=project_root, creationflags=_no_window_flags())
        return
    if process is not None and process.poll() is None:
        process.terminate()


def _no_window_flags() -> int:
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return flags


if __name__ == "__main__":
    raise SystemExit(main())
