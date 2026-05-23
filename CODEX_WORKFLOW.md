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

1. Stop.
2. Report the uncommitted or untracked changes.
3. Do not continue development until the user decides what to do.

## Checkpoint Completion

After every checkpoint, report:

1. Modified files.
2. `git diff --stat`.
3. Tests or checks run.
4. Whether tests passed.
5. Whether the database was affected.
6. Whether scoring was affected.
7. Whether UI was affected.
8. Whether the change can be rolled back.
9. Suggested next step.

## Communication Rules

1. If a search needs many files, list the plan first.
2. If multiple modules are involved, split the work into separate checkpoints.
3. If the issue is more complex than expected, stop and report instead of forcing a large change.
4. Before editing files, update `AGENT_HANDOFF.md` Active Work.
5. After editing files, write the result in `AGENT_HANDOFF.md` Latest Handoff.
