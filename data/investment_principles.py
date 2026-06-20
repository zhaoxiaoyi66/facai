from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_PRINCIPLES_PATH = Path("config/investment_principles.local.json")
DEFAULT_NOTES_PATH = DEFAULT_PRINCIPLES_PATH

NOTE_TAG_OPTIONS = ["心态", "仓位", "买点", "卖点", "基本面", "AI主线", "风险", "其他"]

DEFAULT_QUOTE_ID = "bubble_trend_misread"
DEFAULT_QUOTE_TEXT = "泡沫由两部分组成：真实的趋势 + 对该趋势的误解。"
DEFAULT_QUOTE_NOTE = "先判断真实趋势，再判断市场是否误解了这个趋势。"
DEFAULT_QUOTE_TAGS = ["泡沫", "趋势", "认知"]

IRON_RULE_TAGS = ["不做空", "只买高信念", "最多6只核心", "现金也是仓位", "不参与感受型小仓"]

DEFAULT_CORE_RULES = [
    {
        "id": "high_conviction_only",
        "title": "不做空，只买高信念股",
        "body": "不做空；只做高信念、能承受波动的股票。买点看客观承接，仓位看纪律。现金也是仓位，等待也是操作。",
    },
    {
        "id": "position_limit",
        "title": "持仓原则",
        "body": "最多持有 6 只股票，结构为 1 只第一核心、2 只强核心、2 只卫星赔率仓、1 只战术仓；新增股票必须替换低信念持仓。",
    },
]

DEFAULT_PRINCIPLES = {
    "quotes": [
        {
            "id": DEFAULT_QUOTE_ID,
            "text": DEFAULT_QUOTE_TEXT,
            "note": DEFAULT_QUOTE_NOTE,
            "tags": DEFAULT_QUOTE_TAGS,
            "created_at": "",
            "updated_at": "",
        }
    ],
    "core_rules": DEFAULT_CORE_RULES,
    "selected_quote_id": DEFAULT_QUOTE_ID,
    "default_quote_deleted": False,
}


def load_investment_principles(path: Path = DEFAULT_PRINCIPLES_PATH) -> dict[str, Any]:
    payload = _read_payload(path)
    normalized = _normalize_payload(payload)
    if normalized != payload:
        _write_payload(path, normalized)
    return normalized


def add_principle_quote(
    text: str,
    *,
    note: str = "",
    tags: list[str] | str | None = None,
    path: Path = DEFAULT_PRINCIPLES_PATH,
) -> dict[str, Any]:
    payload = load_investment_principles(path)
    quote = _normalize_quote(
        {
            "id": _quote_id(text),
            "text": text,
            "note": note,
            "tags": _split_tags(tags),
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }
    )
    if not quote["text"]:
        raise ValueError("笔记正文不能为空")
    existing_ids = {str(item.get("id") or "") for item in payload["quotes"]}
    if quote["id"] in existing_ids:
        quote["id"] = f"{quote['id']}_{len(existing_ids) + 1}"
    payload["quotes"].append(quote)
    payload["selected_quote_id"] = quote["id"]
    _write_payload(path, payload)
    return quote


def update_principle_quote(
    quote_id: str,
    *,
    text: str,
    note: str = "",
    tags: list[str] | str | None = None,
    path: Path = DEFAULT_PRINCIPLES_PATH,
) -> dict[str, Any]:
    payload = load_investment_principles(path)
    normalized_id = str(quote_id or "").strip()
    if not str(text or "").strip():
        raise ValueError("笔记正文不能为空")
    for index, quote in enumerate(payload["quotes"]):
        if str(quote.get("id") or "") == normalized_id:
            updated = {
                **quote,
                "text": str(text).strip(),
                "note": str(note or "").strip(),
                "tags": _split_tags(tags),
                "updated_at": _now_iso(),
            }
            payload["quotes"][index] = _normalize_quote(updated)
            _write_payload(path, payload)
            return payload["quotes"][index]
    raise KeyError(f"未找到投资笔记：{normalized_id}")


def delete_principle_quote(
    quote_id: str,
    *,
    confirm_default_delete: bool = False,
    path: Path = DEFAULT_PRINCIPLES_PATH,
) -> dict[str, Any]:
    payload = load_investment_principles(path)
    normalized_id = str(quote_id or "").strip()
    quotes = payload["quotes"]
    if len(quotes) <= 1 and normalized_id == str(quotes[0].get("id") or "") and not confirm_default_delete:
        return {
            "deleted": False,
            "requires_confirmation": True,
            "message": "这是当前唯一默认原则，删除后顶部将没有默认笔记。确认删除？",
        }
    kept = [quote for quote in quotes if str(quote.get("id") or "") != normalized_id]
    deleted = len(kept) != len(quotes)
    payload["quotes"] = kept
    if deleted and normalized_id == DEFAULT_QUOTE_ID:
        payload["default_quote_deleted"] = True
    if payload.get("selected_quote_id") == normalized_id:
        payload["selected_quote_id"] = str(kept[0].get("id") or "") if kept else ""
    _write_payload(path, payload)
    return {"deleted": deleted, "requires_confirmation": False, "message": "已删除投资笔记。" if deleted else "未找到投资笔记。"}


def select_principle_quote(quote_id: str, *, path: Path = DEFAULT_PRINCIPLES_PATH) -> dict[str, Any] | None:
    payload = load_investment_principles(path)
    normalized_id = str(quote_id or "").strip()
    quote = _quote_by_id(payload, normalized_id)
    if quote:
        payload["selected_quote_id"] = normalized_id
        _write_payload(path, payload)
    return quote


def next_principle_quote(current_id: str | None = None, *, path: Path = DEFAULT_PRINCIPLES_PATH) -> dict[str, Any] | None:
    payload = load_investment_principles(path)
    quotes = payload["quotes"]
    if not quotes:
        return None
    if len(quotes) == 1:
        payload["selected_quote_id"] = str(quotes[0].get("id") or "")
        _write_payload(path, payload)
        return quotes[0]
    active_id = str(current_id or payload.get("selected_quote_id") or "").strip()
    ids = [str(item.get("id") or "") for item in quotes]
    try:
        next_index = (ids.index(active_id) + 1) % len(quotes)
    except ValueError:
        next_index = 0
    payload["selected_quote_id"] = ids[next_index]
    _write_payload(path, payload)
    return quotes[next_index]


def selected_principle_quote(payload: dict[str, Any]) -> dict[str, Any] | None:
    quote = _quote_by_id(payload, str(payload.get("selected_quote_id") or ""))
    if quote:
        return quote
    return payload["quotes"][0] if payload.get("quotes") else None


def principle_reminder_for_mistake_tags(tags: list[str] | tuple[str, ...] | set[str] | None) -> str:
    text = " ".join(str(tag or "") for tag in tags or [])
    if any(token in text for token in ("追高", "FOMO", "情绪买入", "情绪交易", "怕错过", "追涨杀跌")):
        return "对应纪律：先判断真实趋势，再判断自己是不是在追逐对趋势的误解。"
    if any(token in text for token in ("小仓乱买", "参与感买入", "感受型小仓")):
        return "对应纪律：不参与感受型小仓。买不进核心仓的票，不该用小仓安慰自己。"
    if any(token in text for token in ("卖飞", "过早卖出", "卖早")):
        return "对应纪律：卖出前先问逻辑是否改变，而不是只看短线波动。"
    if any(token in text for token in ("忘记持仓", "没有管理", "不管理")):
        return "对应纪律：现金也是仓位，持仓也是责任。买了就要进入管理，不管理就不该买。"
    return "对应纪律：泡沫由真实趋势和对趋势的误解组成。先判断事实，再处理情绪。"


def load_investment_notes(path: Path = DEFAULT_NOTES_PATH) -> dict[str, Any]:
    payload = _read_payload(path)
    normalized = _normalize_notes_payload(payload)
    if normalized != payload:
        _write_payload(path, normalized)
    return normalized


def add_investment_note(
    text: str,
    *,
    note: str = "",
    tags: list[str] | str | None = None,
    source: str = "",
    related_symbol: str = "",
    path: Path = DEFAULT_NOTES_PATH,
) -> dict[str, Any]:
    payload = load_investment_notes(path)
    normalized = _normalize_note(
        {
            "id": _quote_id(text),
            "text": text,
            "note": note,
            "tags": _split_tags(tags),
            "source": source,
            "related_symbol": related_symbol,
            "pinned": False,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }
    )
    if not normalized["text"]:
        raise ValueError("笔记正文不能为空")
    existing_ids = {str(item.get("id") or "") for item in payload["notes"]}
    if normalized["id"] in existing_ids:
        normalized["id"] = f"{normalized['id']}_{len(existing_ids) + 1}"
    payload["notes"].append(normalized)
    _write_payload(path, payload)
    return normalized


def update_investment_note(
    note_id: str,
    *,
    text: str,
    note: str = "",
    tags: list[str] | str | None = None,
    source: str = "",
    related_symbol: str = "",
    pinned: bool | None = None,
    path: Path = DEFAULT_NOTES_PATH,
) -> dict[str, Any]:
    payload = load_investment_notes(path)
    normalized_id = str(note_id or "").strip()
    if not str(text or "").strip():
        raise ValueError("笔记正文不能为空")
    for index, item in enumerate(payload["notes"]):
        if str(item.get("id") or "") == normalized_id:
            updated = {
                **item,
                "text": str(text or "").strip(),
                "note": str(note or "").strip(),
                "tags": _split_tags(tags),
                "source": str(source or "").strip(),
                "related_symbol": str(related_symbol or "").strip().upper(),
                "updated_at": _now_iso(),
            }
            if pinned is not None:
                updated["pinned"] = bool(pinned)
            payload["notes"][index] = _normalize_note(updated)
            _write_payload(path, payload)
            return payload["notes"][index]
    raise KeyError(f"未找到投资笔记：{normalized_id}")


def delete_investment_note(note_id: str, *, path: Path = DEFAULT_NOTES_PATH) -> bool:
    payload = load_investment_notes(path)
    normalized_id = str(note_id or "").strip()
    kept = [item for item in payload["notes"] if str(item.get("id") or "") != normalized_id]
    deleted = len(kept) != len(payload["notes"])
    payload["notes"] = kept
    _write_payload(path, payload)
    return deleted


def toggle_investment_note_pin(note_id: str, *, path: Path = DEFAULT_NOTES_PATH) -> dict[str, Any]:
    payload = load_investment_notes(path)
    normalized_id = str(note_id or "").strip()
    for index, item in enumerate(payload["notes"]):
        if str(item.get("id") or "") == normalized_id:
            updated = {**item, "pinned": not bool(item.get("pinned")), "updated_at": _now_iso()}
            payload["notes"][index] = _normalize_note(updated)
            _write_payload(path, payload)
            return payload["notes"][index]
    raise KeyError(f"未找到投资笔记：{normalized_id}")


def sorted_investment_notes(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [_normalize_note(item) for item in notes if isinstance(item, dict) and str(item.get("text") or "").strip()],
        key=lambda item: (bool(item.get("pinned")), str(item.get("created_at") or "")),
        reverse=True,
    )


def filter_investment_notes(
    notes: list[dict[str, Any]],
    *,
    search: str = "",
    tags: list[str] | tuple[str, ...] | set[str] | None = None,
    pinned_only: bool = False,
) -> list[dict[str, Any]]:
    query = str(search or "").strip().lower()
    wanted_tags = {str(tag) for tag in tags or [] if str(tag).strip()}
    result: list[dict[str, Any]] = []
    for item in sorted_investment_notes(notes):
        if pinned_only and not bool(item.get("pinned")):
            continue
        item_tags = {str(tag) for tag in item.get("tags") or []}
        if wanted_tags and not wanted_tags.intersection(item_tags):
            continue
        haystack = " ".join(str(item.get(field) or "") for field in ("text", "note", "source", "related_symbol")).lower()
        if query and query not in haystack:
            continue
        result.append(item)
    return result


def default_principles_payload() -> dict[str, Any]:
    payload = json.loads(json.dumps(DEFAULT_PRINCIPLES, ensure_ascii=False))
    now = _now_iso()
    for quote in payload["quotes"]:
        quote["created_at"] = now
        quote["updated_at"] = now
    return payload


def _read_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        payload = default_principles_payload()
        _write_payload(path, payload)
        return payload
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload or {})
    quotes = [_normalize_quote(item) for item in normalized.get("quotes") or [] if isinstance(item, dict)]
    default_deleted = bool(normalized.get("default_quote_deleted"))
    if not default_deleted and not any(str(item.get("id") or "") == DEFAULT_QUOTE_ID for item in quotes):
        quotes.insert(0, _normalize_quote(default_principles_payload()["quotes"][0]))
    core_rules = [
        _normalize_core_rule(item)
        for item in normalized.get("core_rules") or []
        if isinstance(item, dict) and (item.get("title") or item.get("body"))
    ]
    normalized["quotes"] = quotes
    normalized["default_quote_deleted"] = default_deleted
    normalized["core_rules"] = core_rules or [dict(item) for item in DEFAULT_CORE_RULES]
    selected = str(normalized.get("selected_quote_id") or "").strip()
    if not _quote_by_id(normalized, selected):
        normalized["selected_quote_id"] = str(quotes[0].get("id") or "") if quotes else ""
    return normalized


def _normalize_quote(item: dict[str, Any]) -> dict[str, Any]:
    text = str(item.get("text") or "").strip()
    now = _now_iso()
    return {
        "id": str(item.get("id") or _quote_id(text)).strip(),
        "text": text,
        "note": str(item.get("note") or "").strip(),
        "tags": _split_tags(item.get("tags")),
        "created_at": str(item.get("created_at") or now),
        "updated_at": str(item.get("updated_at") or item.get("created_at") or now),
    }


def _normalize_notes_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_payload(payload)
    raw_notes = normalized.get("notes")
    if isinstance(raw_notes, list):
        source_items = raw_notes
    else:
        source_items = normalized.get("quotes") or []
    notes = [
        _normalize_note(item)
        for item in source_items
        if isinstance(item, dict) and str(item.get("text") or "").strip()
    ]
    normalized["notes"] = sorted_investment_notes(_dedupe_notes(notes))
    return normalized


def _normalize_note(item: dict[str, Any]) -> dict[str, Any]:
    text = str(item.get("text") or "").strip()
    now = _now_iso()
    return {
        "id": str(item.get("id") or _quote_id(text)).strip(),
        "text": text,
        "note": str(item.get("note") or "").strip(),
        "tags": _split_tags(item.get("tags")),
        "source": str(item.get("source") or "").strip(),
        "related_symbol": str(item.get("related_symbol") or item.get("symbol") or "").strip().upper(),
        "pinned": bool(item.get("pinned")),
        "created_at": str(item.get("created_at") or now),
        "updated_at": str(item.get("updated_at") or item.get("created_at") or now),
    }


def _dedupe_notes(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in notes:
        note_id = str(item.get("id") or "").strip()
        if note_id and note_id not in seen:
            result.append(item)
            seen.add(note_id)
    return result


def _normalize_core_rule(item: dict[str, Any]) -> dict[str, str]:
    title = str(item.get("title") or "").strip()
    body = str(item.get("body") or item.get("content") or "").strip()
    return {
        "id": str(item.get("id") or _quote_id(title)).strip(),
        "title": title or "未命名原则",
        "body": body,
    }


def _quote_by_id(payload: dict[str, Any], quote_id: str) -> dict[str, Any] | None:
    normalized_id = str(quote_id or "").strip()
    for quote in payload.get("quotes") or []:
        if str(quote.get("id") or "") == normalized_id:
            return quote
    return None


def _write_payload(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def _split_tags(tags: list[str] | str | object | None) -> list[str]:
    if isinstance(tags, str):
        parts = tags.replace("，", ",").replace("｜", ",").split(",")
    elif isinstance(tags, list | tuple | set):
        parts = [str(item) for item in tags]
    else:
        parts = []
    cleaned: list[str] = []
    seen: set[str] = set()
    for part in parts:
        tag = str(part or "").strip()
        if tag and tag not in seen:
            cleaned.append(tag)
            seen.add(tag)
    return cleaned


def _quote_id(text: str) -> str:
    if str(text or "").strip() == DEFAULT_QUOTE_TEXT:
        return DEFAULT_QUOTE_ID
    seed = "".join(char.lower() for char in str(text or "") if char.isalnum())
    return (seed[:32] or f"quote_{int(datetime.now(tz=timezone.utc).timestamp())}")


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()
