from __future__ import annotations

from pathlib import Path


RUNTIME_ROOTS = [
    Path("app.py"),
    Path("ui"),
    Path("data"),
    Path("tools"),
    Path("scripts"),
]

MOJIBAKE_TOKEN_ESCAPES = (
    r"\u7459\u509c\u5063",
    r"\u95b2\u5d85\u3047",
    r"\u59dd\uff49\u6f70",
    r"\u7490\u71bc\u6f70",
    r"\u93c8",
    r"\u7f02",
    r"\u93b6",
    r"\u95b0",
    r"\u95ba",
    r"\u95b9",
    r"\u95bf",
    r"\u940e\u4f43\u58c8",
    r"\u95b9\u57ab\u6338",
    r"\u7f02\u56e7\u6c26",
    r"\u5a11\u64b3\u79f7",
    r"\u95b8\u5fd3\u61d3",
    r"\u93c9\u581d\u556b",
    r"\u95ba\u51a8\u723c",
    r"\u941f\u6b0f\u5038",
    r"\u95c1\u63d2\u79f6",
    r"\u9225",
    r"\u923c",
    r"\u951f",
    r"\ufffd",
)

MOJIBAKE_TOKENS = tuple(
    token.encode("ascii").decode("unicode_escape") for token in MOJIBAKE_TOKEN_ESCAPES
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
