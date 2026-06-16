from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from data.prices import CACHE_PATH


class WatchlistStarStore:
    """User-owned watchlist star marks.

    Star marks are deliberately display-only. They are not used by scoring,
    buy-zone calculation, risk ranking, or trade advice.
    """

    def __init__(self, path: Path = CACHE_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS watchlist_star_marks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL UNIQUE,
                    is_starred INTEGER NOT NULL DEFAULT 0,
                    star_note TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def get_marks(self, symbols: list[str] | tuple[str, ...] | set[str] | None = None) -> dict[str, dict]:
        normalized = [_normalize_symbol(symbol) for symbol in (symbols or []) if _normalize_symbol(symbol)]
        with self.connect() as conn:
            if normalized:
                placeholders = ",".join("?" for _ in normalized)
                rows = conn.execute(
                    f"""
                    SELECT symbol, is_starred, star_note, created_at, updated_at
                    FROM watchlist_star_marks
                    WHERE symbol IN ({placeholders})
                    """,
                    normalized,
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT symbol, is_starred, star_note, created_at, updated_at
                    FROM watchlist_star_marks
                    """
                ).fetchall()
        return {str(row["symbol"]).upper(): _row_to_mark(row) for row in rows}

    def is_starred(self, symbol: object) -> bool:
        mark = self.get_mark(symbol)
        return bool(mark.get("is_starred"))

    def get_mark(self, symbol: object) -> dict:
        normalized = _normalize_symbol(symbol)
        if not normalized:
            return {"symbol": "", "is_starred": False, "star_note": ""}
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT symbol, is_starred, star_note, created_at, updated_at
                FROM watchlist_star_marks
                WHERE symbol = ?
                """,
                (normalized,),
            ).fetchone()
        if not row:
            return {"symbol": normalized, "is_starred": False, "star_note": ""}
        return _row_to_mark(row)

    def set_star(self, symbol: object, is_starred: bool, star_note: object = None) -> dict:
        normalized = _normalize_symbol(symbol)
        if not normalized:
            raise ValueError("股票代码不能为空")
        now = _now()
        note = None if star_note is None else str(star_note or "").strip()
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT created_at, star_note FROM watchlist_star_marks WHERE symbol = ?",
                (normalized,),
            ).fetchone()
            created_at = existing["created_at"] if existing and existing["created_at"] else now
            if note is None and existing:
                note = existing["star_note"]
            conn.execute(
                """
                INSERT INTO watchlist_star_marks (
                    symbol, is_starred, star_note, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    is_starred = excluded.is_starred,
                    star_note = excluded.star_note,
                    updated_at = excluded.updated_at
                """,
                (normalized, 1 if is_starred else 0, note or "", created_at, now),
            )
        return self.get_mark(normalized)

    def toggle_star(self, symbol: object) -> dict:
        current = self.get_mark(symbol)
        return self.set_star(current.get("symbol") or symbol, not bool(current.get("is_starred")))


def _normalize_symbol(symbol: object) -> str:
    return str(symbol or "").strip().upper()


def _row_to_mark(row: sqlite3.Row) -> dict:
    return {
        "symbol": str(row["symbol"] or "").upper(),
        "is_starred": bool(row["is_starred"]),
        "star_note": str(row["star_note"] or ""),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
