# ZHX Research Windows Launcher

Recommended daily launcher:

```text
launch_hidden.pyw
```

Double-clicking `launch_hidden.pyw` starts the local Streamlit service in the background and opens a dedicated ZHX Research Chrome app window:

```text
http://localhost:8501
```

It does not show a CMD or PowerShell window. Closing the dedicated app window stops the background Streamlit service and releases port `8501`.

## Daily Hidden Mode

Use:

```text
launch_hidden.pyw
```

Behavior:

1. Uses port `8501`.
2. If ZHX Research is already running, it opens a monitored app window and does not start another service.
3. If port `8501` is free, it starts `streamlit run app.py --server.port 8501 --server.headless true`.
4. It opens Google Chrome app mode first, with a dedicated profile and a wide high-DPI window.
5. If Chrome is missing, it falls back to Edge app mode. Native WebView is only used when `ZHX_RESEARCH_USE_NATIVE_WEBVIEW=1` is set.
6. When that app window is closed, it runs `stop_zhx.py` and stops the Streamlit service.
7. Streamlit logs are written to:

```text
C:\Users\User\AppData\Local\Temp\zhx_research_streamlit.log
```

The exact temp directory follows Windows `%TEMP%`, which is normally `C:\Users\User\AppData\Local\Temp`.

## Developer Mode

Use developer mode when you want to see Streamlit logs in a console:

```text
start_dev.bat
```

This keeps the console visible and runs:

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py --server.port 8501 --server.headless true
```

Press `Ctrl+C` in that console to stop the developer service.

The older `start_zhx_research.bat` is still available for compatibility, but the default daily launcher is now `launch_hidden.pyw`.

## Stop The Background Service

Use:

```text
stop_zhx.bat
```

The stop script checks port `8501` and stops the Streamlit/Python process that owns it.

It prints one of these messages:

- `已停止 ZHX Research 服务，已释放 8501 端口。`
- `未发现运行中的 ZHX Research 服务。`

## Notes

- Hidden mode is local only. It does not bundle the project into an EXE.
- Keep the project folder and `.venv` in place.
- Hidden mode does not read or print `.env`; API keys stay local.
- Repeated double-clicks do not start multiple Streamlit services.
- Auto-stop only applies to the dedicated app window opened by `launch_hidden.pyw`. If you manually open `http://localhost:8501` in a normal browser tab, closing that tab does not stop Streamlit; use `stop_zhx.bat`.
