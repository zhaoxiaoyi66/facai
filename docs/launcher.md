# ZHX Research Windows Launcher

Recommended daily launcher:

```text
launch_hidden.pyw
```

Double-clicking `launch_hidden.pyw` starts the local Streamlit service in the background and opens:

```text
http://localhost:8501
```

It does not show a CMD or PowerShell window.

## Daily Hidden Mode

Use:

```text
launch_hidden.pyw
```

Behavior:

1. Uses port `8501`.
2. If ZHX Research is already running, it opens the browser and does not start another service.
3. If port `8501` is free, it starts `streamlit run app.py --server.port 8501 --server.headless true`.
4. Streamlit logs are written to:

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
