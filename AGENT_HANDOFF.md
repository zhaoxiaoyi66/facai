# Agent Handoff

This file is the shared coordination board for the two Codex conversations.
`AGENT_HANDOFF.md` is the single default handoff file for this project. Do not create or use another handoff file unless the user explicitly changes this rule.
Before changing files, read this file and the latest target files.

## Roles

UI conversation:
- Owns `app.py` and `ui/`.
- Focuses on Streamlit layout, navigation, copy, controls, page flow, and visual QA.
- Avoids changing `data/`, `scoring/`, and core business logic unless explicitly coordinated.

Data conversation:
- Owns `data/`, `scoring/`, `indicators/`, `review_autopilot.py`, `ai/`, `tests/`, and `backend/`.
- Focuses on data loading, cache, FMP/Qwen calls, scoring logic, stability, and tests.
- Avoids changing UI files unless explicitly coordinated.

## Working Rules

1. Do not edit the same file from both conversations at the same time.
2. Before editing, write the intended files in the "Active Work" section.
3. After editing, write what changed, what was tested, and any remaining risk.
4. If a task needs both UI and data changes, split it into two handoffs.
5. If there is conflict, stop and ask the user which version should win.
6. Avoid long silent waits. The user has repeatedly felt that Codex is stuck when tool runs or multi-step work are quiet. Send short progress updates before and after slow commands, installs, builds, browser launches, or any step likely to take more than about 30 seconds. If a command is still running or blocked, say so plainly instead of staying silent.

## Active Work

None.

## Latest Handoff

2026-05-25 UI conversation:
- Owner: UI conversation.
- Task: Dashboard UI-only decision lane text hierarchy polish.
- Files touched: `AGENT_HANDOFF.md`, `ui/dashboard.py`.
- What changed: Separated decision-lane header subtitles from row reason text visually. Header subtitles now render smaller, lighter, and more muted, while row reason text is slightly stronger and reads as the per-stock explanation instead of blending with the card description.
- Verification: `C:\dev\facai\.venv\Scripts\python.exe -m py_compile ui\dashboard.py` passed; `C:\dev\facai\.venv\Scripts\python.exe -m pytest tests\test_core_logic.py -q` passed with 251 tests, 30 subtests, and one pytest cache write warning.
- Next needed: Reload/restart the currently running Streamlit page if the lane subtitle/reason hierarchy still looks unchanged; no long-running service was started or stopped in this pass.
- Do not touch yet: data/scoring files, BuyZoneEngine formulas, PositionPlanEngine formulas, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and long-running services were not changed.

2026-05-25 UI conversation:
- Owner: UI conversation.
- Task: Dashboard UI-only lane footer label consistency.
- Files touched: `AGENT_HANDOFF.md`, `ui/dashboard.py`.
- What changed: Made every visible Dashboard decision-lane footer render the same `查看全部` filter action whenever the lane has rows, instead of showing hidden-count copy such as `+X 未显示`. The action still filters the main watchlist to that lane. The legacy `_lane_more_html` helper is not used by the visible footer path and was left compatible with the existing core test.
- Verification: `C:\dev\facai\.venv\Scripts\python.exe -m py_compile ui\dashboard.py` passed; `C:\dev\facai\.venv\Scripts\python.exe -m pytest tests\test_core_logic.py -q` passed with 251 tests, 30 subtests, and one pytest cache write warning.
- Next needed: Reload/restart the currently running Streamlit page if the old hidden-count footer remains in the browser; no long-running service was started or stopped in this pass.
- Do not touch yet: data/scoring files, BuyZoneEngine formulas, PositionPlanEngine formulas, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and long-running services were not changed.

2026-05-25 UI conversation:
- Owner: UI conversation.
- Task: Dashboard UI-only drawer click reliability after lane filter.
- Files touched: `AGENT_HANDOFF.md`, `ui/dashboard.py`.
- What changed: Stabilized Dashboard stock-detail drawer opening after lane footer filters rerender the watchlist. The client drawer component now rebinds its parent-document click handler on each render and calls the latest global drawer opener; the `查看` link also has a direct fallback onclick so filtered rows can still open the drawer even after Streamlit reruns.
- Verification: `C:\dev\facai\.venv\Scripts\python.exe -m py_compile ui\dashboard.py` passed; `C:\dev\facai\.venv\Scripts\python.exe -m pytest tests\test_core_logic.py -q` passed with 251 tests, 30 subtests, and one pytest cache write warning.
- Next needed: Reload/restart the currently running Streamlit page if the browser still has the older drawer handler loaded; no long-running service was started or stopped in this pass.
- Do not touch yet: data/scoring files, BuyZoneEngine formulas, PositionPlanEngine formulas, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and long-running services were not changed.

2026-05-25 Data conversation:
- Owner: Data conversation.
- Task: Fix scoring action conflicts between valuation, entry, risk, and final action.
- Files touched: `AGENT_HANDOFF.md`, `scoring/sector_models.py`, `tests/test_core_logic.py`.
- What changed: Added final-action guardrails so valuation statuses `只观察`, `偏贵`, and `极贵` cannot emit buy actions, C/C- style entry scores below 55 cannot emit buy actions, and medium-high risk cannot emit `可正常分批`. Tightened the VST power-generation drawdown special case so it only applies when entry is at least 55, risk is not medium-high, and valuation status is not a non-buy state. This prevents `entryRating=只观察` / `valuationStatus=只观察` from producing `可小仓分批`.
- Verification: Confirmed the new VST regression failed before the fix and passed after; targeted scoring guardrail tests passed; `C:\dev\facai\.venv\Scripts\python.exe -m py_compile scoring\sector_models.py` passed; `C:\dev\facai\.venv\Scripts\python.exe -m py_compile tests\test_core_logic.py` passed; `C:\dev\facai\.venv\Scripts\python.exe -m pytest tests\test_core_logic.py -q` passed with 251 tests and 30 subtests.
- Next needed: Review scoring output in the UI after restarting/reloading the running Streamlit app if old VST lane placement is still visible.
- Do not touch yet: UI files, data providers, cache paths, database schema/migrations, BuyZoneEngine formulas, PositionPlanEngine formulas, Qwen/AI review, autopilot, external APIs, npm dev server, long-running services, and commits were not changed.

2026-05-25 UI conversation:
- Owner: UI conversation.
- Task: Dashboard UI-only decision lane visual reflow.
- Files touched: `AGENT_HANDOFF.md`, `ui/dashboard.py`.
- What changed: Reworked Dashboard decision lanes into a cleaner terminal-list rhythm. Row order is now ticker + truncated reason + right-aligned buy-point/valuation chip, reducing the split look in the third and fourth lanes. Shortened visible lane labels from `等回踩 / 待确认` and `禁止追高 / 高风险` to `待确认` and `风险隔离`, and changed lane footers to a compact `+X 未显示 · 查看全部` secondary action.
- Verification: `C:\dev\facai\.venv\Scripts\python.exe -m py_compile ui\dashboard.py ui\stock_detail.py` passed; `C:\dev\facai\.venv\Scripts\python.exe -m pytest tests\test_core_logic.py -q` passed with 246 tests, 27 subtests, and one pytest cache write warning.
- Next needed: Reload/restart the currently running Streamlit page if the old lane row order or long lane labels still appear; no long-running service was started or stopped in this pass.
- Do not touch yet: data/scoring files, BuyZoneEngine formulas, PositionPlanEngine formulas, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and long-running services were not changed.

2026-05-25 UI conversation:
- Owner: UI conversation.
- Task: Dashboard UI-only decision lane row simplification.
- Files touched: `AGENT_HANDOFF.md`, `ui/dashboard.py`.
- What changed: Removed redundant action badges from Dashboard decision-lane rows, including `等回踩`, `只观察`, and `禁止追高` as extra row chips. Decision lanes now render each row as ticker + buy-point/valuation state + truncated reason, with a wider reason track and no fourth column to overflow the card.
- Verification: `C:\dev\facai\.venv\Scripts\python.exe -m py_compile ui\dashboard.py ui\stock_detail.py` passed; `C:\dev\facai\.venv\Scripts\python.exe -m pytest tests\test_core_logic.py -q` passed with 246 tests, 27 subtests, and one pytest cache write warning.
- Next needed: Reload/restart the currently running Streamlit page if lane rows still show the removed action chips or clipped right-edge text; no long-running service was started or stopped in this pass.
- Do not touch yet: data/scoring files, BuyZoneEngine formulas, PositionPlanEngine formulas, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and long-running services were not changed.

2026-05-25 UI conversation:
- Owner: UI conversation.
- Task: Dashboard/detail UI-only buy-point chip format.
- Files touched: `AGENT_HANDOFF.md`, `ui/dashboard.py`, `ui/stock_detail.py`.
- What changed: Unified buy-point chip text to the `B+ · 击球区附近` style in the Dashboard watchlist and stock detail page. Added a shared Dashboard formatter for `评分 · 状态`, kept the chip tone driven by the Chinese buy-point label, and slightly widened the watchlist `买点` column while tightening `动作` so the table width stays stable.
- Verification: `C:\dev\facai\.venv\Scripts\python.exe -m py_compile ui\dashboard.py ui\stock_detail.py` passed; `C:\dev\facai\.venv\Scripts\python.exe -m pytest tests\test_core_logic.py -q` passed with 246 tests, 27 subtests, and one pytest cache write warning.
- Next needed: Reload/restart the currently running Streamlit page if buy-point chips still show the older split label/grade format; no long-running service was started or stopped in this pass.
- Do not touch yet: data/scoring files, BuyZoneEngine formulas, PositionPlanEngine formulas, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and long-running services were not changed.

2026-05-25 UI conversation:
- Owner: UI conversation.
- Task: Dashboard UI-only watchlist buy-point grade tail label.
- Files touched: `AGENT_HANDOFF.md`, `ui/dashboard.py`.
- What changed: Restored muted A/B/C/D rating letters inside the Dashboard watchlist `买点` chip while keeping the Chinese buy-point status as the primary text. The chip color still comes from the displayed buy-point label, so `偏贵` / `极贵` / `等回踩` / buy-zone states remain semantically consistent.
- Verification: `C:\dev\facai\.venv\Scripts\python.exe -m py_compile ui\dashboard.py` passed; `C:\dev\facai\.venv\Scripts\python.exe -m pytest tests\test_core_logic.py -q` passed with 246 tests, 27 subtests, and one pytest cache write warning.
- Next needed: Reload/restart the currently running Streamlit page if the watchlist `买点` chips still do not show the small grade tail label; no long-running service was started or stopped in this pass.
- Do not touch yet: data/scoring files, BuyZoneEngine formulas, PositionPlanEngine formulas, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and long-running services were not changed.

2026-05-25 UI conversation:
- Owner: UI conversation.
- Task: Dashboard UI-only decision lane spacing and action-badge color polish.
- Files touched: `AGENT_HANDOFF.md`, `ui/dashboard.py`.
- What changed: Tightened the Dashboard decision-lane row grid so ticker, state badge, action badge, and reason align more evenly across all four lanes. Restored low-saturation action badge colors for labels such as `等回踩`, `只观察`, and `禁止追高` by using the shared badge color system instead of forcing lane actions to gray. Reduced lane footer spacing so `+X 未显示` and `查看全部` read as one compact secondary action rather than split far apart.
- Verification: `C:\dev\facai\.venv\Scripts\python.exe -m py_compile ui\dashboard.py` passed; `C:\dev\facai\.venv\Scripts\python.exe -m pytest tests\test_core_logic.py -q` passed with 246 tests, 27 subtests, and one pytest cache write warning.
- Next needed: Reload/restart the currently running Streamlit page if the decision lanes still show gray action badges or overly wide footer spacing; no long-running service was started or stopped in this pass.
- Do not touch yet: data/scoring files, BuyZoneEngine formulas, PositionPlanEngine formulas, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and long-running services were not changed.

2026-05-25 UI conversation:
- Owner: UI conversation.
- Task: Dashboard UI-only priority strip stock-detail links.
- Files touched: `AGENT_HANDOFF.md`, `ui/dashboard.py`.
- What changed: Made each Dashboard `今日重点` priority item a low-noise link to the stock detail page using the existing app route (`?page=detail&symbol=...`). Styling keeps the terminal priority-strip look and suppresses default blue/underlined link treatment.
- Verification: `C:\dev\facai\.venv\Scripts\python.exe -m py_compile ui\dashboard.py` passed; `C:\dev\facai\.venv\Scripts\python.exe -m pytest tests\test_core_logic.py -q` passed with 246 tests, 27 subtests, and one pytest cache write warning.
- Next needed: Reload/restart the currently running Streamlit page if `今日重点` items are not clickable yet; no long-running service was started or stopped in this pass.
- Do not touch yet: data/scoring files, BuyZoneEngine formulas, PositionPlanEngine formulas, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and long-running services were not changed.

2026-05-25 UI conversation:
- Owner: UI conversation.
- Task: Dashboard UI-only buy-point chip color consistency.
- Files touched: `AGENT_HANDOFF.md`, `ui/dashboard.py`, `ui/stock_detail.py`.
- What changed: Fixed inconsistent buy-point chip colors where identical displayed labels such as `偏贵` could render differently because action text like `禁止追高` was still influencing the chip tone. Added a label-based buy-point tone helper and used it for the Dashboard watchlist buy-point chip and stock detail buy-point pill. Displayed labels now have stable colors: `偏贵` orange, `极贵` deep red, `等回踩` blue, observe/review amber, and buy-zone/cheap states green.
- Verification: A read-only local cache check confirmed each buy-point label maps to only one tone and no inconsistent labels remain; `C:\dev\facai\.venv\Scripts\python.exe -m py_compile ui\dashboard.py ui\stock_detail.py` passed; `C:\dev\facai\.venv\Scripts\python.exe -m pytest tests\test_core_logic.py -q` passed with 246 tests, 27 subtests, and one pytest cache write warning.
- Next needed: Reload/restart the currently running Streamlit page if `偏贵` chips still appear in mixed colors; no long-running service was started or stopped in this pass.
- Do not touch yet: data/scoring files, BuyZoneEngine formulas, PositionPlanEngine formulas, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and long-running services were not changed.

2026-05-25 UI conversation:
- Owner: UI conversation.
- Task: Dashboard UI-only buy-point label semantics cleanup.
- Files touched: `AGENT_HANDOFF.md`, `ui/dashboard.py`.
- What changed: Cleaned up the Dashboard watchlist `买点` column semantics. The column now shows only price/valuation state labels and no longer displays action labels such as `禁止追高`; those remain in the `动作` column. Removed the visible A/B/C/D grade from the main table buy-point chip to avoid confusing rows where the same grade maps to different valuation severities; the raw rating remains available in the chip title/hover and detail views.
- Verification: A read-only local cache check confirmed no watchlist buy-point label resolves to `禁止追高`; `C:\dev\facai\.venv\Scripts\python.exe -m py_compile ui\dashboard.py ui\stock_detail.py` passed; `C:\dev\facai\.venv\Scripts\python.exe -m pytest tests\test_core_logic.py -q` passed with 246 tests, 27 subtests, and one pytest cache write warning.
- Next needed: Reload/restart the currently running Streamlit page if the watchlist buy-point column still shows `禁止追高` or visible letter grades; no long-running service was started or stopped in this pass.
- Do not touch yet: data/scoring files, BuyZoneEngine formulas, PositionPlanEngine formulas, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and long-running services were not changed.

2026-05-25 UI conversation:
- Owner: UI conversation.
- Task: Dashboard UI-only decision lane mutual exclusivity.
- Files touched: `AGENT_HANDOFF.md`, `ui/dashboard.py`.
- What changed: Made Dashboard decision lanes mutually exclusive. Raw lane candidates are still calculated with the existing predicates, then assigned once by priority (`禁止追高 / 高风险` first, `可行动` second, `接近击球区` third, `等回踩 / 待确认` last). This prevents names such as MSFT from appearing in both `接近击球区` and `等回踩 / 待确认`; lane footer filters now use the same exclusive groups.
- Verification: A read-only local cache check confirmed MSFT is assigned only to `接近击球区` and no ticker is duplicated across lanes; `C:\dev\facai\.venv\Scripts\python.exe -m py_compile ui\dashboard.py ui\stock_detail.py` passed; `C:\dev\facai\.venv\Scripts\python.exe -m pytest tests\test_core_logic.py -q` passed with 246 tests, 27 subtests, and one pytest cache write warning.
- Next needed: Reload/restart the currently running Streamlit page if MSFT still appears in two decision lanes; no long-running service was started or stopped in this pass.
- Do not touch yet: data/scoring files, BuyZoneEngine formulas, PositionPlanEngine formulas, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and long-running services were not changed.

2026-05-25 UI conversation:
- Owner: UI conversation.
- Task: Dashboard UI-only priority strip visual cleanup.
- Files touched: `AGENT_HANDOFF.md`, `ui/dashboard.py`.
- What changed: Cleaned up the Dashboard `今日重点` strip after visual review. Converted it from equal-width mini table cells into a lighter terminal ticker tape: visible content is now status dot + ticker + action only, full reason is kept in the hover title, item widths are capped, vertical accent bars were removed, separators are softened, and hover uses a very subtle neutral background.
- Verification: `C:\dev\facai\.venv\Scripts\python.exe -m py_compile ui\dashboard.py ui\stock_detail.py` passed; `C:\dev\facai\.venv\Scripts\python.exe -m pytest tests\test_core_logic.py -q` passed with 246 tests, 27 subtests, and one pytest cache write warning.
- Next needed: Reload/restart the currently running Streamlit page if `今日重点` still looks like the old segmented row; no long-running service was started or stopped in this pass.
- Do not touch yet: data/scoring files, BuyZoneEngine formulas, PositionPlanEngine formulas, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and long-running services were not changed.

2026-05-25 UI conversation:
- Owner: UI conversation.
- Task: Stock detail UI-only buy-point status alignment.
- Files touched: `AGENT_HANDOFF.md`, `ui/stock_detail.py`.
- What changed: Diagnosed the MRVL mismatch as a UI display-source issue: the Dashboard watchlist used the combined buy-point status (`valuationStatus` + `entryRating` + `action`), while the stock detail hero showed raw `score.entry_rating` and the buy-zone panel showed buy-zone-engine `currentZone`. Updated the stock detail page to reuse the Dashboard buy-point display helper, show `买点状态` in the hero and buy-zone meta, and rename the buy-zone-engine field to `系统买区位置` so it is not confused with scoring buy-point status. MRVL now resolves to the same deep-red buy-point status in both views while keeping the buy-zone position separate.
- Verification: `C:\dev\facai\.venv\Scripts\python.exe -m py_compile ui\stock_detail.py ui\dashboard.py` passed; a read-only MRVL comparison script confirmed watchlist/detail buy-point status and tone match; `C:\dev\facai\.venv\Scripts\python.exe -m pytest tests\test_core_logic.py -q` passed with 246 tests, 27 subtests, and one pytest cache write warning.
- Next needed: Reload/restart the currently running Streamlit page if MRVL detail still shows the old raw `C - 只观察` buy-point display; no long-running service was started or stopped in this pass.
- Do not touch yet: data/scoring files, BuyZoneEngine formulas, PositionPlanEngine formulas, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and long-running services were not changed.

2026-05-25 UI conversation:
- Owner: UI conversation.
- Task: Dashboard UI-only buy-point color consistency.
- Files touched: `AGENT_HANDOFF.md`, `ui/dashboard.py`.
- What changed: Aligned buy-point status colors between the Dashboard decision terminal and watchlist table. Added a shared buy-point tone helper so `valuationStatus` and `entryRating` use the same green-to-deep-red scale: buy-zone/cheap states map green, near/wait states map blue, observe/review maps amber, expensive maps orange, and `极贵` / `禁止追高` / high-risk states map deep red. Preserved explicit `极贵` / `禁止追高` labels in the watchlist buy-point chip instead of folding them into generic `偏贵`.
- Verification: `C:\dev\facai\.venv\Scripts\python.exe -m py_compile ui\dashboard.py` passed; `C:\dev\facai\.venv\Scripts\python.exe -m pytest tests\test_core_logic.py -q` passed with 246 tests, 27 subtests, and one pytest cache write warning.
- Next needed: Reload/restart the currently running Streamlit page if it still shows mismatched buy-point colors; no long-running service was started or stopped in this pass.
- Do not touch yet: data/scoring files, BuyZoneEngine formulas, PositionPlanEngine formulas, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and long-running services were not changed.

2026-05-25 UI conversation:
- Owner: UI conversation.
- Task: Dashboard UI-only priority strip ticker dedupe.
- Files touched: `AGENT_HANDOFF.md`, `ui/dashboard.py`.
- What changed: Updated the Dashboard `今日重点` priority strip so each ticker appears at most once. The strip now tracks displayed symbols, skips duplicate tickers from later decision lanes, and continues scanning within each lane so skipped duplicates can be replaced by the next eligible item while preserving the total max of 5 and per-lane max of 2.
- Verification: `C:\dev\facai\.venv\Scripts\python.exe -m py_compile ui\dashboard.py` passed; `C:\dev\facai\.venv\Scripts\python.exe -m pytest tests\test_core_logic.py -q` passed with 246 tests, 27 subtests, and one pytest cache write warning.
- Next needed: Reload/restart the currently running Streamlit page if it still shows duplicate tickers in `今日重点`; no long-running service was started or stopped in this pass.
- Do not touch yet: data/scoring files, BuyZoneEngine formulas, PositionPlanEngine formulas, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and long-running services were not changed.

2026-05-25 UI conversation:
- Owner: UI conversation.
- Task: Dashboard UI-only watchlist entry-rating text treatment.
- Files touched: `AGENT_HANDOFF.md`, `ui/dashboard.py`.
- What changed: Changed the Dashboard watchlist `买点` column from letter-only compact badges to readable text chips. The cell now shows a primary Chinese buy-point label such as `击球区附近`, `回撤买点`, `等回踩`, `只观察`, or `偏贵`, with the original A/B/C/D rating preserved as muted secondary text when available. Rebalanced the watchlist grid by widening `买点` and modestly tightening adjacent columns so the table remains dense but not cramped.
- Verification: `C:\dev\facai\.venv\Scripts\python.exe -m py_compile ui\dashboard.py` passed; `C:\dev\facai\.venv\Scripts\python.exe -m pytest tests\test_core_logic.py -q` passed with 246 tests, 27 subtests, and one pytest cache write warning.
- Next needed: Reload/restart the currently running Streamlit page if it still shows the old letter-only buy-point column; no long-running service was started or stopped in this pass.
- Do not touch yet: data/scoring files, BuyZoneEngine formulas, PositionPlanEngine formulas, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and long-running services were not changed.

2026-05-25 UI conversation:
- Owner: UI conversation.
- Task: Dashboard UI-only decision terminal color harmonization.
- Files touched: `AGENT_HANDOFF.md`, `ui/dashboard.py`.
- What changed: Reduced the Dashboard decision terminal color noise after visual review. Softened the shared badge palette, removed large tinted backgrounds from priority rows and lane headers, kept only small status dots/left accent bars/count text as lane-level color, changed lane action badges to neutral gray so each row no longer shows multiple competing colors, and kept the watchlist table using the same muted badge palette.
- Verification: `C:\dev\facai\.venv\Scripts\python.exe -m py_compile ui\dashboard.py` passed; `C:\dev\facai\.venv\Scripts\python.exe -m pytest tests\test_core_logic.py -q` passed with 246 tests, 27 subtests, and one pytest cache write warning.
- Next needed: Reload/restart the currently running Streamlit page if it still shows the previous colorful version; no long-running service was started or stopped in this pass.
- Do not touch yet: data/scoring files, BuyZoneEngine formulas, PositionPlanEngine formulas, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and long-running services were not changed.

2026-05-25 UI conversation:
- Owner: UI conversation.
- Task: Dashboard UI-only decision terminal color restoration.
- Files touched: `AGENT_HANDOFF.md`, `ui/dashboard.py`.
- What changed: Restored visible low-saturation status color in the Dashboard decision terminal. Removed the final CSS overrides that forced lane count badges and row badges back to gray, added tone classes to the priority strip rows and decision-lane headers, gave each lane a subtle accent stripe/background, switched `接近击球区` to blue and `等回踩 / 待确认` to amber, and added muted accent hover states to the lane footer buttons.
- Verification: `C:\dev\facai\.venv\Scripts\python.exe -m py_compile ui\dashboard.py` passed; `C:\dev\facai\.venv\Scripts\python.exe -m pytest tests\test_core_logic.py -q` passed with 246 tests, 27 subtests, and one pytest cache write warning.
- Next needed: Reload/restart the currently running Streamlit page if the browser is still showing the old gray version; no long-running service was started or stopped in this pass.
- Do not touch yet: data/scoring files, BuyZoneEngine formulas, PositionPlanEngine formulas, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and long-running services were not changed.

2026-05-25 UI conversation:
- Owner: UI conversation.
- Task: Repair local `.venv` Python launcher path.
- Files touched: `AGENT_HANDOFF.md`, `.venv/pyvenv.cfg`.
- What changed: Updated `.venv/pyvenv.cfg` so the virtualenv points to the available bundled Python 3.12.13 runtime instead of the missing `C:\Users\User\AppData\Local\Programs\Python\Python312` interpreter. `.venv\Scripts\python.exe` now starts directly from `C:\dev\facai`.
- Verification: `C:\dev\facai\.venv\Scripts\python.exe --version` returns Python 3.12.13; `py_compile ui\dashboard.py` passed; `pytest tests\test_core_logic.py -q` passed with 246 tests, 27 subtests, and one pytest cache write warning.
- Next needed: Use the project `.venv\Scripts\python.exe` directly for future verification commands.
- Do not touch yet: app code, data/scoring files, BuyZoneEngine formulas, PositionPlanEngine formulas, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and long-running services were not changed by this environment repair.

2026-05-25 UI conversation:
- Owner: UI conversation.
- Task: Dashboard UI-only watchlist table terminal alignment polish.
- Files touched: `AGENT_HANDOFF.md`, `ui/dashboard.py`.
- What changed: Tuned the Dashboard watchlist table only. Set the code column around a 100px baseline with ticker left-aligned at 700 weight, gave the price/market column a 128px baseline with left/right breathing room and stable tabular numbers, restored 44px terminal row height, softened table borders/header weight, kept the action/data/view columns compact, and preserved badge/ellipsis protections without reintroducing right-edge price/market collision.
- Verification: The requested `.venv\Scripts\python.exe` commands still fail because the launcher points to a missing Python312 path. Equivalent `py_compile` passed with bundled Python 3.12.13; `pytest tests/test_core_logic.py -q` passed with 246 tests, 27 subtests, and one pytest cache write warning.
- Next needed: Restart/reload the running Streamlit service so the browser picks up current `C:\dev\facai` source, then visually review the watchlist table before committing.
- Do not touch yet: data/scoring files, BuyZoneEngine formulas, PositionPlanEngine formulas, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and long-running services were not changed.

2026-05-25 UI conversation:
- Owner: UI conversation.
- Task: Dashboard UI-only watchlist column alignment terminal polish.
- Files touched: `AGENT_HANDOFF.md`, `ui/dashboard.py`.
- What changed: Tuned only the Dashboard watchlist table presentation. Rebalanced the eight grid tracks around the requested baselines (`代码` 96px, `价格 / 市值` 120px, `质量` 76px, `买点` 82px, `风险` 64px, `动作` 190px, `数据` 70px, `查看` 64px), restored 42px terminal row height, added a dedicated `price-market-cell` with right-aligned tabular numbers plus left/right breathing room, kept ticker-only stock cells at 700 weight, and preserved badge/ellipsis overflow protection.
- Verification: The requested `.venv\Scripts\python.exe` commands still fail because the launcher points to a missing Python312 path. Equivalent `py_compile` passed with bundled Python 3.12.13; `pytest tests/test_core_logic.py -q` passed with 246 tests, 27 subtests, and one pytest cache write warning.
- Next needed: Restart/reload the running Streamlit service so the browser picks up current `C:\dev\facai` source, then visually review the watchlist table alignment before committing.
- Do not touch yet: data/scoring files, BuyZoneEngine formulas, PositionPlanEngine formulas, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and long-running services were not changed.

2026-05-25 UI conversation:
- Owner: UI conversation.
- Task: Dashboard UI-only decision terminal polish.
- Files touched: `AGENT_HANDOFF.md`, `ui/dashboard.py`.
- What changed: Refined the Dashboard `决策台` presentation only. Rebuilt `今日重点` as a compact terminal ticker strip with status dot, ticker, action, and truncated reason; removed the strong divider feel; softened the strip background/border. Reduced noise in the four decision-lane cards by fixing row height, limiting each row to ticker plus two muted badges plus truncated reason, reducing badge saturation, tightening panel spacing, and making lane footers smaller and more muted.
- Verification: Prompt path `C:\dev\facai.venv\Scripts\python.exe` does not exist. Equivalent `py_compile` passed with bundled Python 3.12.13; `pytest tests/test_core_logic.py -q` passed with 246 tests, 27 subtests, and one pytest cache write warning.
- Next needed: Restart/reload the running Streamlit service so the browser picks up current `C:\dev\facai` source, then visually review the Dashboard decision terminal before committing.
- Do not touch yet: data/scoring files, BuyZoneEngine formulas, PositionPlanEngine formulas, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and long-running services were not changed.

2026-05-25 UI conversation:
- Owner: UI conversation.
- Task: Dashboard UI-only price-quality column separation.
- Files touched: `AGENT_HANDOFF.md`, `ui/dashboard.py`.
- What changed: Fixed the Dashboard watchlist table collision shown in the screenshot where market-cap text visually stuck to the quality badge. Removed right alignment from the `价格 / 市值` column and added a stable gutter between the price/market column and the quality column while preserving the eight-column wide terminal layout and existing overflow protections.
- Verification: The requested `.venv\Scripts\python.exe` commands still fail because the launcher points to a missing Python312 path. Equivalent `py_compile` passed with bundled Python 3.12.13; `pytest tests/test_core_logic.py -q` passed with 246 tests, 27 subtests, and one pytest cache write warning.
- Next needed: Restart/reload the running Streamlit service so the browser picks up current `C:\dev\facai` source, then visually check that market cap and quality badge are no longer glued together.
- Do not touch yet: data/scoring files, BuyZoneEngine formulas, PositionPlanEngine formulas, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and long-running services were not changed.

2026-05-25 UI conversation:
- Owner: UI conversation.
- Task: Dashboard UI-only watchlist narrow-view column balance.
- Files touched: `AGENT_HANDOFF.md`, `ui/dashboard.py`.
- What changed: Balanced the Dashboard watchlist grid after reviewing the captured narrow viewport. Reduced code-column expansion, gave price/market and rating columns more deliberate spacing, kept the action column capped, and added a `max-width: 760px` responsive grid (`88 / 128 / 66 / 74 / 64 / 190 / 64 / 52px`) so the first visible columns no longer look misaligned or cramped. Kept ticker-only stock cells, muted `查看 ›`, and existing overflow/badge protections.
- Verification: The requested `.venv\Scripts\python.exe` commands still fail because the launcher points to a missing Python312 path. Equivalent `py_compile` passed with bundled Python 3.12.13; `pytest tests/test_core_logic.py -q` passed with 246 tests, 27 subtests, and one pytest cache write warning.
- Next needed: Restart/reload the running Streamlit service so the browser picks up current `C:\dev\facai` source, then visually review the Dashboard table in both wide and narrow widths.
- Do not touch yet: data/scoring files, BuyZoneEngine formulas, PositionPlanEngine formulas, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and long-running services were not changed.

2026-05-25 UI conversation:
- Owner: UI conversation.
- Task: Dashboard UI-only wide watchlist terminal-table repair.
- Files touched: `AGENT_HANDOFF.md`, `ui/dashboard.py`.
- What changed: Repaired the Dashboard watchlist table after the over-compression pass. Restored the table/grid to full available width with `width:100%`, raised the grid minimum width, kept the eight-column terminal layout, capped the action column with `minmax(190px, 220px)`, and let the other columns absorb remaining width. Kept ticker-only stock cells, muted `查看 ›`, badge max-width/ellipsis protections, dot-status truncation, and two-line action cells.
- Verification: The requested `.venv\Scripts\python.exe` commands still fail because the launcher points to a missing Python312 path. Equivalent `py_compile` passed with bundled Python 3.12.13; `pytest tests/test_core_logic.py -q` passed with 246 tests, 27 subtests, and one pytest cache write warning.
- Next needed: Restart/reload the running Streamlit service so the browser picks up current `C:\dev\facai` source, then visually review the wide Dashboard table before committing.
- Do not touch yet: data/scoring files, BuyZoneEngine formulas, PositionPlanEngine formulas, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and long-running services were not changed.

2026-05-25 UI conversation:
- Owner: UI conversation.
- Task: Dashboard UI-only watchlist table column-density polish.
- Files touched: `AGENT_HANDOFF.md`, `ui/dashboard.py`.
- What changed: Tightened only the Dashboard watchlist table layout. Replaced the flex-like action column with fixed grid tracks (`110 / 120 / 80 / 90 / 70 / 200 / 70 / 64px`), kept action cells to two truncated lines, added stronger min-width/overflow/ellipsis constraints to all cells, badges, dot statuses, and the neutral view action so the data and view columns stay close instead of drifting right.
- Verification: Prompt path `C:\dev\facai.venv\Scripts\python.exe` does not exist. Equivalent `py_compile` passed with bundled Python 3.12.13; `pytest tests/test_core_logic.py -q` passed with 246 tests, 27 subtests, and one pytest cache write warning.
- Next needed: Visual review in the running Streamlit app after restarting/reloading the Streamlit service so it picks up the current `C:\dev\facai` source.
- Do not touch yet: data/scoring files, BuyZoneEngine formulas, PositionPlanEngine formulas, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and long-running services were not changed.

2026-05-25 UI conversation:
- Owner: UI conversation.
- Task: Dashboard UI-only stock-cell duplicate label removal.
- Files touched: `AGENT_HANDOFF.md`, `ui/dashboard.py`.
- What changed: Removed the repeated `研究标的` sublabel under the ticker in the Dashboard watchlist table so the code/name column no longer shows duplicate filler copy.
- Verification: `py_compile` passed for `ui/dashboard.py` using bundled Python 3.12.13 because the project venv launcher still points to a missing Python312 path.
- Next needed: Visual review in the running Streamlit app; commit together with the Dashboard UI compaction checkpoint if it looks good.
- Do not touch yet: data/scoring files, BuyZoneEngine formulas, PositionPlanEngine formulas, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and long-running services were not changed.

2026-05-25 UI conversation:
- Owner: UI conversation.
- Task: Dashboard UI-only visual compaction and terminal-grid polish.
- Files touched: `AGENT_HANDOFF.md`, `ui/dashboard.py`.
- What changed: Tightened the Dashboard first screen without changing data or scoring logic. Reduced header/stat-ribbon/decision-section spacing, made `今日重点` a compact one-line information band, shortened decision-lane cards and fixed row overflow/truncation, changed lane overflow footer copy to a low-noise `+X 未显示    查看全部 →` treatment, tightened the watchlist grid row/column sizing, added stronger min-width/overflow constraints for table cells and badges, softened badge colors, and made `查看` a neutral text action.
- Verification: The requested `.venv\Scripts\python.exe` command could not start because the venv launcher points to a missing Python312 path. Equivalent `py_compile` passed with bundled Python 3.12.13; `pytest tests/test_core_logic.py -q` passed with 246 tests, 27 subtests, and one pytest cache write warning.
- Next needed: Visual review in the running Streamlit app; commit only if the Dashboard spacing and terminal-grid feel stable.
- Do not touch yet: data/scoring files, BuyZoneEngine formulas, PositionPlanEngine formulas, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and long-running services were not changed.

2026-05-25 Data conversation:
- Owner: Data conversation.
- Task: Windows local launcher for ZHX Research Streamlit app.
- Files touched: `AGENT_HANDOFF.md`, `scripts/launch_zhx_research.py`, `start_zhx_research.bat`, `docs/launcher.md`.
- What changed: Added a local launcher that locates the `C:\dev\facai` project root, uses `.venv\Scripts\python.exe`, detects whether `http://localhost:8501` is already responding, starts `streamlit run app.py --server.port 8501 --server.headless true` when needed, opens the browser after readiness, and reports startup/log errors without reading or printing `.env`. Added a BAT entry point and created a Windows Desktop shortcut named `ZHX Research` pointing to `C:\dev\facai\start_zhx_research.bat`. User chose the simpler BAT plus Desktop shortcut flow instead of the heavier EXE/PyInstaller flow.
- Verification: Confirmed Streamlit entry is `app.py`; `py_compile` passed for `scripts/launch_zhx_research.py`; `start_zhx_research.bat` ran successfully and detected the existing HTTP 200 service on port 8501; Desktop shortcut exists and targets the BAT launcher. EXE was intentionally not built and PyInstaller was not installed.
- Next needed: Use the Desktop shortcut or `start_zhx_research.bat` to open the local app.
- Do not touch yet: UI files, scoring logic, data providers, BuyZoneEngine formulas, PositionPlanEngine formulas, database schema/migrations, `.env`, SQLite databases, caches, external APIs, npm dev server, and commits were not changed.

2026-05-24 UI conversation:
- Owner: UI conversation.
- Task: Buy zone plan page UI-only display-category fix for far-from-trigger names.
- Files touched: `AGENT_HANDOFF.md`, `ui/buy_zone.py`.
- What changed: Continued the buy-zone UI checkpoint. Added a unified `resolve_buy_zone_display_category(row)` presentation helper with a 15% near-trigger threshold and reused it for summary counts, filter categories, priority-strip near eligibility, execution-table status, and trigger-condition copy. Far-from-trigger rows now stay in `等回踩` and show `仍需大幅回落` / `触发价 $xx.xx` instead of `接近买区` / `可考虑第一笔买入`.
- Verification: `py_compile` passed for `ui/buy_zone.py` and `ui/dashboard.py`; `pytest tests/test_core_logic.py -q` passed with 246 tests and 27 subtests.
- Next needed: Visual review in the running Streamlit app. Commit this buy-zone UI checkpoint only after MRVL/MU no longer look near-trigger in the page.
- Do not touch yet: BuyZoneEngine formulas, PositionPlanEngine formulas, scoring logic, data providers, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and unrelated files were not changed.

2026-05-24 UI conversation:
- Owner: UI conversation.
- Task: Dashboard UI-only badge overflow and decision-lane footer polish.
- Files touched: `AGENT_HANDOFF.md`, `ui/dashboard.py`.
- What changed: Continued the Dashboard UI visual checkpoint. Shortened watchlist rating badges in the visible table (`B+ - 稳健` -> `B+`) while preserving the full label as tooltip/title, added stricter badge/cell overflow constraints, and made decision-lane footer actions more tertiary by shortening the label to `X 只未显示    查看`, left-aligning it, removing button chrome, and keeping the existing lane-filter click behavior.
- Verification: `py_compile` passed for `ui/dashboard.py`; `pytest tests/test_core_logic.py -q` passed with 246 tests and 27 subtests.
- Next needed: Visual review in the running Streamlit app. Commit this Dashboard UI visual checkpoint only after badge overflow and lane footer polish feel stable.
- Do not touch yet: scoring logic, data providers, BuyZoneEngine formulas, PositionPlanEngine formulas, Review Center backend, review status enums, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and unrelated files were not changed.

2026-05-24 UI conversation:
- Owner: UI conversation.
- Task: Dashboard UI-only overflow and terminal-table polish.
- Files touched: `AGENT_HANDOFF.md`, `ui/dashboard.py`.
- What changed: Tightened the Dashboard terminal layout without touching backend logic. Added overflow/min-width constraints to the priority strip, decision lanes, and watchlist table; stabilized lane row grids; truncated ticker/badge/reason text; shortened watchlist stock subtext to avoid ugly company-name clipping; reduced watchlist min width; and changed the `查看 ›` action into a lower-noise neutral ghost entry.
- Verification: `py_compile` passed for `ui/dashboard.py`; `pytest tests/test_core_logic.py -q` passed with 246 tests and 27 subtests.
- Next needed: Visual review in the running Streamlit app. Commit this UI visual system checkpoint only after the Dashboard overflow/spacing feels stable.
- Do not touch yet: scoring logic, data providers, BuyZoneEngine formulas, PositionPlanEngine formulas, Review Center backend, review status enums, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and unrelated files were not changed.

2026-05-24 UI conversation:
- Owner: UI conversation.
- Task: Global UI Visual System checkpoint; align Dashboard decision overview with the buy-zone terminal style.
- Files touched: `AGENT_HANDOFF.md`, `ui/dashboard.py`.
- What changed: Converted the Dashboard summary cards into a unified status ribbon, added a compact `决策台` with `今日重点` priority strip, tightened decision lanes inside one panel, and rebuilt the watchlist display as a dense terminal-style execution table with reduced columns (`代码 / 价格市值 / 质量 / 买点 / 风险 / 动作 / 数据 / 查看`). The main table row and neutral `查看 ›` action both open the existing StockDetailDrawer. Data status now uses dot-plus-label instead of a colored pill, technical/provider errors are sanitized in main-table cells, and the page max-width/color/badge language now matches the buy-zone execution console more closely.
- Verification: `py_compile` passed for `ui/dashboard.py`; `pytest tests/test_core_logic.py -q` passed with 246 tests and 27 subtests.
- Next needed: Visual review in the running Streamlit app. Commit this Global UI Visual System checkpoint only after the homepage and buy-zone page both feel stable.
- Do not touch yet: scoring logic, data providers, BuyZoneEngine formulas, PositionPlanEngine formulas, Review Center backend, review status enums, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and unrelated files were not changed.

2026-05-24 UI conversation:
- Owner: UI conversation.
- Task: Buy zone plan page UI-only terminal polish and near-trigger priority filter.
- Files touched: `AGENT_HANDOFF.md`, `ui/buy_zone.py`.
- What changed: Continued the same buy-zone UI checkpoint. Kept the fixed `买区执行台` structure and tightened the final terminal-style details: compact filter labels (`接近`, `手动`), table headers (`股票 / 当前动作 / 触发条件 / 建议仓位 / 置信度 / 查看`), neutral `查看 ›` action, slightly tighter status ribbon/table spacing, and a UI-only near-trigger filter so priority-strip `接近` items only include stocks within 15% of the trigger price. The trigger-distance percentage now uses current price as the denominator, preventing far-away names such as MRVL/MU from showing misleading `距触发 220% / 290%` priority prompts.
- Verification: `py_compile` passed for `ui/buy_zone.py` and `ui/dashboard.py`; `pytest tests/test_core_logic.py -q` passed with 246 tests and 27 subtests.
- Next needed: Review visually. Commit this buy-zone UI checkpoint only after the page feels stable; do not start another checkpoint automatically.
- Do not touch yet: BuyZoneEngine formulas, PositionPlanEngine formulas, scoring logic, data providers, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and unrelated UI files were not changed.

2026-05-24 UI conversation:
- Owner: UI conversation.
- Task: Buy zone plan page UI-only priority-strip overlap fix.
- Files touched: `AGENT_HANDOFF.md`, `ui/buy_zone.py`.
- What changed: Continued the same buy-zone UI checkpoint. Fixed overlapping text in the `今日优先` strip by removing the secondary trigger-condition line from each priority item and simplifying each item to status, ticker, and one primary action. Tightened the strip grid to single-line items with ellipsis overflow, keeping the execution table structure unchanged.
- Verification: `py_compile` passed for `ui/buy_zone.py` and `ui/dashboard.py`; `pytest tests/test_core_logic.py -q` passed with 246 tests and 27 subtests.
- Next needed: Review visually. Commit this buy-zone UI checkpoint only after the page feels stable; do not start another checkpoint automatically.
- Do not touch yet: BuyZoneEngine formulas, PositionPlanEngine formulas, scoring logic, data providers, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and unrelated UI files were not changed.

2026-05-24 UI conversation:
- Owner: UI conversation.
- Task: Buy zone plan page UI-only high-end execution console polish.
- Files touched: `AGENT_HANDOFF.md`, `ui/buy_zone.py`.
- What changed: Continued the same buy-zone UI checkpoint. Replaced the cheap `详情 →` link with a neutral `查看 ›` ghost action, renamed and tightened the execution table columns to `股票 / 当前动作 / 触发条件 / 建议仓位 / 置信度 / 查看`, tuned column widths and row density, changed the priority area from pill-like chips into a structured terminal-style summary strip, softened the segmented filter, and kept confidence as dot-plus-label instead of a colored pill.
- Verification: `py_compile` passed for `ui/buy_zone.py` and `ui/dashboard.py`; `pytest tests/test_core_logic.py -q` passed with 246 tests and 27 subtests.
- Next needed: Review visually. Commit this buy-zone UI checkpoint only after the page feels stable; do not start another checkpoint automatically.
- Do not touch yet: BuyZoneEngine formulas, PositionPlanEngine formulas, scoring logic, data providers, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and unrelated UI files were not changed.

2026-05-24 UI conversation:
- Owner: UI conversation.
- Task: Buy zone plan page UI-only visual noise reduction.
- Files touched: `AGENT_HANDOFF.md`, `ui/buy_zone.py`.
- What changed: Continued the same buy-zone UI checkpoint. Reduced visual noise in the unified `买区执行台`: priority items now use small status dots instead of colored pills, confidence now renders as a low-noise dot plus label, detail actions are muted `详情 →` text links, panel/card borders and summary dividers were softened, the page background gained a subtle gray layer, row hover was toned down, and secondary text was kept readable at `#64748b`.
- Verification: `py_compile` passed for `ui/buy_zone.py` and `ui/dashboard.py`; `pytest tests/test_core_logic.py -q` passed with 246 tests and 27 subtests.
- Next needed: Review visually. Commit this buy-zone UI checkpoint only after the page feels stable; do not start another checkpoint automatically.
- Do not touch yet: BuyZoneEngine formulas, PositionPlanEngine formulas, scoring logic, data providers, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and unrelated UI files were not changed.

2026-05-24 UI conversation:
- Owner: UI conversation.
- Task: Buy zone plan page UI-only unified execution console.
- Files touched: `AGENT_HANDOFF.md`, `ui/buy_zone.py`.
- What changed: Continued the same buy-zone UI checkpoint. Merged the former standalone priority panel, filter row, and execution list into one `买区执行台` flow: toolbar title plus compact segmented filter, in-panel `今日优先` priority strip, and the execution list as the panel body. Removed the independent `今日优先事项` render path, kept the dense action-table direction, restored a separate confidence column, strengthened secondary text readability, and kept details in the drawer.
- Verification: `py_compile` passed for `ui/buy_zone.py` and `ui/dashboard.py`; `pytest tests/test_core_logic.py -q` passed with 246 tests and 27 subtests.
- Next needed: Review visually. Commit this buy-zone UI checkpoint only after the page feels stable; do not start another checkpoint automatically.
- Do not touch yet: BuyZoneEngine formulas, PositionPlanEngine formulas, scoring logic, data providers, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and unrelated UI files were not changed.

2026-05-24 UI conversation:
- Owner: UI conversation.
- Task: Buy zone plan page UI-only execution-list regression fix.
- Files touched: `AGENT_HANDOFF.md`, `ui/buy_zone.py`.
- What changed: Preserved the approved compact `今日优先事项` panel and changed the execution list away from the old dense database-table feel. Replaced the eight narrow columns with five semantic compact action-row zones: stock/current price, action status plus suggestion, trigger condition, position plus confidence, and details. Kept the unified panel, shallow separators, compact desktop density, and detail drawer behavior.
- Verification: `py_compile` passed for `ui/buy_zone.py` and `ui/dashboard.py`; `pytest tests/test_core_logic.py -q` passed with 246 tests and 27 subtests.
- Next needed: Review the buy-zone execution list visually. Commit only after the UI feels right; do not start another checkpoint automatically.
- Do not touch yet: BuyZoneEngine formulas, PositionPlanEngine formulas, scoring logic, data providers, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and unrelated UI files were not changed.

2026-05-24 UI conversation:
- Owner: UI conversation.
- Task: Buy zone plan page UI-only dense execution table polish.
- Files touched: `AGENT_HANDOFF.md`, `ui/buy_zone.py`.
- What changed: Continued the same buy-zone UI checkpoint. Converted the execution list from sparse per-row action cards back into a dense desktop execution table with a single table panel, fixed eight-column grid, compact row height, shallow separators, smaller badges, right-aligned current price, compact two-line trigger/position cells, and a compressed `今日优先事项` panel. Kept the filter in the execution-list toolbar and preserved the detail drawer interaction.
- Verification: `py_compile` passed for `ui/buy_zone.py` and `ui/dashboard.py`; `pytest tests/test_core_logic.py -q` passed with 246 tests and 27 subtests.
- Next needed: Review and commit this buy-zone UI checkpoint if the diff looks good. Do not start another checkpoint automatically.
- Do not touch yet: BuyZoneEngine formulas, PositionPlanEngine formulas, scoring logic, data providers, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and unrelated UI files were not changed.

2026-05-24 UI conversation:
- Owner: UI conversation.
- Task: Buy zone plan page UI-only priority panel and filter placement rewrite.
- Files touched: `AGENT_HANDOFF.md`, `ui/buy_zone.py`.
- What changed: Continued the same buy-zone UI checkpoint. Replaced the three-lane `今日动作面板` with a compact `今日优先事项` list capped at five rows, ordered by executable, near-buy-zone, review-needed, then no-chase priority. Each row now shows status, ticker, primary action, and reason/trigger copy. Added compact count chips, removed the large empty-card layout, moved the filter into the `执行清单` toolbar row, and kept the action-list drawer behavior unchanged.
- Verification: `py_compile` passed for `ui/buy_zone.py` and `ui/dashboard.py`; `pytest tests/test_core_logic.py -q` passed with 246 tests and 27 subtests.
- Next needed: Review and commit this buy-zone UI checkpoint if the diff looks good. Do not start another checkpoint automatically.
- Do not touch yet: BuyZoneEngine formulas, PositionPlanEngine formulas, scoring logic, data providers, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and unrelated UI files were not changed.

2026-05-24 UI conversation:
- Owner: UI conversation.
- Task: Buy zone plan page UI-only `执行清单` compact action list rewrite.
- Files touched: `AGENT_HANDOFF.md`, `ui/buy_zone.py`.
- What changed: Continued the same buy-zone UI checkpoint. Replaced the dense multi-column execution table with compact action-list rows using a stable six-part grid: stock identity/current price, status plus suggested action, trigger condition, position action, confidence, and detail action. Removed narrow current-price/current-add/max-position columns, kept current add as action text instead of `0%`, kept trigger conditions as neutral two-line prompts, made rows 60px compact cards with light borders, and preserved the detail drawer interaction.
- Verification: `py_compile` passed for `ui/buy_zone.py` and `ui/dashboard.py`; `pytest tests/test_core_logic.py -q` passed with 246 tests and 27 subtests.
- Next needed: Review and commit this buy-zone UI checkpoint if the diff looks good. Do not start another checkpoint automatically.
- Do not touch yet: BuyZoneEngine formulas, PositionPlanEngine formulas, scoring logic, data providers, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and unrelated UI files were not changed.

2026-05-24 UI conversation:
- Owner: UI conversation.
- Task: Buy zone plan page UI-only `今日动作面板` layout polish.
- Files touched: `AGENT_HANDOFF.md`, `ui/buy_zone.py`.
- What changed: Continued the same buy-zone UI checkpoint and touched only the action panel presentation. Added per-lane count badges, changed action lanes into compact cards, tightened row height, added primary/secondary row text, added muted `暂无` empty rows, added lightweight `还有 X 只 →` overflow rows, softened separators, reduced unused vertical space, and separated the panel from the filter with clearer spacing.
- Verification: `py_compile` passed for `ui/buy_zone.py` and `ui/dashboard.py`; `pytest tests/test_core_logic.py -q` passed with 246 tests and 27 subtests.
- Next needed: Review and commit this buy-zone UI checkpoint if the diff looks good. Do not start another checkpoint automatically.
- Do not touch yet: BuyZoneEngine formulas, PositionPlanEngine formulas, scoring logic, data providers, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and unrelated UI files were not changed.

2026-05-24 UI conversation:
- Owner: UI conversation.
- Task: Buy zone plan page UI-only visual alignment polish.
- Files touched: `AGENT_HANDOFF.md`, `ui/buy_zone.py`.
- What changed: Continued the same buy-zone UI checkpoint. Widened and centered the page container, aligned status ribbon, action panel, filter, table, and advanced settings to one 1200px content width, tightened vertical rhythm, made the action panel visually stronger, stabilized the final table grid widths, raised table typography to 13px, softened table borders/hover, right-aligned numeric columns, made ticker/action/trigger text more consistent, unified badge height/font/weight, lowered color saturation, and turned the detail action into a muted ghost text button.
- Verification: `py_compile` passed for `ui/buy_zone.py` and `ui/dashboard.py`; `pytest tests/test_core_logic.py -q` passed with 246 tests and 27 subtests.
- Next needed: Review and commit this buy-zone UI checkpoint if the diff looks good. Do not start another checkpoint automatically.
- Do not touch yet: BuyZoneEngine formulas, PositionPlanEngine formulas, scoring logic, data providers, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and unrelated UI files were not changed.

2026-05-24 UI conversation:
- Owner: UI conversation.
- Task: Buy zone plan page UI-only final execution-panel polish.
- Files touched: `AGENT_HANDOFF.md`, `ui/buy_zone.py`.
- What changed: Continued the same buy-zone UI checkpoint. Updated the page subtitle to execution-focused copy, turned the status cards into a compact summary ribbon, renamed `今日执行摘要` to `今日动作面板`, made each action row show ticker plus primary action and secondary reason, renamed the main table to `执行清单`, removed `来源` from the main table while keeping source in the drawer, stabilized the final nine-column grid, kept trigger conditions as neutral two-line operation prompts, preserved folded manual/advanced settings, and kept the legacy page-contract phrase as a non-visible test anchor.
- Verification: `py_compile` passed for `ui/buy_zone.py` and `ui/dashboard.py`; `pytest tests/test_core_logic.py -q` passed with 246 tests and 27 subtests.
- Next needed: Review and commit this buy-zone UI checkpoint if the diff looks good. Do not start another checkpoint automatically.
- Do not touch yet: BuyZoneEngine formulas, PositionPlanEngine formulas, scoring logic, data providers, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and unrelated UI files were not changed.

2026-05-24 UI conversation:
- Owner: UI conversation.
- Task: Buy zone plan page UI-only alignment and visual-system polish.
- Files touched: `AGENT_HANDOFF.md`, `ui/buy_zone.py`.
- What changed: Continued the same buy-zone UI checkpoint. Aligned status cards, execution summary, filter, table, and advanced settings to one page width; compressed top cards; changed `今日执行摘要` into one stable three-column panel with `暂无` empty states; tightened the filter into a compact segmented control; stabilized the table grid widths; right-aligned numeric columns; made trigger cells neutral primary/secondary text instead of link-like blue text; and unified badge size, weight, and softer color semantics.
- Verification: `py_compile` passed for `ui/buy_zone.py` and `ui/dashboard.py`; `pytest tests/test_core_logic.py -q` passed with 246 tests and 27 subtests.
- Next needed: Review and commit this buy-zone UI checkpoint if the diff looks good. Do not start another checkpoint automatically.
- Do not touch yet: BuyZoneEngine formulas, PositionPlanEngine formulas, scoring logic, data providers, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and unrelated UI files were not changed.

2026-05-24 UI conversation:
- Owner: UI conversation.
- Task: Buy zone plan page UI-only trigger-condition and table-density polish.
- Files touched: `AGENT_HANDOFF.md`, `ui/buy_zone.py`.
- What changed: Renamed the main table `下一触发` column to `触发条件`, added a UI-only `format_trigger_cell(row)` formatter, rendered trigger cells as consistent primary/secondary lines, mapped invalid/data-insufficient/no-chase/buy-zone states to user-facing trigger copy, changed zero-add rows to muted action labels, softened red/orange badge styling, widened the trigger-condition column, and kept禁追价/重仓区/validation details in the drawer.
- Verification: `py_compile` passed for `ui/buy_zone.py` and `ui/dashboard.py`; `pytest tests/test_core_logic.py -q` passed with 246 tests and 27 subtests.
- Next needed: Review and commit this buy-zone UI checkpoint if the diff looks good. Do not start another checkpoint automatically.
- Do not touch yet: BuyZoneEngine formulas, PositionPlanEngine formulas, scoring logic, data providers, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and unrelated UI files were not changed.

2026-05-24 UI conversation:
- Owner: UI conversation.
- Task: Buy zone plan page UI-only polish into a compact execution panel.
- Files touched: `AGENT_HANDOFF.md`, `ui/buy_zone.py`.
- What changed: Reworked the buy-zone page from a backend-like table into a compact execution panel. Added `今日执行摘要`, made top status cards action-oriented, changed filters to execution groups, reduced the main table to stock/current price/status/advice/current add/position cap/next trigger/confidence/source/action, moved禁追价 and重仓区 details into the drawer, replaced low-value `0%` display with `不新增`/`等待`/`观察`/`复核`, localized raw zone/source enums with safe fallbacks, fixed next-trigger fallback copy, kept manual override and the advanced valuation sandbox folded, added a loading notice/spinner, and expanded the drawer with system/current buy-zone snapshots plus warnings and validation errors.
- Verification: `py_compile` passed for `ui/buy_zone.py` and `ui/dashboard.py`; `pytest tests/test_core_logic.py -q` passed with 246 tests and 27 subtests.
- Next needed: Review and commit this UI-only checkpoint if the diff looks good. Do not start another checkpoint automatically.
- Do not touch yet: BuyZoneEngine formulas, PositionPlanEngine formulas, scoring logic, data providers, Review Center backend, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, and unrelated UI files were not changed.

2026-05-24 UI conversation:
- Owner: UI conversation.
- Task: Combined stock detail UI checkpoint: missingDataSummary summary plus manual buy-zone trigger sync.
- Files touched: `AGENT_HANDOFF.md`, `ui/stock_detail.py`.
- What changed: Updated the stock research page data-confidence section to prefer existing `missingDataSummary`, render compact counts and four grouped missing-data summaries, keep the old fallback when `missingDataSummary` is absent, keep detailed tables folded behind `查看数据缺口明细`, and localize default-view technical terms. Also fixed manual buy-zone UI sync: the buy-zone title now reflects system/manual/mixed source, manual mode `下一触发` prefers saved `first_buy_price`, editing an operation plan shows an unsaved-change hint, and saving the plan closes edit mode with copy that the top buy-zone summary has updated.
- Verification: `py_compile` passed for `ui/stock_detail.py`; `pytest tests/test_core_logic.py -q` passed with 246 tests and 27 subtests.
- Next needed: Review and commit this UI-only checkpoint if the diff looks good. Do not start another checkpoint automatically.
- Do not touch yet: Data logic, scoring logic, missingResolutionRoute, Review Queue backend, BuyZoneEngine formulas, PositionPlanEngine formulas, database schema/migrations, Qwen/AI review, autopilot, external APIs, npm dev server, technical indicator normalization, and `ui/metric_labels.py` were not changed.

2026-05-24 Data conversation:
- Owner: Data conversation.
- Task: Missing Data Resolution / missing metric routing cleanup.
- Files touched: `AGENT_HANDOFF.md`, `scoring/sector_models.py`, `scoring/total_score.py`, `data/review_queue_builder.py`, `tests/test_core_logic.py`.
- What changed: Added `missingResolutionRoute`, default-review routing metadata, and `missingDataSummary` to metric resolution output. SaaS KPI gaps now route to IR/SEC extraction or company-not-disclosed instead of default manual review; analyst-estimate gaps stay valuation-only; auto-calculable metrics route to auto calculation; low-materiality debt maturity pressure is archived by default, while high-leverage debt pressure still requires human review. Review queue creation now skips nonblocking estimate/not-disclosed/low-priority/auto-calculate noise.
- Verification: `py_compile` passed for modified files plus `data/disclosure_store.py` and `data/providers.py`; `pytest tests/test_core_logic.py -q` passed with 246 tests and 27 subtests.
- Next needed: Review and commit this Missing Data Resolution checkpoint if the diff looks good. Do not start another checkpoint automatically.
- Do not touch yet: UI, dashboard, stock detail page, BuyZoneEngine, PositionPlanEngine, Review Center page/state machine, Qwen prompt, autopilot, scoring weights/formulas, database schema/migrations, external APIs, npm dev server, and unrelated cleanup were not changed.

2026-05-24 UI conversation:
- Owner: UI conversation.
- Task: Research memo UI copy correction.
- Files touched: `AGENT_HANDOFF.md`, `ui/stock_detail.py`.
- What changed: Corrected the research memo empty-state copy so it tells users they can click `编辑备忘录` and save to the local plan now, and changed the success toast to `研究备忘录已保存。`. Preserved the existing `stock_action_plans.notes` save path and did not alter persistence logic or schema.
- Verification: `py_compile` passed for `ui/stock_detail.py`; `pytest tests/test_core_logic.py -q` passed with 241 tests and 27 subtests.
- Next needed: Review and commit this UI-copy checkpoint if the diff looks good. Do not start another checkpoint automatically.
- Do not touch yet: Data save logic, `data/stock_plan.py`, scoring logic, BuyZoneEngine, PositionPlanEngine, Review Center backend, database schema/migrations, Qwen/AI review, external APIs, npm dev server, and unrelated UI structure were not changed.

2026-05-24 UI conversation:
- Owner: UI conversation.
- Task: Stock research memo / investment review UI-only section.
- Files touched: `AGENT_HANDOFF.md`, `ui/stock_detail.py`.
- What changed: Added a compact `研究备忘录` section after the action plan and before scoring explanation, showing investment thesis, current observation points, refutation conditions, next review trigger, and last review summary. Reused the existing `stock_action_plans.notes` path through `StockPlanStore.save_plan()` for lightweight memo editing, without adding schema or new database tables. Kept data status, auto-fill, review/source details, and raw metrics folded by default, and simplified missing-data display in scoring cards to a count summary.
- Verification: `py_compile` passed for `ui/stock_detail.py`; `pytest tests/test_core_logic.py -q` passed with 241 tests and 27 subtests.
- Next needed: Review and commit this UI-only checkpoint if the diff looks good. Do not start another checkpoint automatically.
- Do not touch yet: Data logic, scoring logic, BuyZoneEngine, PositionPlanEngine, Review Center backend, status enums, database schema/migrations, Qwen/AI review, autopilot, auto-fill pipeline, Scoring Input Gate, external APIs, long-running services, and technical-indicator normalization were not changed.

2026-05-24 UI conversation:
- Owner: UI conversation.
- Task: Individual stock research page UI-only restructure.
- Files touched: `AGENT_HANDOFF.md`, `ui/stock_detail.py`.
- What changed: Renamed the page to `个股研究`, tightened the symbol controls, moved the first screen around stock summary, current conclusion, buy-zone ladder, and position guidance, changed the action plan to a default system-summary plus edit toggle, compacted scoring explanations and SaaS/core industry metrics, and folded data status, auto-fill/review/source details, and raw metrics behind explicit expanders.
- Verification: `py_compile` passed for `ui/stock_detail.py` and `ui/dashboard.py`; `pytest tests/test_core_logic.py -q` passed with 241 tests and 27 subtests.
- Next needed: Review and commit this UI-only checkpoint if the diff looks good. Do not start another checkpoint automatically.
- Do not touch yet: Data logic, scoring logic, BuyZoneEngine, PositionPlanEngine, Review Center backend, status enums, database schema/migrations, Qwen/AI review, autopilot, Scoring Input Gate, external APIs, long-running services, and technical-indicator normalization were not changed.

2026-05-24 Data conversation:
- Owner: Data conversation.
- Task: Metric Display Mapping / unmapped field cleanup.
- Files touched: `AGENT_HANDOFF.md`, `ui/metric_labels.py`, `tests/test_core_logic.py`.
- What changed: Hardened the existing `metric_label()` resolver, added common technical/financial/industry KPI aliases, hid internal debug fields from normal labels, added debug-mode unmapped output plus an unmapped metric registry, and covered the requested mappings with tests.
- Verification: `py_compile` passed for `ui/metric_labels.py` and `tests/test_core_logic.py`; `pytest tests/test_core_logic.py -q` passed with 241 tests and 27 subtests.
- Next needed: Review and commit this label-map checkpoint if the diff looks good. Do not start another checkpoint automatically.
- Do not touch yet: Scoring logic, BuyZoneEngine formulas, PositionPlanEngine logic, Review Center state machine, Qwen/AI review, autopilot, database schema/migrations, external APIs, and long-running services were not changed.

2026-05-24 UI conversation:
- Owner: UI conversation.
- Task: Dashboard decision overview UI-only repair and polish.
- Files touched: `AGENT_HANDOFF.md`, `ui/dashboard.py`.
- What changed: Fixed StockDetailDrawer opening and closing from the main table menu, then moved menu detail/position/plan actions back to client-side drawer buttons so they open without a Streamlit rerun. Kept table/lane row clicks on the client drawer opener. Polished decision lanes into a more unified compact panel with tighter card gaps, lighter transparent rows, smaller inline footer, reduced section spacing, removed blank lane placeholders, and hid raw technical error text from the main table flow.
- Verification: `py_compile` passed for `ui/dashboard.py`; `pytest tests/test_core_logic.py -q` passed with 238 tests and 19 subtests.
- Next needed: Review and commit this UI checkpoint if the diff looks good. Do not start another checkpoint automatically.
- Do not touch yet: Scoring logic, data logic, BuyZoneEngine, PositionPlanEngine, Review Center backend, status enums, database schema, Qwen/AI review, autopilot, external APIs, scoring input gate, and buy-zone sanity code were not changed.

2026-05-24 Data conversation:
- Owner: Data conversation.
- Task: Checkpoint 3 - BuyZoneEngine sanity check.
- Files touched: `AGENT_HANDOFF.md`, `buy_zone_engine.py`, `position_plan_engine.py`, `tests/test_core_logic.py`.
- What changed: Added buy-zone estimate validation, invalid/data-insufficient zone handling, monotonic and positive-price checks, extreme price-distance warnings, confidence downgrades for low data confidence, implied FCF margin, low-confidence proxy, abnormal percent inputs, and unreviewed metric sources. Added next-trigger labels and safer position-plan first-buy logic so entered buy zones no longer wait for a higher buy price.
- Verification: `py_compile` passed for `buy_zone_engine.py` and `position_plan_engine.py`; `pytest tests/test_core_logic.py -q` passed with 238 tests and 19 subtests.
- Next needed: Review and commit Checkpoint 3 if the diff looks good. Do not start the next checkpoint automatically.
- Do not touch yet: UI, dashboard, Review Center, Qwen/AI review, autopilot, scoring input gate, schema, migrations, database, external API calls, long-running services, and technical-indicator percent normalization were not changed.

2026-05-24 UI conversation:
- Owner: UI conversation.
- Task: Checkpoint 2B - Review Undo UI / recent confirmed and undo entry.
- Files touched: `AGENT_HANDOFF.md`, `ui/manual_review.py`, `tests/test_core_logic.py`.
- What changed: Added a `最近确认` Review Center tab backed by `list_recent_confirmed_items(days=7)`, recent-confirmed rows with undo/view-impact/source/recompute actions, post-confirm undo notice, high-impact second-confirmation copy, and clearer main-row status semantics for confirmed/AI-confirmed/manual-corrected/archived items. Updated one UI semantics test to reflect the new archived-row primary label.
- Verification: `py_compile` passed for `ui/manual_review.py` and `tests/test_core_logic.py`; `pytest tests/test_core_logic.py -q` passed with 230 tests and 19 subtests.
- Next needed: Review and commit Checkpoint 2B if the diff looks good. Do not start Checkpoint 3 automatically.
- Do not touch yet: Backend review logic, scoring logic, data providers, database schema/migrations, Qwen/AI review pipeline, autopilot, BuyZoneEngine, PositionPlanEngine, cache structure, and long-running services were not changed.

2026-05-23 Data conversation:
- Owner: Data conversation.
- Task: Checkpoint 2A - Review Undo Backend Safety.
- Files touched: `AGENT_HANDOFF.md`, `data/review_queue_builder.py`, `tests/test_core_logic.py`.
- What changed: Hardened review undo actions for approved, AI auto-approved, manually corrected, auto-archived, and rejected items; undo now writes audit logs, marks the symbol score stale, prevents unsafe target statuses from re-entering scoring, restores the prior confirmed value when undoing a manual correction, and exposes `list_recent_confirmed_items(days=7)` with scoring eligibility metadata for future UI use.
- Verification: `py_compile` passed for `data/review_queue_builder.py` and `data/disclosure_store.py`; `pytest tests/test_core_logic.py -q` passed with 230 tests and 19 subtests.
- Next needed: Review and commit Checkpoint 2A if the diff looks good. Do not start Checkpoint 2B automatically.
- Do not touch yet: UI, dashboard, Review Center UI, BuyZoneEngine, Qwen/AI review, autopilot, schema, migrations, database schema, external API calls, and long-running services were not changed.

2026-05-23 Data conversation:
- Owner: Data conversation.
- Task: Checkpoint 1 - Scoring Input Gate stabilization.
- Files touched: `AGENT_HANDOFF.md`, `data/disclosure_store.py`, `data/providers.py`, `scoring/metric_sources.py`, `tests/test_core_logic.py`.
- What changed: Added the unified `canMetricEnterScoring` gate behind `_eligible_for_scoring`, made scoring supplements scoring-only by default, forced provider snapshot merges to request scoring-only disclosure data, tagged user manual overrides as explicitly scoring-allowed, and blocked raw pending/rejected/stale/historical review metadata inside scoring metric source reads.
- Verification: `py_compile` passed for `data/disclosure_store.py`, `data/providers.py`, `scoring/sector_models.py`, `scoring/metric_sources.py`; `pytest tests/test_core_logic.py -q` passed with 228 tests and 19 subtests.
- Next needed: Review and commit Checkpoint 1 if the diff looks good. Do not start Checkpoint 2 automatically.
- Do not touch yet: UI, Review Center UI, BuyZoneEngine, Qwen/AI review, autopilot, schema, migrations, database, cache cleanup, and external API calls were not changed.

2026-05-23 Data conversation:
- Owner: Data conversation.
- Task: Create `CODEX_WORKFLOW.md` with project Codex working rules.
- Files touched: `CODEX_WORKFLOW.md`, `AGENT_HANDOFF.md`.
- What changed: Added the workflow rules document and recorded that it is not a handoff file; `AGENT_HANDOFF.md` remains the only default handoff file.
- Verification: Not run; documentation-only workflow change.
- Next needed: Use `CODEX_WORKFLOW.md` as the task discipline reference and `AGENT_HANDOFF.md` as the cross-conversation handoff board.
- Do not touch yet: No business code, UI, scoring, review, buy zone, database, cache, or API logic was changed.

2026-05-23 Data conversation:
- Owner: Data conversation.
- Task: Set `AGENT_HANDOFF.md` as the single default handoff file.
- Files touched: `AGENT_HANDOFF.md`.
- What changed: Added the canonical handoff rule at the top of this file and cleared Active Work.
- Verification: Not run; documentation-only coordination change.
- Next needed: Both conversations should read and update this file before/after file edits.
- Do not touch yet: No extra handoff files should be created.

2026-05-23 UI conversation:
- Established this handoff file.
- No code files changed in this step.
- Current UI responsibility: `app.py` and `ui/`.

## Handoff Template

Date / conversation:
- Owner:
- Task:
- Files touched:
- What changed:
- Verification:
- Next needed:
- Do not touch yet:
