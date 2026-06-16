from __future__ import annotations

from typing import Any


ROLE_UNDEFINED = "UNDEFINED"
ROLE_FIRST_CORE = "FIRST_CORE"
ROLE_STRONG_CORE = "STRONG_CORE"
ROLE_SATELLITE = "SATELLITE"
ROLE_TACTICAL = "TACTICAL"
ROLE_OBSERVATION = "OBSERVATION"

VALID_PORTFOLIO_ROLES = {
    ROLE_FIRST_CORE,
    ROLE_STRONG_CORE,
    ROLE_SATELLITE,
    ROLE_TACTICAL,
    ROLE_OBSERVATION,
    ROLE_UNDEFINED,
}

ACTIVE_PORTFOLIO_ROLES = {
    ROLE_FIRST_CORE,
    ROLE_STRONG_CORE,
    ROLE_SATELLITE,
    ROLE_TACTICAL,
    ROLE_OBSERVATION,
}

FORMAL_PORTFOLIO_ROLES = {
    ROLE_FIRST_CORE,
    ROLE_STRONG_CORE,
    ROLE_SATELLITE,
    ROLE_TACTICAL,
}

ROLE_ORDER = {
    ROLE_FIRST_CORE: 0,
    ROLE_STRONG_CORE: 1,
    ROLE_SATELLITE: 2,
    ROLE_TACTICAL: 3,
    ROLE_OBSERVATION: 4,
    ROLE_UNDEFINED: 5,
}

ROLE_LIMITS = {
    ROLE_FIRST_CORE: 1,
    ROLE_STRONG_CORE: 2,
    ROLE_SATELLITE: 2,
    ROLE_TACTICAL: 1,
}

ROLE_LABELS = {
    ROLE_FIRST_CORE: "第一核心",
    ROLE_STRONG_CORE: "强核心",
    ROLE_SATELLITE: "卫星赔率仓",
    ROLE_TACTICAL: "战术仓",
    ROLE_OBSERVATION: "观察仓",
    ROLE_UNDEFINED: "观察仓",
}

ROLE_SHORT_LABELS = {
    ROLE_FIRST_CORE: "第一核心",
    ROLE_STRONG_CORE: "强核心",
    ROLE_SATELLITE: "卫星",
    ROLE_TACTICAL: "战术",
    ROLE_OBSERVATION: "观察",
    ROLE_UNDEFINED: "观察",
}

ROLE_FRAMEWORKS = {
    ROLE_FIRST_CORE: {
        "target_weight": "25%–35%",
        "split": "70% / 30%",
        "description": "第一主线，长期穿越波动，不轻易卖核心仓",
    },
    ROLE_STRONG_CORE: {
        "target_weight": "10%–20%",
        "split": "65% / 35%",
        "description": "高信念主仓，但优先级低于第一核心",
    },
    ROLE_SATELLITE: {
        "target_weight": "5%–10%",
        "split": "50% / 50%",
        "description": "赔率仓、成长仓、题材仓，不能无限加仓",
    },
    ROLE_TACTICAL: {
        "target_weight": "0%–8%",
        "split": "0% / 100%",
        "description": "事件交易、财报交易、短期波段，不许伪装成长期核心",
    },
    ROLE_OBSERVATION: {
        "target_weight": "0%–2%",
        "split": "0% / 100%",
        "description": "小仓观察，不计入正式持仓数量",
    },
    ROLE_UNDEFINED: {
        "target_weight": "0%–2%",
        "split": "0% / 100%",
        "description": "旧记录未设置角色，按观察仓处理",
    },
}

ROLE_FORM_OPTIONS = {
    "第一核心": ROLE_FIRST_CORE,
    "强核心": ROLE_STRONG_CORE,
    "卫星赔率仓": ROLE_SATELLITE,
    "战术仓": ROLE_TACTICAL,
    "观察仓": ROLE_OBSERVATION,
}

BUY_ROLE_FORM_OPTIONS = {
    "第一核心": ROLE_FIRST_CORE,
    "强核心": ROLE_STRONG_CORE,
    "卫星赔率仓": ROLE_SATELLITE,
    "战术仓": ROLE_TACTICAL,
    "观察仓": ROLE_OBSERVATION,
}

SELL_ROLE_REMINDERS = {
    ROLE_FIRST_CORE: "这是第一核心持仓。除非基本面被改写、估值极端泡沫、或出现更强替代标的，否则不建议卖核心仓。",
    ROLE_STRONG_CORE: "这是强核心持仓。卖出前请确认是降低仓位，还是信念下降。",
    ROLE_SATELLITE: "这是卫星赔率仓。允许波段，但不要让亏损扩大成核心仓。",
    ROLE_TACTICAL: "这是战术仓。可以按计划止盈止损，不要临时改成长线信仰仓。",
    ROLE_OBSERVATION: "这是观察仓。卖出只影响观察记录，不计入正式持仓结构。",
    ROLE_UNDEFINED: "这是观察仓。卖出只影响观察记录，不计入正式持仓结构。",
}


def normalize_portfolio_role(value: object, *, default: str | None = ROLE_OBSERVATION) -> str | None:
    text = str(value or "").strip()
    if not text:
        return default
    upper = text.upper()
    if upper == ROLE_UNDEFINED:
        return default if default is not None else ROLE_OBSERVATION
    if upper in ACTIVE_PORTFOLIO_ROLES:
        return upper
    for label, role in ROLE_FORM_OPTIONS.items():
        if text == label:
            return role
    return default


def portfolio_role_label(value: object) -> str:
    role = normalize_portfolio_role(value)
    return ROLE_LABELS.get(role or ROLE_OBSERVATION, ROLE_LABELS[ROLE_OBSERVATION])


def portfolio_role_short_label(value: object) -> str:
    role = normalize_portfolio_role(value)
    return ROLE_SHORT_LABELS.get(role or ROLE_OBSERVATION, ROLE_SHORT_LABELS[ROLE_OBSERVATION])


def portfolio_role_badge_class(value: object) -> str:
    role = normalize_portfolio_role(value)
    return f"role-{str(role or ROLE_OBSERVATION).lower().replace('_', '-')}"


def portfolio_role_sort_key(value: object) -> int:
    role = normalize_portfolio_role(value)
    return ROLE_ORDER.get(role or ROLE_OBSERVATION, ROLE_ORDER[ROLE_OBSERVATION])


def portfolio_role_framework(value: object) -> dict[str, str]:
    role = normalize_portfolio_role(value)
    return dict(ROLE_FRAMEWORKS.get(role or ROLE_OBSERVATION, ROLE_FRAMEWORKS[ROLE_OBSERVATION]))


def portfolio_role_target_weight(value: object) -> str:
    return portfolio_role_framework(value).get("target_weight", "")


def portfolio_role_core_tactical_split(value: object) -> str:
    return portfolio_role_framework(value).get("split", "")


def portfolio_role_description(value: object) -> str:
    return portfolio_role_framework(value).get("description", "")


def is_formal_portfolio_role(value: object) -> bool:
    role = normalize_portfolio_role(value)
    return role in FORMAL_PORTFOLIO_ROLES


def build_portfolio_role_structure(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {role: 0 for role in ACTIVE_PORTFOLIO_ROLES}
    symbols_by_role = {role: [] for role in ACTIVE_PORTFOLIO_ROLES}
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        role = normalize_portfolio_role(row.get("role") or row.get("holdingRole") or row.get("portfolio_role"))
        role = role or ROLE_OBSERVATION
        counts[role] = counts.get(role, 0) + 1
        if symbol:
            symbols_by_role.setdefault(role, []).append(symbol)
    formal_count = sum(counts.get(role, 0) for role in FORMAL_PORTFOLIO_ROLES)
    warnings: list[str] = []
    for role, limit in ROLE_LIMITS.items():
        count = counts.get(role, 0)
        if count > limit:
            warnings.append(f"{portfolio_role_label(role)} 已有 {count} / {limit}，超过角色名额。")
    if formal_count > 8:
        warnings.append("组合过度分散，新增前应先替换。")
    elif formal_count > 6:
        warnings.append("组合开始偏离集中原则。")
    return {
        "counts": counts,
        "symbolsByRole": symbols_by_role,
        "formalCount": formal_count,
        "formalTarget": 6,
        "undefinedCount": 0,
        "warnings": warnings,
    }


def portfolio_role_capacity_warning(role: object, rows: list[dict[str, Any]], *, symbol: str = "") -> str:
    clean_role = normalize_portfolio_role(role)
    if clean_role not in FORMAL_PORTFOLIO_ROLES:
        return ""
    clean_symbol = str(symbol or "").strip().upper()
    count = 0
    formal_count = 0
    has_existing_symbol = False
    for row in rows:
        row_symbol = str(row.get("symbol") or "").strip().upper()
        row_role = normalize_portfolio_role(row.get("role") or row.get("holdingRole") or row.get("portfolio_role"))
        if row_symbol == clean_symbol and clean_symbol:
            has_existing_symbol = True
            continue
        if row_role == clean_role:
            count += 1
        if row_role in FORMAL_PORTFOLIO_ROLES:
            formal_count += 1
    limit = ROLE_LIMITS.get(clean_role)
    if limit is not None and count >= limit:
        return "该角色名额已满。你是在替换低信念持仓，还是继续摊大饼？"
    if not has_existing_symbol and formal_count >= 6:
        return "当前正式持仓已超过 6 只，偏离激进集中型组合原则。新增前建议先明确替换对象。"
    return ""
