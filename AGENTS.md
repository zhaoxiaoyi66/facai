## Agent skills

### Issue tracker

Issues are tracked as local markdown files under `.scratch/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the default mattpocock/skills triage vocabulary. See `docs/agents/triage-labels.md`.

### Domain docs

This repo uses a single-context domain docs layout under `docs/`. See `docs/agents/domain.md`.

### Testing policy

Use targeted tests by default instead of full regression for every task. See `docs/testing_policy.md` and `scripts/select_tests.py`.
For command cadence and reporting, use `CODEX_WORKFLOW.md`; docs-only/read-only workflow tasks usually need no tests.
