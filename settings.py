from __future__ import annotations

from pathlib import Path

from data.watchlist_store import get_watchlist_symbols
from data.watchlist_store import save_watchlist_entries

PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_DIR = PROJECT_ROOT / "config"
WATCHLIST_PATH = CONFIG_DIR / "watchlist.yaml"


DEFAULT_TICKERS = [
    "NOW",
    "ADBE",
    "CRM",
    "ORCL",
    "MSFT",
    "PLTR",
    "NVDA",
    "AVGO",
    "MU",
    "WDC",
    "STX",
    "COHR",
    "LITE",
    "FN",
    "VST",
    "CEG",
    "NRG",
    "ETN",
    "COIN",
    "HOOD",
]


def normalize_tickers(raw_tickers: list[str] | str) -> list[str]:
    if isinstance(raw_tickers, str):
        tokens = raw_tickers.replace(",", "\n").replace(";", "\n").splitlines()
    else:
        tokens = raw_tickers

    cleaned: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        ticker = str(token).strip().upper()
        if not ticker or ticker in seen:
            continue
        cleaned.append(ticker)
        seen.add(ticker)
    return cleaned


def load_watchlist(path: Path = WATCHLIST_PATH) -> list[str]:
    if not path.exists():
        save_watchlist(DEFAULT_TICKERS, path)
        return DEFAULT_TICKERS.copy()

    tickers = get_watchlist_symbols(path, default_symbols=DEFAULT_TICKERS)
    return normalize_tickers(tickers) or DEFAULT_TICKERS.copy()


def save_watchlist(tickers: list[str] | str, path: Path = WATCHLIST_PATH) -> list[str]:
    normalized = normalize_tickers(tickers)
    path.parent.mkdir(parents=True, exist_ok=True)
    save_watchlist_entries([{"ticker": ticker, "status": "active"} for ticker in normalized], path)
    return normalized


def _parse_watchlist_yaml(text: str) -> list[str]:
    """Parse the simple watchlist YAML shape used by this MVP.

    This avoids adding a YAML dependency for one short config file. If the
    config grows beyond a ticker list, swap this for PyYAML.
    """

    tickers: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or line == "tickers:":
            continue
        if line.startswith("tickers:"):
            inline = line.removeprefix("tickers:").strip()
            if inline:
                tickers.extend(inline.strip("[]").replace('"', "").replace("'", "").split(","))
            continue
        if line.startswith("-"):
            tickers.append(line[1:].strip().strip('"').strip("'"))
    return tickers


def _dump_watchlist_yaml(tickers: list[str]) -> str:
    lines = ["tickers:"]
    lines.extend(f"  - {ticker}" for ticker in tickers)
    return "\n".join(lines) + "\n"
