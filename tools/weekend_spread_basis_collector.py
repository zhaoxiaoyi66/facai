from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from data.weekend_spread_basis import collect_open_market_basis_once


LOG_PATH = PROJECT_ROOT / ".cache" / "weekend_spread_basis_collector.log"


def _append_log(payload: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = dict(payload)
    record["logged_at"] = datetime.now(timezone.utc).isoformat()
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect open-market basis samples for weekend spread.")
    parser.add_argument("--once", action="store_true", help="Run one collection pass.")
    parser.add_argument("--source", choices=["manual", "scheduler"], default="manual")
    parser.add_argument("--quiet", action="store_true", help="Write output to log only.")
    args = parser.parse_args(argv)

    result = collect_open_market_basis_once()
    payload = {"source": args.source, "result": result}
    _append_log(payload)
    if not args.quiet:
        print(result.get("message") or json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result.get("ok") or result.get("market_session") == "closed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
