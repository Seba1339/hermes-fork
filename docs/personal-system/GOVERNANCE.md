# Agent-facing memory governance

`memory_governance` is the only provider tool exposed to the agent for
changing or deleting an existing fact.

## Update

```json
{
  "action": "update",
  "fact_id": 42,
  "reason": "The user corrected the stored preference",
  "content": "...",
  "category": "user_pref",
  "trust_score": 0.9
}
```

Only `content`, `category`, and absolute `trust_score` are mutable through
this tool. The reason is mandatory and the mutation plus its audit row are
written atomically.

## Forget

```json
{
  "action": "forget",
  "fact_id": 42,
  "reason": "The user asked to remove this obsolete fact",
  "confirm_forget": true
}
```

Forgetting requires `confirm_forget: true`. Without it, nothing is read or
written. The operation is recorded in `fact_governance_audit` before the fact
is removed.

## Boundary with `fact_store`

`fact_store` still handles add/search/probe/related/reason/contradict/list.
Its old agent-facing `update` and `remove` actions are no longer exposed and
are rejected if called directly. Internal store methods remain available to
migration and maintenance code, but they are not agent-facing routes.

All provider operations are tested against temporary SQLite databases. This
feature does not modify the active memory database or automatically create
BuJo entries, tasks, or reminders.
