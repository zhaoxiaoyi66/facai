from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from data.prices import CACHE_PATH


CACHE_TTL_SECONDS = {
    "quote": 5 * 60,
    "profile": 7 * 24 * 60 * 60,
    "financials": 7 * 24 * 60 * 60,
    "ratios": 7 * 24 * 60 * 60,
    "keyMetrics": 7 * 24 * 60 * 60,
    "historicalPrice": 24 * 60 * 60,
    "news": 30 * 60,
    "analystEstimates": 24 * 60 * 60,
    "scores": 24 * 60 * 60,
}


ENDPOINT_TTL_BUCKETS = {
    "quote": "quote",
    "profile": "profile",
    "income-statement": "financials",
    "balance-sheet-statement": "financials",
    "cash-flow-statement": "financials",
    "income-statement-growth": "financials",
    "cash-flow-statement-growth": "financials",
    "ratios-ttm": "ratios",
    "key-metrics-ttm": "keyMetrics",
    "historical-price-eod/full": "historicalPrice",
    "analyst-estimates": "analystEstimates",
    "stock-news": "news",
    "news/stock": "news",
}


class FMPResponseCache:
    def __init__(self, path: Path = CACHE_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS fmp_api_responses (
                    cache_key TEXT PRIMARY KEY,
                    endpoint TEXT NOT NULL,
                    params_json TEXT NOT NULL,
                    ttl_bucket TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    fetched_at TEXT NOT NULL
                )
                """
            )

    def get(self, endpoint: str, params: dict) -> list | dict | None:
        bucket = ttl_bucket_for_endpoint(endpoint)
        ttl_seconds = CACHE_TTL_SECONDS[bucket]
        cache_key = build_cache_key(endpoint, params)
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT payload_json, fetched_at
                FROM fmp_api_responses
                WHERE cache_key = ?
                """,
                (cache_key,),
            ).fetchone()

        if not row:
            return None
        payload_json, fetched_at = row
        if not _is_fresh_seconds(fetched_at, ttl_seconds):
            return None
        return json.loads(payload_json)

    def set(self, endpoint: str, params: dict, payload: list | dict) -> None:
        bucket = ttl_bucket_for_endpoint(endpoint)
        params_json = canonical_params_json(params)
        cache_key = build_cache_key(endpoint, params)
        fetched_at = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO fmp_api_responses
                    (cache_key, endpoint, params_json, ttl_bucket, payload_json, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    endpoint = excluded.endpoint,
                    params_json = excluded.params_json,
                    ttl_bucket = excluded.ttl_bucket,
                    payload_json = excluded.payload_json,
                    fetched_at = excluded.fetched_at
                """,
                (
                    cache_key,
                    endpoint,
                    params_json,
                    bucket,
                    json.dumps(payload, default=str),
                    fetched_at,
                ),
            )


def ttl_bucket_for_endpoint(endpoint: str) -> str:
    return ENDPOINT_TTL_BUCKETS.get(endpoint, "quote")


def canonical_params_json(params: dict) -> str:
    return json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)


def build_cache_key(endpoint: str, params: dict) -> str:
    digest = hashlib.sha256(canonical_params_json(params).encode("utf-8")).hexdigest()
    return f"{endpoint}:{digest}"


def _is_fresh_seconds(fetched_at: str, ttl_seconds: int) -> bool:
    fetched_dt = datetime.fromisoformat(fetched_at)
    if fetched_dt.tzinfo is None:
        fetched_dt = fetched_dt.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - fetched_dt <= timedelta(seconds=ttl_seconds)
