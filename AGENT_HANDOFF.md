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

No active work recorded.

## Latest Handoff

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
