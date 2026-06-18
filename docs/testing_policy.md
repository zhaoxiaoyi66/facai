# Testing Policy

Use targeted tests by default. Full regression is reserved for release checkpoints,
major refactors, and changes that cross trading, Radar, macro, and UI boundaries.
Docs-only and read-only workflow tasks usually need no tests.

## Slow-Test Baseline

Last measured with:

```powershell
.\.venv\Scripts\python.exe -m pytest --durations=30 -q
```

Result: 952 tests passed in 172.28s. The slowest tests are concentrated in:

1. `tests/test_portfolio_trade_entry.py` planned-ladder and real-ledger buy/add paths, about 1.0-1.2s each.
2. `tests/test_core_logic.py::ScoringTests` HOOD IR/SEC extraction pipeline tests, about 1.4-1.8s.
3. SQLite/cache-heavy workflow tests under portfolio, decision log, macro, and refresh policy.

Streamlit import/source-inspection tests are not the top call-time offenders, but
they still belong in targeted UI runs instead of every small copy or CSS change.

## Test Levels

### 0. No-Test Audit

Use for read-only audits, planning, screenshots, and tasks where no files change.

Command: none.

### 1. UI / Copy / Layout

Use when the change is confined to one UI file, CSS, display copy, or a formatting
helper. Run the tests selected by `scripts/select_tests.py`; do not default to the
full core suite. If the change is pure copy/CSS and the selector recommends a
broad regression, prefer the narrow UI/helper test or a no-test note.

Example:

```powershell
.\.venv\Scripts\python.exe scripts\select_tests.py ui\dashboard.py --python .\.venv\Scripts\python.exe
```

Then run the printed commands.

### 2. Module-Level

Use when a data/helper module changes but the trading ledger is not touched. Run
the module test plus directly related regressions selected by `scripts/select_tests.py`.
Advisory display modules should not force trading workflow tests unless entry,
sync, or journal code changed too.

Example:

```powershell
.\.venv\Scripts\python.exe scripts\select_tests.py data\macro_regime.py --python .\.venv\Scripts\python.exe
```

### 3. Trading Workflow

Use for buy/add, sell/trim, real-ledger, decision log, trade journal, performance,
or Sell Review changes.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_portfolio_trade_entry.py tests/test_portfolio_trade_sync.py tests/test_decision_log.py tests/test_trade_journal_ui.py tests/test_trade_performance.py tests/test_sell_review.py -q
```

### 4. Release / Major Refactor

Use for release candidates, phase freezes, broad refactors, and changes that cross
several business domains.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_core_logic.py tests/test_ai_stock_radar.py tests/test_portfolio_trade_entry.py tests/test_portfolio_trade_sync.py tests/test_decision_log.py tests/test_trade_journal_ui.py tests/test_trade_performance.py tests/test_sell_review.py tests/test_macro_regime.py tests/test_refresh_policy.py -q
```

## Changed-File Mapping

Use `scripts/select_tests.py` to convert changed files into pytest commands. It
emits separate commands for `-k` selectors so broad `tests/test_core_logic.py`
does not run by accident.

Current high-value mappings:

| Changed file | Targeted tests |
| --- | --- |
| `ui/dashboard.py` | `tests/test_dashboard_freshness.py`, `tests/test_core_logic.py -k dashboard` |
| `ui/ai_stock_radar.py` | `tests/test_ai_stock_radar.py`, `tests/test_entry_display.py` |
| `data/ai_stock_radar.py` | `tests/test_ai_stock_radar.py`, `tests/test_entry_display.py`, `tests/test_core_logic.py -k Scoring` |
| `data/macro_regime.py` | `tests/test_macro_regime.py`, `tests/test_core_logic.py -k macro` |
| `data/structure_entry.py` | `tests/test_structure_entry.py` |
| `data/pullback_acceptance.py` | `tests/test_pullback_acceptance.py` |
| `data/buy_execution_context.py` | `tests/test_buy_execution_context.py` |
| `data/portfolio_trade_entry.py` | `tests/test_portfolio_trade_entry.py`, `tests/test_decision_log.py` |
| `data/portfolio_trade_sync.py` | `tests/test_portfolio_trade_sync.py`, `tests/test_trade_performance.py` |
| `ui/trade_journal.py` | `tests/test_trade_journal_ui.py` |
| `ui/portfolio.py` | `tests/test_portfolio_trade_entry.py`, `tests/test_portfolio_model.py` |
| `data/buy_plan.py` | `tests/test_buy_plan.py`, `tests/test_price_alerts.py` |
| `ui/weekend_spread.py`, `data/weekend_spread_backtest.py` | `tests/test_weekend_spread.py` |

## Codex Workflow Rules

1. Small UI, copy, CSS, and display-only tasks should use level 1, not full regression.
2. Single UI-file changes should start with `scripts/select_tests.py`.
3. Buy/sell/ledger logic changes must use level 3 trading workflow tests.
4. Radar scoring or buy-zone changes must run Radar, entry display, and core scoring tests.
5. Macro changes should run macro plus dashboard-related tests.
6. Release candidates, phase freezes, and major refactors should use level 4.
7. Final responses should state the chosen test profile compactly. Mention skipped
   suites only when the omission matters.

## Optional Markers

Markers can be added gradually when touching tests:

- `fast`
- `ui`
- `unit`
- `integration`
- `ledger`
- `macro`
- `radar`
- `slow`

Do not mass-edit tests only to add markers.
