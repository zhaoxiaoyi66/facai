from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from data.prices import CACHE_PATH
from data.review_queue_builder import ReviewQueueStore


AI_DECISIONS = {
    "recommend_approve",
    "recommend_reject",
    "recommend_correct",
    "needs_human_review",
    "needs_more_source",
    "not_enough_evidence",
}
EVIDENCE_MATCHES = {"exact_match", "partial_match", "mismatch", "no_evidence"}
PERIOD_MATCHES = {"exact", "ambiguous", "mismatch"}
UNIT_MATCHES = {"exact", "ambiguous", "mismatch"}
RISK_LEVELS = {"low", "medium", "high"}
PROMPT_VERSION = "ai-review-v1"
MAX_AI_REVIEW_ITEMS = 50
DEFAULT_QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
TERMINAL_REVIEW_STATUSES = {"approved", "rejected", "manually_corrected"}
AI_REVIEWABLE_ITEM_TYPES = {
    "extracted_value",
    "derived_low_confidence",
    "qualitative_risk",
    "missing_kpi",
    "manual_override_needed",
    "analyst_estimate_needed",
}
AI_EXCLUDED_SOURCE_TYPES = {"CALCULATED", "FMP"}
AI_TRIAGE_STATUSES = {
    "auto_approved_by_ai",
    "ai_recommend_approve",
    "ai_recommend_correct",
    "ai_recommend_reject",
    "ai_needs_human_review",
    "ai_not_enough_evidence",
    "ai_invalid_output",
    "ai_skipped",
}


AI_REVIEW_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "aiDecision": {"type": "string", "enum": sorted(AI_DECISIONS)},
        "correctedValue": {"anyOf": [{"type": "number"}, {"type": "null"}]},
        "correctedUnit": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "correctedPeriod": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "confidenceScore": {"type": "number", "minimum": 0, "maximum": 1},
        "evidenceMatch": {"type": "string", "enum": sorted(EVIDENCE_MATCHES)},
        "periodMatch": {"type": "string", "enum": sorted(PERIOD_MATCHES)},
        "unitMatch": {"type": "string", "enum": sorted(UNIT_MATCHES)},
        "riskLevel": {"type": "string", "enum": sorted(RISK_LEVELS)},
        "hallucinationRisk": {"type": "boolean"},
        "explanationZh": {"type": "string"},
        "evidenceQuote": {"type": "string"},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "aiDecision",
        "correctedValue",
        "correctedUnit",
        "correctedPeriod",
        "confidenceScore",
        "evidenceMatch",
        "periodMatch",
        "unitMatch",
        "riskLevel",
        "hallucinationRisk",
        "explanationZh",
        "evidenceQuote",
        "warnings",
    ],
}


SYSTEM_PROMPT = """你是一个股票数据复核助手，只负责核对输入证据是否支持某个复核项。
你不是投资顾问，不能给买卖建议，不能上网，不能使用输入之外的信息。
You must not use your own world knowledge. Only evaluate the provided evidence text.
所有 provider，包括 Qwen / MiMo / DeepSeek / OpenAI，都只能根据 review item 中的 extractedText、sourceDocumentTitle、period、metricKey、extractedValue 判断。
不允许回答“最新数据是多少”，不允许补充外部事实，不允许根据模型自身知识修正数据。
你必须只输出符合 JSON schema 的对象。

判断规则：
1. 如果 extractedText 不能证明 extractedValue，aiDecision 必须是 not_enough_evidence。
2. 只有数值、单位、期间都能被原文明确证明时，才可 recommend_approve。
3. 如果原文数值和 extractedValue 不一致，使用 recommend_reject 或 recommend_correct。
4. 定性风险项、低置信度推导项、高影响风险项不能 recommend_approve，只能 needs_human_review 或 needs_more_source。
5. 如果期间不清楚，periodMatch = ambiguous。
6. 如果单位不清楚，unitMatch = ambiguous。
7. evidenceQuote 必须是输入 extractedText 中能支持判断的短摘录；没有证据则为空字符串。
8. 如果你的解释、修正值或证据引用包含 extractedText 没有的信息，hallucinationRisk 必须为 true，aiDecision 必须为 needs_human_review。
"""


@dataclass(frozen=True)
class AIReviewRunResult:
    reviewed: int
    skipped: int
    auto_approved: int
    needs_human: int
    not_configured: bool = False
    errors: list[str] | None = None


class AIReviewStore:
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
                CREATE TABLE IF NOT EXISTS ai_review_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reviewItemId INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    metricKey TEXT NOT NULL,
                    aiDecision TEXT NOT NULL,
                    correctedValue REAL,
                    correctedUnit TEXT,
                    correctedPeriod TEXT,
                    confidenceScore REAL NOT NULL,
                    evidenceMatch TEXT NOT NULL,
                    periodMatch TEXT NOT NULL,
                    unitMatch TEXT NOT NULL,
                    riskLevel TEXT NOT NULL,
                    hallucinationRisk INTEGER NOT NULL DEFAULT 0,
                    explanationZh TEXT,
                    evidenceQuote TEXT,
                    warnings TEXT,
                    createdAt TEXT NOT NULL,
                    model TEXT,
                    promptVersion TEXT,
                    inputHash TEXT,
                    appliedAction TEXT,
                    aiTriageStatus TEXT,
                    approvedAt TEXT,
                    approvedBy TEXT,
                    correctionCandidate TEXT
                )
                """
            )
            _ensure_column(conn, "ai_review_results", "hallucinationRisk", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(conn, "ai_review_results", "aiTriageStatus", "TEXT")
            _ensure_column(conn, "ai_review_results", "approvedAt", "TEXT")
            _ensure_column(conn, "ai_review_results", "approvedBy", "TEXT")
            _ensure_column(conn, "ai_review_results", "correctionCandidate", "TEXT")
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_ai_review_results_item_hash
                ON ai_review_results(reviewItemId, inputHash)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_review_audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reviewItemId INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    oldReviewStatus TEXT,
                    newReviewStatus TEXT,
                    oldValue REAL,
                    newValue REAL,
                    actor TEXT,
                    aiReviewResultId INTEGER,
                    reason TEXT,
                    createdAt TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_review_batches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batchId TEXT,
                    reviewItemIds TEXT NOT NULL,
                    status TEXT NOT NULL,
                    createdAt TEXT NOT NULL,
                    updatedAt TEXT NOT NULL,
                    model TEXT,
                    errorMessage TEXT
                )
                """
            )

    def save_result(self, review_item: dict, result: dict, model: str, input_hash: str, applied_action: str) -> int:
        validated = validate_ai_review_result(result)
        triage_status = _triage_status_from_action(applied_action)
        correction_candidate = _correction_candidate_payload(validated) if triage_status == "ai_recommend_correct" else None
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO ai_review_results (
                    reviewItemId, symbol, metricKey, aiDecision, correctedValue, correctedUnit,
                    correctedPeriod, confidenceScore, evidenceMatch, periodMatch, unitMatch,
                    riskLevel, hallucinationRisk, explanationZh, evidenceQuote, warnings, createdAt, model,
                    promptVersion, inputHash, appliedAction, aiTriageStatus, approvedAt, approvedBy, correctionCandidate
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(review_item["id"]),
                    str(review_item.get("symbol") or "").upper(),
                    str(review_item.get("metricKey") or ""),
                    validated["aiDecision"],
                    validated["correctedValue"],
                    validated["correctedUnit"],
                    validated["correctedPeriod"],
                    validated["confidenceScore"],
                    validated["evidenceMatch"],
                    validated["periodMatch"],
                    validated["unitMatch"],
                    validated["riskLevel"],
                    int(bool(validated["hallucinationRisk"])),
                    validated["explanationZh"],
                    validated["evidenceQuote"],
                    json.dumps(validated["warnings"], ensure_ascii=False),
                    _now(),
                    model,
                    PROMPT_VERSION,
                    input_hash,
                    applied_action,
                    triage_status,
                    _now() if triage_status == "auto_approved_by_ai" else None,
                    "ai" if triage_status == "auto_approved_by_ai" else None,
                    correction_candidate,
                ),
            )
            return int(cursor.lastrowid)

    def latest_for_item(self, review_item_id: int) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM ai_review_results
                WHERE reviewItemId = ?
                ORDER BY createdAt DESC, id DESC
                LIMIT 1
                """,
                (int(review_item_id),),
            ).fetchone()
        return _decorate_result(dict(row)) if row else None

    def latest_for_items(self, item_ids: Iterable[int]) -> dict[int, dict]:
        ids = [int(item_id) for item_id in item_ids]
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT r.*
                FROM ai_review_results r
                JOIN (
                    SELECT reviewItemId, MAX(id) AS maxId
                    FROM ai_review_results
                    WHERE reviewItemId IN ({placeholders})
                    GROUP BY reviewItemId
                ) latest ON latest.maxId = r.id
                """,
                ids,
            ).fetchall()
        return {int(row["reviewItemId"]): _decorate_result(dict(row)) for row in rows}

    def has_same_input_result(self, review_item_id: int, input_hash: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id FROM ai_review_results
                WHERE reviewItemId = ? AND inputHash = ?
                LIMIT 1
                """,
                (int(review_item_id), input_hash),
            ).fetchone()
        return row is not None

    def summary(self) -> dict:
        rows = self.list_results()
        summary = {
            "total": len(rows),
            "recommend_approve": 0,
            "recommend_reject": 0,
            "recommend_correct": 0,
            "needs_human_review": 0,
            "needs_more_source": 0,
            "not_enough_evidence": 0,
            "hallucination_risk": 0,
            "auto_approved_by_ai": 0,
            "manually_correct_candidate": 0,
            "ai_recommend_approve": 0,
            "ai_recommend_correct": 0,
            "ai_recommend_reject": 0,
            "ai_needs_human_review": 0,
            "ai_not_enough_evidence": 0,
            "ai_invalid_output": 0,
            "ai_skipped": 0,
        }
        for row in rows:
            decision = str(row.get("aiDecision") or "")
            action = str(row.get("appliedAction") or "")
            triage_status = str(row.get("aiTriageStatus") or "")
            if decision in summary:
                summary[decision] += 1
            if row.get("hallucinationRisk"):
                summary["hallucination_risk"] += 1
            if action in summary:
                summary[action] += 1
            if triage_status in summary:
                summary[triage_status] += 1
        return summary

    def list_results(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM ai_review_results
                ORDER BY createdAt DESC, id DESC
                """
            ).fetchall()
        return [_decorate_result(dict(row)) for row in rows]

    def create_batch_record(self, review_item_ids: list[int], model: str, batch_id: str | None = None, status: str = "created") -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO ai_review_batches (batchId, reviewItemIds, status, createdAt, updatedAt, model)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (batch_id, json.dumps(review_item_ids), status, _now(), _now(), model),
            )
            return int(cursor.lastrowid)

    def update_batch_record(self, record_id: int, status: str, batch_id: str | None = None, error_message: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE ai_review_batches
                SET status = ?, batchId = COALESCE(?, batchId), errorMessage = ?, updatedAt = ?
                WHERE id = ?
                """,
                (status, batch_id, error_message, _now(), int(record_id)),
            )

    def get_batch_record(self, record_id: int) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM ai_review_batches WHERE id = ?", (int(record_id),)).fetchone()
        if not row:
            return None
        result = dict(row)
        try:
            result["reviewItemIds"] = json.loads(result.get("reviewItemIds") or "[]")
        except json.JSONDecodeError:
            result["reviewItemIds"] = []
        return result

    def log_audit(
        self,
        review_item_id: int,
        action: str,
        old_review_status: str | None,
        new_review_status: str | None,
        old_value: float | None,
        new_value: float | None,
        actor: str,
        ai_review_result_id: int | None = None,
        reason: str | None = None,
    ) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO ai_review_audit_logs (
                    reviewItemId, action, oldReviewStatus, newReviewStatus,
                    oldValue, newValue, actor, aiReviewResultId, reason, createdAt
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(review_item_id),
                    action,
                    old_review_status,
                    new_review_status,
                    old_value,
                    new_value,
                    actor,
                    ai_review_result_id,
                    reason,
                    _now(),
                ),
            )
            return int(cursor.lastrowid)

    def list_audit_logs(self, review_item_id: int | None = None) -> list[dict]:
        clauses = []
        params: list[object] = []
        if review_item_id is not None:
            clauses.append("reviewItemId = ?")
            params.append(int(review_item_id))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM ai_review_audit_logs
                {where}
                ORDER BY createdAt DESC, id DESC
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]


class OpenAIReviewClient:
    def __init__(self, api_key: str | None = None, model: str | None = None, batch_model: str | None = None) -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.model = model or os.getenv("OPENAI_REVIEW_MODEL") or "gpt-4o-mini"
        self.batch_model = batch_model or os.getenv("OPENAI_REVIEW_BATCH_MODEL") or self.model

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def review_item(self, payload: dict) -> dict:
        if not self.configured:
            raise RuntimeError("OpenAI 复核接口密钥未配置")
        body = self._response_payload(payload, self.model)
        request = Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=45) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAI 复核请求失败：{exc.code} {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"OpenAI 复核请求失败：{exc.reason}") from exc
        return validate_ai_review_result(json.loads(_extract_response_text(raw)))

    def response_body_for_batch(self, payload: dict) -> dict:
        return self._response_payload(payload, self.batch_model)

    def _response_payload(self, payload: dict, model: str) -> dict:
        return {
            "model": model,
            "input": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False, sort_keys=True)},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "ai_review_result",
                    "description": "AI pre-review result for one manual review queue item.",
                    "schema": AI_REVIEW_JSON_SCHEMA,
                    "strict": True,
                }
            },
            "temperature": 0,
        }


class QwenReviewClient:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        batch_model: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY") or os.getenv("QWEN_API_KEY")
        self.model = model or os.getenv("QWEN_REVIEW_MODEL") or os.getenv("OPENAI_REVIEW_MODEL") or "qwen-plus"
        self.batch_model = batch_model or os.getenv("QWEN_REVIEW_BATCH_MODEL") or os.getenv("OPENAI_REVIEW_BATCH_MODEL") or self.model
        self.base_url = (base_url or os.getenv("QWEN_BASE_URL") or os.getenv("DASHSCOPE_BASE_URL") or DEFAULT_QWEN_BASE_URL).rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def review_item(self, payload: dict) -> dict:
        if not self.configured:
            raise RuntimeError("Qwen 复核接口密钥未配置")
        body = self._chat_payload(payload, self.model)
        request = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=45) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Qwen 复核请求失败：{exc.code} {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"Qwen 复核请求失败：{exc.reason}") from exc
        return validate_ai_review_result(json.loads(_extract_chat_completion_text(raw)))

    def response_body_for_batch(self, payload: dict) -> dict:
        return self._chat_payload(payload, self.batch_model)

    def _chat_payload(self, payload: dict, model: str) -> dict:
        return {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False, sort_keys=True)},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "ai_review_result",
                    "description": "AI pre-review result for one manual review queue item.",
                    "schema": AI_REVIEW_JSON_SCHEMA,
                    "strict": True,
                },
            },
            "temperature": 0,
        }


def create_review_client(provider: str | None = None):
    normalized = (provider or os.getenv("AI_REVIEW_PROVIDER") or "openai").strip().lower()
    if normalized in {"qwen", "dashscope", "aliyun", "alibaba"}:
        return QwenReviewClient()
    return OpenAIReviewClient()


class AIReviewAssistant:
    def __init__(
        self,
        queue_store: ReviewQueueStore | None = None,
        ai_store: AIReviewStore | None = None,
        client: object | None = None,
    ) -> None:
        self.queue_store = queue_store or ReviewQueueStore()
        self.ai_store = ai_store or AIReviewStore(self.queue_store.path)
        self.client = client or create_review_client()

    @property
    def configured(self) -> bool:
        return bool(getattr(self.client, "configured", False))

    def review_rows(self, rows: list[dict], limit: int = MAX_AI_REVIEW_ITEMS, high_impact_only: bool = False) -> AIReviewRunResult:
        candidates = ai_review_candidates(rows, high_impact_only=high_impact_only)[: min(limit, MAX_AI_REVIEW_ITEMS)]
        if not self.configured:
            return AIReviewRunResult(0, len(candidates), 0, 0, not_configured=True, errors=[])

        reviewed = 0
        skipped = 0
        auto_approved = 0
        needs_human = 0
        errors: list[str] = []
        for row in candidates:
            input_payload = build_ai_review_input(row)
            input_hash = ai_review_input_hash(input_payload)
            if self.ai_store.has_same_input_result(int(row["id"]), input_hash):
                skipped += 1
                continue
            try:
                result = enforce_evidence_only_result(row, self.client.review_item(input_payload))
                applied_action = apply_ai_review_result(row, result, self.queue_store)
                self.ai_store.save_result(row, result, getattr(self.client, "model", "unknown"), input_hash, applied_action)
                reviewed += 1
                if applied_action == "auto_approved_by_ai":
                    auto_approved += 1
                if applied_action in {"needs_human_review", "suggested_reject", "manually_correct_candidate", "not_enough_evidence"}:
                    needs_human += 1
            except Exception as exc:  # pragma: no cover - defensive UI path
                errors.append(f"{row.get('symbol')} {row.get('metricKey')}: {exc}")
        return AIReviewRunResult(reviewed, skipped, auto_approved, needs_human, False, errors)

    def review_item_ids(self, review_item_ids: Iterable[int], limit: int = MAX_AI_REVIEW_ITEMS) -> AIReviewRunResult:
        ids = {int(item_id) for item_id in review_item_ids}
        rows = [row for row in self.queue_store.list_items() if int(row["id"]) in ids]
        return self.review_rows(rows, limit=limit)

    def create_ai_review_batch(self, review_item_ids: Iterable[int]) -> dict:
        ids = list(dict.fromkeys(int(item_id) for item_id in review_item_ids))[:MAX_AI_REVIEW_ITEMS]
        record_id = self.ai_store.create_batch_record(ids, getattr(self.client, "batch_model", getattr(self.client, "model", "unknown")), status="queued")
        # The local app stores a batch record and keeps actual API submission behind this
        # boundary, so the UI can schedule night runs without exposing API keys.
        return {"id": record_id, "status": "queued", "reviewItemIds": ids, "configured": self.configured}

    def check_ai_review_batch_status(self, batch_id: int) -> dict:
        record = self.ai_store.get_batch_record(batch_id)
        if not record:
            return {"id": batch_id, "status": "not_found"}
        return record

    def apply_ai_review_batch_results(self, batch_id: int) -> AIReviewRunResult:
        record = self.ai_store.get_batch_record(batch_id)
        if not record:
            return AIReviewRunResult(0, 0, 0, 0, errors=[f"AI 复核批次 {batch_id} 不存在"])
        result = self.review_item_ids(record.get("reviewItemIds") or [])
        self.ai_store.update_batch_record(batch_id, "completed" if not result.errors else "completed_with_errors")
        return result


def validate_ai_review_result(result: dict) -> dict:
    if not isinstance(result, dict):
        raise ValueError("AI 复核返回格式不正确：需要 JSON 对象")
    allowed = set(AI_REVIEW_JSON_SCHEMA["properties"].keys())
    extra = set(result) - allowed
    if extra:
        raise ValueError("AI 复核返回包含未支持字段")
    missing = set(AI_REVIEW_JSON_SCHEMA["required"]) - set(result)
    if missing:
        raise ValueError("AI 复核返回缺少必要字段")
    normalized = dict(result)
    if normalized["aiDecision"] not in AI_DECISIONS:
        raise ValueError("AI 复核返回的判断类型无效")
    if normalized["evidenceMatch"] not in EVIDENCE_MATCHES:
        raise ValueError("AI 复核返回的证据匹配状态无效")
    if normalized["periodMatch"] not in PERIOD_MATCHES:
        raise ValueError("AI 复核返回的期间匹配状态无效")
    if normalized["unitMatch"] not in UNIT_MATCHES:
        raise ValueError("AI 复核返回的单位匹配状态无效")
    if normalized["riskLevel"] not in RISK_LEVELS:
        raise ValueError("AI 复核返回的风险等级无效")
    normalized["hallucinationRisk"] = bool(normalized.get("hallucinationRisk"))
    score = float(normalized["confidenceScore"])
    if score < 0 or score > 1:
        raise ValueError("AI 复核置信度必须在 0 到 1 之间")
    normalized["confidenceScore"] = score
    normalized["warnings"] = [str(item) for item in normalized.get("warnings") or []]
    for nullable in ("correctedValue", "correctedUnit", "correctedPeriod"):
        if normalized.get(nullable) == "":
            normalized[nullable] = None
    return normalized


def enforce_evidence_only_result(row: dict, result: dict) -> dict:
    normalized = validate_ai_review_result(result)
    extracted_text = str(row.get("extractedText") or row.get("explanation") or "")
    evidence_quote = str(normalized.get("evidenceQuote") or "").strip()
    has_quote = bool(evidence_quote)
    quote_in_text = has_quote and _normalize_text(evidence_quote) in _normalize_text(extracted_text)
    needs_evidence = normalized["aiDecision"] in {"recommend_approve", "recommend_correct"} or normalized["evidenceMatch"] in {
        "exact_match",
        "partial_match",
    }
    if normalized.get("hallucinationRisk"):
        normalized["aiDecision"] = "needs_human_review"
        warnings = list(normalized.get("warnings") or [])
        if "hallucination_risk" not in warnings:
            warnings.append("hallucination_risk")
        normalized["warnings"] = warnings
    if needs_evidence and not quote_in_text:
        normalized["hallucinationRisk"] = True
        normalized["aiDecision"] = "needs_human_review"
        normalized["evidenceMatch"] = "no_evidence" if not has_quote else "partial_match"
        normalized["riskLevel"] = "high"
        warnings = list(normalized.get("warnings") or [])
        if "hallucination_risk" not in warnings:
            warnings.append("hallucination_risk")
        normalized["warnings"] = warnings
        if not has_quote:
            normalized["evidenceQuote"] = ""
    return normalized


def build_ai_review_input(row: dict) -> dict:
    return {
        "symbol": row.get("symbol"),
        "companyName": row.get("companyName"),
        "modelType": row.get("modelType"),
        "metricKey": row.get("metricKey"),
        "displayName": row.get("displayName") or row.get("metricKey"),
        "extractedValue": row.get("value"),
        "unit": row.get("unit"),
        "period": row.get("period"),
        "fiscalQuarter": row.get("fiscalQuarter"),
        "fiscalYear": row.get("fiscalYear"),
        "sourceType": row.get("sourceType"),
        "sourceDocumentTitle": row.get("sourceDocumentTitle"),
        "extractedText": row.get("extractedText") or row.get("explanation") or "",
        "resolutionStatus": row.get("resolutionStatus"),
        "confidence": row.get("confidence"),
        "affects": sorted(_affects(row.get("affects"))),
        "itemType": row.get("itemType"),
        "currentQualityRating": row.get("currentQualityRating"),
        "currentEntryRating": row.get("currentEntryRating"),
        "currentRiskRating": row.get("currentRiskRating"),
    }


def ai_review_input_hash(payload: dict) -> str:
    stable = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


def ai_review_candidates(rows: list[dict], high_impact_only: bool = False) -> list[dict]:
    candidates = []
    for row in rows:
        if not _is_ai_review_candidate(row):
            continue
        if high_impact_only and not (_affects(row.get("affects")) & {"Quality", "Entry", "Risk"}):
            continue
        candidates.append(row)
    return candidates[:MAX_AI_REVIEW_ITEMS]


def _is_ai_review_candidate(row: dict) -> bool:
    if str(row.get("reviewStatus") or "") in TERMINAL_REVIEW_STATUSES:
        return False
    if str(row.get("sourceType") or "") in AI_EXCLUDED_SOURCE_TYPES:
        return False
    if str(row.get("resolutionStatus") or "") in {"calculated", "not_applicable"}:
        return False
    if str(row.get("itemType") or "") not in AI_REVIEWABLE_ITEM_TYPES:
        return False
    return True


def apply_ai_review_result(row: dict, result: dict, queue_store: ReviewQueueStore) -> str:
    validated = validate_ai_review_result(result)
    item_id = int(row["id"])
    item_type = str(row.get("itemType") or "")
    affects = _affects(row.get("affects"))
    if validated.get("hallucinationRisk"):
        return "needs_human_review"
    if _can_auto_approve(row, validated):
        queue_store.update_review_status(item_id, "approved", notes="auto_approved_by_ai")
        return "auto_approved_by_ai"
    if item_type in {"qualitative_risk", "derived_low_confidence"}:
        return "needs_human_review"
    if "Risk" in affects and validated["riskLevel"] == "high":
        return "needs_human_review"
    if validated["aiDecision"] == "recommend_reject" or validated["evidenceMatch"] == "mismatch":
        return "suggested_reject"
    if validated["aiDecision"] == "recommend_correct" and validated.get("correctedValue") is not None:
        return "manually_correct_candidate"
    if validated["aiDecision"] in {"needs_human_review", "needs_more_source"}:
        return validated["aiDecision"]
    if validated["aiDecision"] == "not_enough_evidence":
        return "not_enough_evidence"
    return "suggested_approve"


def _can_auto_approve(row: dict, result: dict) -> bool:
    return (
        str(row.get("itemType") or "") == "extracted_value"
        and not result.get("hallucinationRisk")
        and result["aiDecision"] == "recommend_approve"
        and result["confidenceScore"] >= 0.90
        and result["evidenceMatch"] == "exact_match"
        and result["periodMatch"] == "exact"
        and result["unitMatch"] == "exact"
        and result["riskLevel"] == "low"
        and "Risk" not in _affects(row.get("affects"))
        and "Action" not in _affects(row.get("affects"))
    )


def create_ai_review_batch(review_item_ids: Iterable[int]) -> dict:
    return AIReviewAssistant().create_ai_review_batch(review_item_ids)


def check_ai_review_batch_status(batch_id: int) -> dict:
    return AIReviewAssistant().check_ai_review_batch_status(batch_id)


def apply_ai_review_batch_results(batch_id: int) -> AIReviewRunResult:
    return AIReviewAssistant().apply_ai_review_batch_results(batch_id)


def _extract_response_text(raw: dict) -> str:
    if raw.get("output_text"):
        return str(raw["output_text"])
    for item in raw.get("output") or []:
        for content in item.get("content") or []:
            if "text" in content:
                return str(content["text"])
    raise ValueError("OpenAI response did not include output text")


def _extract_chat_completion_text(raw: dict) -> str:
    choices = raw.get("choices") or []
    if not choices:
        raise ValueError("Qwen response did not include choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("text"):
                return str(item["text"])
    raise ValueError("Qwen response did not include message content")


def _affects(value: object) -> set[str]:
    if isinstance(value, (list, tuple, set)):
        return {str(item) for item in value if item}
    return {part.strip() for part in str(value or "").split(",") if part.strip()}


def _decorate_result(row: dict) -> dict:
    try:
        row["warnings"] = json.loads(row.get("warnings") or "[]")
    except json.JSONDecodeError:
        row["warnings"] = []
    row["hallucinationRisk"] = bool(row.get("hallucinationRisk"))
    if not row.get("aiTriageStatus"):
        row["aiTriageStatus"] = _triage_status_from_action(str(row.get("appliedAction") or ""))
    try:
        row["correctionCandidate"] = json.loads(row.get("correctionCandidate") or "null")
    except json.JSONDecodeError:
        row["correctionCandidate"] = None
    return row


def _triage_status_from_action(action: str) -> str:
    normalized = str(action or "").strip()
    if normalized in AI_TRIAGE_STATUSES:
        return normalized
    return {
        "suggested_approve": "ai_recommend_approve",
        "suggested_reject": "ai_recommend_reject",
        "manually_correct_candidate": "ai_recommend_correct",
        "needs_human_review": "ai_needs_human_review",
        "needs_more_source": "ai_needs_human_review",
        "not_enough_evidence": "ai_not_enough_evidence",
        "invalid_output": "ai_invalid_output",
    }.get(normalized, "ai_skipped")


def _correction_candidate_payload(result: dict) -> str:
    return json.dumps(
        {
            "correctedValue": result.get("correctedValue"),
            "correctedUnit": result.get("correctedUnit"),
            "correctedPeriod": result.get("correctedPeriod"),
            "correctionReason": result.get("explanationZh"),
            "evidenceQuote": result.get("evidenceQuote"),
        },
        ensure_ascii=False,
    )


def _normalize_text(value: str) -> str:
    return " ".join(str(value or "").lower().split())


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
