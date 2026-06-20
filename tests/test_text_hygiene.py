from __future__ import annotations

from pathlib import Path


RUNTIME_ROOTS = [
    Path("app.py"),
    Path("ui"),
    Path("data"),
    Path("tools"),
    Path("scripts"),
]

MOJIBAKE_TOKENS = (
    "瑙傜偣",
    "閲嶅ぇ",
    "姝ｉ潰",
    "璐熼潰",
    "鏈",
    "缂",
    "鎶",
    "閰",
    "閺",
    "閹",
    "閿",
    "鐎佃壈",
    "閹垫挸",
    "缂囧氦",
    "娑撳秷",
    "閸忓懓",
    "鏉堝啫",
    "閺冨爼",
    "鐟欏倸",
    "闁插秶",
    "鈥",
    "鈼",
    "锟",
    "\ufffd",
)


def _runtime_python_files() -> list[Path]:
    files: list[Path] = []
    for root in RUNTIME_ROOTS:
        if root.is_file():
            files.append(root)
            continue
        files.extend(
            path
            for path in root.rglob("*.py")
            if "data/cache" not in path.as_posix()
            and "__pycache__" not in path.as_posix()
        )
    return sorted(files)


def test_runtime_python_files_do_not_contain_known_mojibake_tokens() -> None:
    offenders: list[str] = []
    for path in _runtime_python_files():
        text = path.read_text(encoding="utf-8")
        hits = [token for token in MOJIBAKE_TOKENS if token in text]
        if hits:
            offenders.append(f"{path.as_posix()}: {', '.join(hits)}")

    assert offenders == []


def test_global_theme_uses_cjk_safe_font_fallbacks() -> None:
    source = Path("ui/theme.py").read_text(encoding="utf-8")

    assert "--zhx-font-sans" in source
    assert '"PingFang SC"' in source
    assert '"Microsoft YaHei"' in source
    assert '"Noto Sans CJK SC"' in source
    assert "font-family: var(--zhx-font-sans)" in source


def test_sidebar_navigation_uses_current_user_facing_labels() -> None:
    source = Path("app.py").read_text(encoding="utf-8")

    assert 'PAGE_DISCIPLINE_REVIEW = "交易复盘"' in source
    assert 'PAGE_AI_RADAR = "研报中心"' in source
    assert '"交易错题本": PAGE_DISCIPLINE_REVIEW' in source
    assert '"价格位置": PAGE_AI_RADAR' in source
    assert 'PAGE_DISCIPLINE_REVIEW: "交易复盘"' in source
    assert 'PAGE_AI_RADAR: "研报中心"' in source

    nav_block = source[source.index("NAV_STRUCTURE = [") : source.index("]\n\n\ndef main")]
    assert "PAGE_WEEKEND_SPREAD" in nav_block
    assert "PAGE_NEWS_RADAR" in nav_block
    assert "PAGE_WATCHLIST" in nav_block
    assert nav_block.rfind("PAGE_WATCHLIST") > nav_block.rfind("PAGE_SIGNAL_PERFORMANCE")
    assert '"children": [PAGE_SIGNAL_PERFORMANCE]' in nav_block
