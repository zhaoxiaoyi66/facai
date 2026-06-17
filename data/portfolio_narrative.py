from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from data.prices import CACHE_PATH


NARRATIVE_ID = "default"

DEFAULT_PORTFOLIO_NARRATIVE = {
    "main_thesis": "AI 上半场看 GPU / 算力基础设施，下半场看企业 AI Agent 与价值应用落地变现。",
    "first_half_title": "上半场：GPU / 算力基础设施",
    "first_half_body": "先赚资本开支的钱，关注 GPU、服务器、网络、光模块、电力等卖铲环节。",
    "second_half_title": "下半场：企业 AI Agent / 应用变现",
    "second_half_body": "再赚价值落地的钱，关注企业软件、AI Agent、工作流自动化和真实商业变现。",
    "portfolio_mapping": "NVDA = 上半场核心主线；NOW = 下半场核心主线。",
}


class PortfolioNarrativeStore:
    def __init__(self, path: Path = CACHE_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS portfolio_narrative (
                    id TEXT PRIMARY KEY,
                    main_thesis TEXT NOT NULL,
                    first_half_title TEXT NOT NULL,
                    first_half_body TEXT NOT NULL,
                    second_half_title TEXT NOT NULL,
                    second_half_body TEXT NOT NULL,
                    portfolio_mapping TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._ensure_columns(conn)

    def _ensure_columns(self, conn: sqlite3.Connection) -> None:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(portfolio_narrative)").fetchall()}
        additions = {
            "main_thesis": "TEXT",
            "first_half_title": "TEXT",
            "first_half_body": "TEXT",
            "second_half_title": "TEXT",
            "second_half_body": "TEXT",
            "portfolio_mapping": "TEXT",
            "updated_at": "TEXT",
        }
        for column, definition in additions.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE portfolio_narrative ADD COLUMN {column} {definition}")

    def get_narrative(self) -> dict[str, str]:
        with self.connect() as conn:
            cursor = conn.execute("SELECT * FROM portfolio_narrative WHERE id = ?", (NARRATIVE_ID,))
            row = cursor.fetchone()
            columns = [description[0] for description in cursor.description] if cursor.description else []
        if not row:
            return {**DEFAULT_PORTFOLIO_NARRATIVE, "updated_at": ""}
        values = dict(zip(columns, row))
        narrative = {key: _clean_text(values.get(key)) or default for key, default in DEFAULT_PORTFOLIO_NARRATIVE.items()}
        narrative["updated_at"] = _clean_text(values.get("updated_at"))
        return narrative

    def save_narrative(self, values: dict[str, object]) -> dict[str, str]:
        cleaned = {
            key: _clean_text(values.get(key)) or default
            for key, default in DEFAULT_PORTFOLIO_NARRATIVE.items()
        }
        now = _now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO portfolio_narrative (
                    id,
                    main_thesis,
                    first_half_title,
                    first_half_body,
                    second_half_title,
                    second_half_body,
                    portfolio_mapping,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    main_thesis = excluded.main_thesis,
                    first_half_title = excluded.first_half_title,
                    first_half_body = excluded.first_half_body,
                    second_half_title = excluded.second_half_title,
                    second_half_body = excluded.second_half_body,
                    portfolio_mapping = excluded.portfolio_mapping,
                    updated_at = excluded.updated_at
                """,
                (
                    NARRATIVE_ID,
                    cleaned["main_thesis"],
                    cleaned["first_half_title"],
                    cleaned["first_half_body"],
                    cleaned["second_half_title"],
                    cleaned["second_half_body"],
                    cleaned["portfolio_mapping"],
                    now,
                ),
            )
        return self.get_narrative()

    def reset_default(self) -> dict[str, str]:
        return self.save_narrative(DEFAULT_PORTFOLIO_NARRATIVE)


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
