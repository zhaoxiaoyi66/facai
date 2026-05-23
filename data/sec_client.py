from __future__ import annotations

import json
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from typing import Iterator
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from data.prices import CACHE_PATH
from data.sec_supplement import SEC_COMPANYFACTS_URL, SEC_TICKERS_URL, SEC_USER_AGENT


SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_no_dash}/"
SEC_MAX_REQUESTS_PER_SECOND = 5
WHITELISTED_DOCUMENT_DOMAINS = (
    "sec.gov",
    "servicenow.com",
    "investors.servicenow.com",
    "q4cdn.com",
)


@dataclass(frozen=True)
class SECFiling:
    form: str
    accession_number: str
    filing_date: str
    report_date: str
    primary_document: str
    document_url: str
    index_url: str


class SECClient:
    def __init__(self, path: Path = CACHE_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._last_request_at = 0.0
        self.user_agent = SEC_USER_AGENT
        self.max_requests_per_second = SEC_MAX_REQUESTS_PER_SECOND
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
                CREATE TABLE IF NOT EXISTS sec_response_cache (
                    cache_key TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    fetched_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sec_text_cache (
                    cache_key TEXT PRIMARY KEY,
                    payload_text TEXT NOT NULL,
                    fetched_at TEXT NOT NULL
                )
                """
            )

    def cik_for_ticker(self, ticker: str, force_refresh: bool = False) -> str | None:
        data = self.cached_json("sec_company_tickers", SEC_TICKERS_URL, ttl_hours=24 * 7, force_refresh=force_refresh)
        for row in data.values() if isinstance(data, dict) else []:
            if str(row.get("ticker", "")).upper() == ticker.upper():
                return f"{int(row['cik_str']):010d}"
        return None

    def companyfacts(self, cik: str, force_refresh: bool = False) -> dict:
        url = SEC_COMPANYFACTS_URL.format(cik=cik)
        return self.cached_json(f"sec_companyfacts_{cik}", url, ttl_hours=12, force_refresh=force_refresh)

    def get_company_facts(self, symbol: str, force_refresh: bool = False) -> dict:
        cik = self.cik_for_ticker(symbol, force_refresh=force_refresh)
        if not cik:
            return {}
        return self.companyfacts(cik, force_refresh=force_refresh)

    def submissions(self, cik: str, force_refresh: bool = False) -> dict:
        url = SEC_SUBMISSIONS_URL.format(cik=cik)
        return self.cached_json(f"sec_submissions_{cik}", url, ttl_hours=12, force_refresh=force_refresh)

    def get_company_submissions(self, symbol: str, force_refresh: bool = False) -> dict:
        cik = self.cik_for_ticker(symbol, force_refresh=force_refresh)
        if not cik:
            return {}
        return self.submissions(cik, force_refresh=force_refresh)

    def recent_filings(self, cik: str, forms: tuple[str, ...] = ("8-K", "10-Q", "10-K"), limit: int = 12, force_refresh: bool = False) -> list[SECFiling]:
        submissions = self.submissions(cik, force_refresh=force_refresh)
        recent = (submissions.get("filings") or {}).get("recent") or {}
        rows: list[SECFiling] = []
        cik_int = str(int(cik))
        for index, form in enumerate(recent.get("form") or []):
            if form not in forms:
                continue
            accession = str((recent.get("accessionNumber") or [])[index])
            primary_document = str((recent.get("primaryDocument") or [])[index])
            accession_no_dash = accession.replace("-", "")
            base_url = SEC_ARCHIVES_BASE.format(cik_int=cik_int, accession_no_dash=accession_no_dash)
            rows.append(
                SECFiling(
                    form=form,
                    accession_number=accession,
                    filing_date=str((recent.get("filingDate") or [""])[index]),
                    report_date=str((recent.get("reportDate") or [""])[index]),
                    primary_document=primary_document,
                    document_url=urljoin(base_url, primary_document),
                    index_url=urljoin(base_url, f"{accession}-index.html"),
                )
            )
            if len(rows) >= limit:
                break
        return rows

    def find_recent_filings(
        self,
        symbol: str,
        forms: tuple[str, ...] | list[str] = ("10-K", "10-Q", "8-K"),
        limit: int = 12,
        force_refresh: bool = False,
    ) -> list[SECFiling]:
        cik = self.cik_for_ticker(symbol, force_refresh=force_refresh)
        if not cik:
            return []
        return self.recent_filings(cik, forms=tuple(forms), limit=limit, force_refresh=force_refresh)

    def filing_exhibit_urls(self, filing: SECFiling, force_refresh: bool = False) -> list[tuple[str, str]]:
        urls = [(filing.document_url, f"{filing.form} primary document")]
        try:
            index_html = self.cached_text(
                f"sec_index_{filing.accession_number}",
                filing.index_url,
                ttl_hours=24 * 7,
                force_refresh=force_refresh,
                normalize_html=False,
            )
        except Exception:
            return urls

        for href, title in _links_from_html(index_html, base_url=filing.index_url):
            lowered = href.lower()
            if any(token in lowered for token in ("ex99", "ex-99", "exhibit99", "exhibit-99")):
                urls.append((href, title or "8-K Exhibit 99.1"))
        for href, title in _exhibit_links_from_index_rows(index_html, base_url=filing.index_url):
            urls.append((href, title))
        return _dedupe_urls(urls)

    def cached_json(self, cache_key: str, url: str, ttl_hours: float, force_refresh: bool = False):
        if not force_refresh:
            cached = self._get_cached_json(cache_key, ttl_hours)
            if cached is not None:
                return cached
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                payload = self._fetch_url(url, timeout_seconds=10)
                data = json.loads(payload)
                self._set_cached_json(cache_key, data)
                return data
            except Exception as exc:
                last_error = exc
                if attempt >= 2:
                    raise
                time.sleep(0.4 * (attempt + 1))
        raise RuntimeError(str(last_error))

    def cached_text(self, cache_key: str, url: str, ttl_hours: float, force_refresh: bool = False, normalize_html: bool = True) -> str:
        if not force_refresh:
            cached = self._get_cached_text(cache_key, ttl_hours)
            if cached is not None:
                return cached
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                payload = self._fetch_url(url, timeout_seconds=12)
                text = _html_to_text(payload) if normalize_html else payload
                self._set_cached_text(cache_key, text)
                return text
            except Exception as exc:
                last_error = exc
                if attempt >= 2:
                    raise
                time.sleep(0.4 * (attempt + 1))
        raise RuntimeError(str(last_error))

    def _fetch_url(self, url: str, timeout_seconds: int) -> str:
        _validate_whitelist(url)
        self._rate_limit()
        request = Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept-Encoding": "identity",
                "Accept": "application/json,text/html,text/plain,*/*",
            },
        )
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read()
        return raw.decode("utf-8", errors="replace")

    def _rate_limit(self) -> None:
        interval = 1 / SEC_MAX_REQUESTS_PER_SECOND
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < interval:
            time.sleep(interval - elapsed)
        self._last_request_at = time.monotonic()

    def _get_cached_json(self, cache_key: str, ttl_hours: float):
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload_json, fetched_at FROM sec_response_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        if not row:
            return None
        payload_json, fetched_at = row
        if _is_stale(fetched_at, ttl_hours):
            return None
        return json.loads(payload_json)

    def _set_cached_json(self, cache_key: str, payload) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sec_response_cache (cache_key, payload_json, fetched_at)
                VALUES (?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    fetched_at = excluded.fetched_at
                """,
                (cache_key, json.dumps(payload), _now()),
            )

    def _get_cached_text(self, cache_key: str, ttl_hours: float) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload_text, fetched_at FROM sec_text_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        if not row:
            return None
        payload_text, fetched_at = row
        if _is_stale(fetched_at, ttl_hours):
            return None
        return payload_text

    def _set_cached_text(self, cache_key: str, payload: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sec_text_cache (cache_key, payload_text, fetched_at)
                VALUES (?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    payload_text = excluded.payload_text,
                    fetched_at = excluded.fetched_at
                """,
                (cache_key, payload, _now()),
            )


def _links_from_html(html: str, base_url: str) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    pattern = re.compile(r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)
    for match in pattern.finditer(html):
        href = urljoin(base_url, unescape(match.group(1)))
        title = _html_to_text(match.group(2)).strip()
        try:
            _validate_whitelist(href)
        except ValueError:
            continue
        links.append((href, title))
    return links


def _exhibit_links_from_index_rows(html: str, base_url: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for row_match in re.finditer(r"(?is)<tr[^>]*>(.*?)</tr>", html):
        row_html = row_match.group(1)
        if not re.search(r"EX-?99", row_html, flags=re.IGNORECASE):
            continue
        link_match = re.search(r"href=[\"']([^\"']+)[\"']", row_html, flags=re.IGNORECASE)
        if not link_match:
            continue
        href = urljoin(base_url, unescape(link_match.group(1)))
        try:
            _validate_whitelist(href)
        except ValueError:
            continue
        rows.append((href, "8-K Exhibit 99.1"))
    return rows


def _dedupe_urls(rows: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[str] = set()
    deduped: list[tuple[str, str]] = []
    for url, title in rows:
        if url in seen:
            continue
        seen.add(url)
        deduped.append((url, title))
    return deduped


def _html_to_text(payload: str) -> str:
    text = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", payload)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", unescape(text)).strip()


def _validate_whitelist(url: str) -> None:
    lowered = url.lower()
    if not lowered.startswith(("https://", "http://")):
        raise ValueError("Only HTTP(S) disclosure sources are allowed")
    if not any(domain in lowered.split("/")[2] for domain in WHITELISTED_DOCUMENT_DOMAINS):
        raise ValueError(f"Domain not whitelisted for disclosure fetch: {url}")


def _is_stale(fetched_at: str, ttl_hours: float) -> bool:
    try:
        fetched = datetime.fromisoformat(fetched_at)
    except ValueError:
        return True
    return datetime.now(timezone.utc) - fetched > timedelta(hours=ttl_hours)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
