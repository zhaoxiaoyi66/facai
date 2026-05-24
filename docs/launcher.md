# ZHX Research Windows Launcher

The recommended one-click launcher is:

```text
start_zhx_research.bat
```

You can also create a Windows Desktop shortcut named `ZHX Research` that points to this BAT file.

The launcher starts the local Streamlit app from `C:\dev\facai` by using:

```powershell
.\.venv\Scripts\python.exe
```

The Streamlit entry file is:

```text
app.py
```

## Use the BAT Launcher

Double-click:

```text
start_zhx_research.bat
```

The BAT file will:

1. Enter `C:\dev\facai`.
2. Run `scripts\launch_zhx_research.py` with `.venv\Scripts\python.exe`.
3. Open `http://localhost:8501` in the browser.

If `http://localhost:8501` is already responding, the launcher opens the browser directly and does not start another Streamlit process.

## Use a Desktop Shortcut

Create a shortcut with:

```text
Target: C:\dev\facai\start_zhx_research.bat
Start in: C:\dev\facai
```

Double-clicking the shortcut behaves the same as double-clicking the BAT file.

## Important Notes

This is a local launcher, not a full standalone installer.

Keep these in place:

- `C:\dev\facai`
- `C:\dev\facai\.venv`
- the project files and local data needed by the app

The launcher does not bundle:

- `.env`
- SQLite database files
- local caches
- the full project directory

No PyInstaller or EXE build is required for the recommended shortcut workflow.

## If Port 8501 Is Occupied

If `http://localhost:8501` is already running, the launcher opens it directly.

If port 8501 is occupied by something that is not responding as a web app, close that process and run the launcher again.

Advanced users can choose another port for the Python launcher by setting:

```powershell
$env:ZHX_RESEARCH_PORT = "8502"
.\.venv\Scripts\python.exe scripts\launch_zhx_research.py
```
