# Codex Workflow

This file records lightweight working rules for this repo. It is a workflow note,
not a handoff file. Use `AGENT_HANDOFF.md` only for explicit handoff/resume work
or long-running multi-session work.

## Task Boundaries

1. Prefer small, coherent changes. More than 5 files is acceptable for docs, tests, or one tightly scoped UI/page workflow.
2. Solve one clearly defined problem per task.
3. Do not mix UI, scoring, database, AI, and buy-zone engine changes unless the user explicitly asks for a broad refactor.
4. Do not change schema, migrations, or SQLite without explicit confirmation.
5. Do not automatically move to the next phase.

## Command Limits

1. Do not run long-lived services such as `npm run dev`, `streamlit run`, `flask run`, `uvicorn`, or `python app.py` unless explicitly needed.
2. Do not call external APIs unless the task explicitly asks for it.
3. Do not install dependencies unless explicitly approved.
4. Prefer short verification commands:
   - `python -m py_compile <file>`
   - `python -m pytest <specific test file> -q`
   - `scripts/select_tests.py <changed files>`

## Git Workflow

Avoid routine git commands because they add latency and can make the desktop app
look stuck.

1. Do not run `git status` or `git diff` at the start of every task.
2. Run `git status --short` only when:
   - The user explicitly asks to confirm a clean worktree.
   - Preparing to edit high-risk files under `data/`, `scoring/`, ledger/sync modules, or database schema.
   - Preparing a commit.
   - Unexpected file changes appear.
3. Run `git diff --stat` only when the user requested it, before commit, or when scope review is useful.
4. During commit flow, use sequential commands: `git status --short`, `git add`, `git commit`, then `git status --short`.
5. In this workspace, `git add` and `git commit` usually need elevated permission because writing `.git/index` is blocked by the sandbox.
6. If `.git/index.lock` permission fails, rerun the same git command with elevated permission instead of diagnosing the expected failure.

## Test Policy

Use `docs/testing_policy.md` and `scripts/select_tests.py`.

1. Docs-only or read-only audits: no tests.
2. Script/test mapping changes: run only the script's own tests.
3. UI/copy/CSS changes: run selected UI/helper tests, not full core regression.
4. Advisory display modules such as structure or pullback acceptance: run the module test only unless entry/sync/journal code changed.
5. Buy/sell/ledger logic changes: run the trading workflow profile.
6. Radar scoring or buy-zone changes: run Radar, entry display, and core scoring tests.
7. Macro provider/regime changes: run macro tests plus the small core macro slice.
8. Release candidates, phase freezes, and major refactors: run full core regression.

## Timeout Rules

1. If a command has no output for 120 seconds, stop and report.
2. If a test or build is expected to take more than 5 minutes, explain before running it.
3. Avoid long silent searches or background work.
4. If a task has no clear progress for more than 10 minutes, stop and report the current state.

## UI Page Checkpoints

UI-only changes may be grouped by page or section into one checkpoint.

Examples:

1. Dashboard decision overview UI.
2. Stock research page UI.
3. Buy-zone plan page UI.
4. Manual review center UI.

Stop and report if a UI checkpoint unexpectedly modifies:

1. `data/`
2. `scoring/`
3. `buy_zone_engine.py`
4. `position_plan_engine.py`
5. `review_queue_builder.py`
6. Database schema or migrations.
7. Qwen, AI review, or autopilot backend files.

## Communication

1. Give short progress updates for multi-step work.
2. After a test command returns, report pass/fail count and duration if visible before another long command.
3. If tests fail, name the failing file or test before proposing a fix.
4. For sub-second docs/script checks, it is fine to fold the result into the next update.
5. Avoid dumping full command output unless the user asked for it or a failure needs context.

## Completion Report

Keep normal completion reports compact:

1. What changed.
2. Test profile/checks run.
3. Whether business logic, database, scoring, gates, or sync were touched.
4. `git diff --stat` only when useful or requested.
5. Commit hash if a commit was made.
