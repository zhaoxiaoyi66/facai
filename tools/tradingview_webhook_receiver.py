from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from data.tradingview_price_cache import record_tradingview_webhook


class TradingViewWebhookHandler(BaseHTTPRequestHandler):
    server_version = "ZHXTradingViewWebhook/1.0"

    def do_POST(self) -> None:  # noqa: N802 - stdlib hook name
        if self.path not in {"/", "/tradingview"}:
            self._send_json(404, {"ok": False, "reason": "路径不存在"})
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = 0
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._send_json(400, {"ok": False, "reason": "JSON 无法解析"})
            return
        if not isinstance(payload, dict):
            self._send_json(400, {"ok": False, "reason": "JSON 必须是对象"})
            return
        result = record_tradingview_webhook(payload)
        if result.get("ok"):
            self._send_json(200, result)
            return
        reason = str(result.get("reason") or "")
        status = 403 if "secret" in reason.lower() else 400
        self._send_json(status, result)

    def do_GET(self) -> None:  # noqa: N802 - stdlib hook name
        self._send_json(
            200,
            {
                "ok": True,
                "service": "TradingView Webhook Receiver",
                "post": "/tradingview",
            },
        )

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        return

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser(description="TradingView webhook receiver for ZHX weekend spread cache.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), TradingViewWebhookHandler)
    print(f"TradingView webhook receiver listening on http://{args.host}:{args.port}/tradingview")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
