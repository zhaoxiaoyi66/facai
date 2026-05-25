# Codex Workflow

This file records the project working rules for Codex. It is a workflow note, not a handoff file. The only default handoff file is `AGENT_HANDOFF.md`.

## Task Boundaries

1. Change at most 5 files per task.
2. Solve only one clearly defined problem per task.
3. Do not mix UI, scoring, database, AI, and buy-zone engine changes in one checkpoint.
4. Do not change schema, migrations, or SQLite without explicit confirmation.
5. Do not automatically move to the next phase.

## Command Limits

1. Do not run long-lived services such as `npm run dev`, `streamlit run`, `flask run`, `uvicorn`, or `python app.py`.
2. Do not call external APIs unless the task explicitly asks for it.
3. Do not install dependencies unless explicitly approved.
4. Prefer short verification commands:
   - `python -m py_compile <file>`
   - `pytest <specific test file>`
   - `npm run build` only for final verification when backend/frontend build is involved.

## Git Command Workflow

Avoid routine git prechecks because the Codex app can show completed git commands as still running and make the session appear stuck.

1. Do not run `git status` or `git diff` at the start of every task.
2. Start by reading the target files for the explicit task.
3. Run git commands only when the user explicitly asks, when preparing a commit, when diagnosing a dirty/conflicting worktree, or when a final diff summary is requested.
4. For normal completion summaries, run at most one `git diff --stat` if a diff is needed.
5. Do not run multiple git/status/diff commands in parallel.
6. During commit flow only, use sequential git commands: `git diff --stat`, `git status --short`, `git add`, `git commit`, then `git status --short`.
7. If the app UI appears stuck on a git command that has already returned, report the last visible result instead of re-running the same command.
8. Never run git/status/diff/test commands in parallel. Run one command, report or decide, then run the next command.

## Timeout Rules

1. If a command has no output for 120 seconds, stop and report.
2. If a task runs for more than 5 minutes without a progress update, stop and report.
3. If a test or build is expected to take more than 5 minutes, explain before running it.
4. If any task has no clear progress for more than 10 minutes, stop instead of waiting.
5. Avoid long silent searches or background work.

## Checkpoint Start

Before every checkpoint, run:

```powershell
git status --short
git diff --stat
```

If the workspace is not clean:

1. Stop if the changes are unrelated to the current task or include backend/data/scoring/database files.
2. Report the uncommitted or untracked changes.
3. Do not continue development until the user decides what to do.
4. For UI-only work, do not stop merely because the working tree contains uncommitted changes if all changes are within the same active UI checkpoint.

## UI Page Checkpoints

UI-only changes may be grouped by page or section into one checkpoint. Do not stop merely because the working tree contains uncommitted changes if all changes are within the same active UI checkpoint. Stop only if backend/data/scoring/database files are modified or if the task scope changes.

Examples of page-level UI checkpoints:

1. Dashboard decision overview UI.
2. Stock research page UI.
3. Buy-zone plan page UI.
4. Manual review center UI.

Within the same page or section, multiple small UI refinements may be completed and validated together before one commit.

Stop and report immediately if any of these files or areas are modified during a UI checkpoint:

1. `data/`
2. `scoring/`
3. `buy_zone_engine.py`
4. `position_plan_engine.py`
5. `review_queue_builder.py`
6. Database schema or migrations.
7. Qwen, AI review, or autopilot backend files.

UI checkpoints must still avoid:

1. Changing scoring logic.
2. Changing data logic.
3. Changing BuyZoneEngine logic.
4. Changing PositionPlanEngine logic.
5. Changing database schema.
6. Calling external APIs.
7. Running `npm run dev`.

## Checkpoint Completion

After every checkpoint, report:

1. Modified files.
2. Whether the change was UI-only.
3. The page or section for UI checkpoints.
4. `git diff --stat`.
5. Tests or checks run.
6. Whether tests passed.
7. Whether the database was affected.
8. Whether scoring was affected.
9. Whether UI was affected.
10. Whether the change can be rolled back.
11. Suggested commit message or next step.

## Communication Rules

1. If a search needs many files, list the plan first.
2. If multiple modules are involved, split the work into separate checkpoints.
3. If the issue is more complex than expected, stop and report instead of forcing a large change.
4. Before editing files, update `AGENT_HANDOFF.md` Active Work.
5. After editing files, write the result in `AGENT_HANDOFF.md` Latest Handoff.

## Command Feedback Rules

Avoid making the session appear stuck after a command has already returned.

1. After every command returns, first report the result in one short sentence before deciding the next step.
2. Do not let a completed command be followed by a long silent reasoning pause.
3. If another command is needed, say what will run next and why before running it.
4. After a test command returns, immediately report pass/fail counts and the failing test name or file if any.
5. If no tool is running but reasoning takes more than about 30 seconds, send a short status update.
6. For multi-step tasks, report progress between steps instead of saving all feedback for the final summary.
7. If a command has completed but the turn feels stuck, stop further整理/analysis and give only a status report.

## Test Feedback Rule

Test output must be reported before any further analysis or follow-up command.

1. When a test command returns, the next assistant message must start with the fixed format: `测试结果：通过/失败，<count summary>，<duration if visible>.`
2. The same message must include `下一步：...`.
3. Do not silently inspect, reason, or run another command before sending that test result message.
4. If a test fails, name the failing test or file in the result message before proposing a fix.
5. This rule exists because tests often finish successfully while the app still shows the assistant as thinking, which feels like a stuck session.

## Short Completion Report

After a normal task finishes, keep the final report to at most five lines:

1. Modified files.
2. What changed.
3. Test/check result.
4. `git diff --stat`.
5. Whether commit is recommended.

Do not write long summaries after a passing test. UI-only small fixes do not update `AGENT_HANDOFF.md`. Default is no commit unless the user explicitly asks.
