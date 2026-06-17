from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


LOG_PATH = Path(tempfile.gettempdir()) / "zhx_research_streamlit.log"


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
            from scripts.launch_zhx_research import main as launch_main

            return int(launch_main())
        except Exception as exc:
            print(f"[ZHX Research] Hidden launcher failed: {exc}", file=log_file)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
