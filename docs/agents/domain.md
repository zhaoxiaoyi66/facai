# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Layout

This repo uses a single-context layout with domain docs under `docs/`.

Read these files when they exist and are relevant to the current task:

- `docs/CONTEXT.md` for shared project vocabulary, domain terms, and constraints.
- `docs/adr/` for architectural decision records.
- `docs/agents/` for agent workflow configuration.

If these files do not exist yet, proceed silently. Do not create new domain docs unless the user asks or a documentation skill is explicitly run.

## Use the glossary's vocabulary

When output names a domain concept, use the term as defined in `docs/CONTEXT.md`. Avoid inventing synonyms for concepts that already have a project term.

If the concept is not in the glossary yet, note it as a possible documentation gap for a future docs-focused task.

## Flag ADR conflicts

If output contradicts an existing ADR, surface it explicitly instead of silently overriding the decision.
