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

## Active Work

None.

## Latest Handoff

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
