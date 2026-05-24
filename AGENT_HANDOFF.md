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
