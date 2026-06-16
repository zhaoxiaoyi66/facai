from __future__ import annotations

from data.watchlist_stars import WatchlistStarStore


def test_watchlist_star_store_persists_toggle_and_note(tmp_path) -> None:
    path = tmp_path / "cache.sqlite"
    store = WatchlistStarStore(path)

    saved = store.set_star("nvda", True, "长期重点观察")
    reloaded = WatchlistStarStore(path).get_mark("NVDA")

    assert saved["symbol"] == "NVDA"
    assert reloaded["is_starred"] is True
    assert reloaded["star_note"] == "长期重点观察"

    toggled = WatchlistStarStore(path).toggle_star("NVDA")

    assert toggled["is_starred"] is False


def test_watchlist_star_store_does_not_create_pin_or_rank_columns(tmp_path) -> None:
    store = WatchlistStarStore(tmp_path / "cache.sqlite")
    store.set_star("NOW", True)

    with store.connect() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(watchlist_star_marks)").fetchall()}

    assert "is_starred" in columns
    assert "star_note" in columns
    assert "is_pinned" not in columns
    assert "manual_rank" not in columns
    assert "conviction_score" not in columns
