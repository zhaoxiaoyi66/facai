from __future__ import annotations

from pathlib import Path


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

    tickers = _parse_watchlist_yaml(path.read_text(encoding="utf-8"))
    return normalize_tickers(tickers) or DEFAULT_TICKERS.copy()


def save_watchlist(tickers: list[str] | str, path: Path = WATCHLIST_PATH) -> list[str]:
    normalized = normalize_tickers(tickers)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_dump_watchlist_yaml(normalized), encoding="utf-8")
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
