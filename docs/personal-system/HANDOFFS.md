# Persistent work handoffs

A handoff is explicit, mutable work-in-progress state for a job or project
that another session or agent may resume. It is stored in the
`memory_handoffs` table and is deliberately separate from `facts` and BuJo.

## Fields

- `handoff_id`: stable identifier;
- `title`: short work title;
- `status`: `open`, `in_progress`, `blocked`, `done`, or `abandoned`;
- `summary`: current state;
- `next_steps`: what the next agent should do;
- `blockers`: known blockers;
- `owner`: optional responsible person/agent;
- `session_id`: session that created or originated the handoff;
- `created_at` / `updated_at`.

Handoffs are not deduplicated by title. The stable `handoff_id` identifies one
piece of work, and updates preserve that identifier.

## Agent tool

The holographic provider exposes `memory_handoff` with these actions:

- `handoff_create` — create explicit resumable state;
- `handoff_get` — retrieve one by ID;
- `handoff_list` — filter by status, session, or owner;
- `handoff_update` — update the work state.

The provider automatically stamps the current session when `session_id` is
not supplied. Invalid statuses and empty titles are rejected.

## Boundaries

This feature does **not** create or modify BuJo entries, tasks, events,
reminders, cron jobs, or facts. It is a continuity record only. A user must
explicitly decide whether a next step should also become a BuJo/task entry.

All tests use temporary SQLite databases. The feature does not migrate or
modify the active database as part of implementation.
