# Roadmap — Personal System (Agenda / BuJo / Memory / Collaboration)

> **Audience:** Whoever picks up Phase 2+.
> **Relationship to `memoria_activa_architecture.md`:** that document (repo
> root) is the detailed technical proposal for the memory system
> specifically (5 layers, cost estimates, per-file complexity/time
> estimates). This roadmap does not repeat those numbers — it sequences
> that proposal alongside the other gaps found during the Phase 1 audit
> (`ARCHITECTURE.md` §8) and the framework benchmark
> (`EXTERNAL_AGENT_PATTERNS.md`), and states what each phase is allowed to
> touch.
> **Last updated:** 2026-08-08

## How to read this roadmap

Each phase lists: what it delivers, which files it's allowed to touch,
what must stay unchanged (the invariants from `AGENTS.md` and
`IMPLEMENTATION_LOG.md`), and what a human must decide before it starts.
No phase after Phase 1 is authorized to begin by this document alone —
each needs an explicit go-ahead, because every one of them either changes
live gateway behavior or touches real personal data outside this repo.

## Phase 1 — Foundation (this phase, complete)

**Delivered:** `docs/personal-system/` (this directory), invariant tests
for `perspective_router`, `perspective_quota`, `hermes_enhanced.skill_router`,
and the BuJo opt-in delivery path. No behavior changes; see
`IMPLEMENTATION_LOG.md` for the exact diff and verification steps.

**Explicitly deferred, not started:** every item below.

## Phase 2 — Decisions and small in-repo fixes (needs user sign-off per item)

These are the concrete findings from `ARCHITECTURE.md` §8 that have a
correct owner (the user) rather than an obvious automatic answer. Each is
independent — doing one does not require doing the others.

1. **`hermes_enhanced/__init__.py` dead code.** `critic_evaluate()` and
   `estimate_task_complexity()` are fully implemented but called by nothing
   (confirmed by repo-wide grep). Decision needed: restore as a
   `post_llm_call` plugin hook (see item 2 below for the pattern), or
   delete. Low risk either way — it's currently inert.
2. **Migrate `enhanced_init.py`'s `AIAgent.run_conversation` monkey-patch to
   a `pre_llm_call` plugin hook.** Removes the direct core-class patch in
   favor of the supported extension point AGENTS.md documents for exactly
   this ("General plugins" hooks: `pre_tool_call`, `post_tool_call`,
   `pre_llm_call`, `post_llm_call`, `on_session_start`, `on_session_end`).
   **Requires:** restarting `hermes-enhanced-gateway.service` (or the
   user-level `hermes-gateway-enhanced.service`) and confirming skill
   injection still fires on a real message — this repo's test suite alone
   cannot verify a systemd-managed live gateway. Not something this phase
   or an automated agent should do without the user present to watch the
   restart.
3. **`hermes-enhanced-bridge.service` targets a deleted module.** Decision
   needed: restore `hermes_enhanced/server.py` (recoverable via
   `git show e8a0feeef~1:hermes_enhanced/server.py`) if the bridge is still
   wanted, or retire the systemd unit. Systemd unit files live outside this
   repo (`/etc/systemd/system/`) — out of scope for this repo's commits
   regardless of the decision; only the Python module restoration (if
   chosen) would land here.
4. **BuJo cron-output cleaning logic extraction.** Extract the inline
   noise-stripping/truncation logic in `APIServerAdapter.send()`
   (`gateway/platforms/api_server.py`) into a pure function
   (e.g. `_clean_cron_output(content: str) -> str | None`) for direct unit
   testing of edge cases (unicode truncation boundaries, empty input).
   Pure refactor, no behavior change — lowest-risk item in this phase, but
   still touches a live gateway file, so it's sequenced after Phase 1
   rather than folded into it.

## Phase 3 — Memory system, Fase 0-1 (per `memoria_activa_architecture.md`)

Corresponds to that document's "Fase 0: Preparación" and "Fase 1:
Extracción Automática" (P0 — critical, the enabler for everything else).
**All of it lives outside this repo**: `~/.hermes/scripts/memory_extract.py`
(new), the `agent_memory.db` → `memory_store.db` migration script, and the
new `session_id`/`fact_type`/`expires_at` fields on
`plugins/memory/holographic/store.py`'s `facts` table — the last of those
is the only piece that touches this repo. Before touching `store.py`:

- Confirm the existing holographic test suite
  (`tests/plugins/memory/test_holographic_*.py`) covers schema migrations,
  or add a migration test first.
- The migration script itself (real `agent_memory.db` → real
  `memory_store.db`) must run against a backup, never the live databases
  directly — per the proposal's own "Riesgos y Mitigaciones" table
  ("Backup de las 3 DBs antes de migrar. Script de rollback").

## Phase 4 — Proactive retrieval (Fase 2, P1)

`prefetch()` in `plugins/memory/holographic/__init__.py`, per the
proposal. In-repo change. Must be verified against the cache-rate
invariant explicitly called out in the proposal's risk table ("Cache rate
cae por contenido dinámico en prefetch") — i.e. confirm the injected
memory context stays inside the existing `<memory-context>` fencing so the
system-prompt prefix is unaffected, per `AGENTS.md`'s prompt-caching rule.
This is the single highest-value, lowest-risk memory-system change and
should be prioritized over Phase 5/6 once Phase 3's storage groundwork is
in place.

## Phase 5 — Cross-domain correlation (Fase 4, P2)

`cross_session_patterns.py`, `cross_salud_bujo.py` (new, outside this
repo) plus extensions to the existing `cross_salud_finanzas.py` /
`bujo_cross_insights.py`. Per `EXTERNAL_AGENT_PATTERNS.md`'s LangGraph
note: if this grows past a handful of independent cron scripts, structure
it as one explicit state machine (plain Python — not the LangGraph
library, which would violate `AGENTS.md`'s narrow-core / no-third-party-
engine-in-core policy) rather than accreting more standalone scripts with
duplicated correlation logic.

## Phase 6 — Lifecycle / scalability (Fase 5, P3)

`memory_lifecycle.py`, `memory_weekly_rollup.py`, `memory_monthly_archive.py`
(new, outside this repo), per the proposal. Lowest priority — depends on
Phases 3-4 producing data worth managing.

## Cross-cutting, opportunistic (do alongside whichever phase touches the relevant file)

- **`agent_eval.py` / `perspective_router` category alignment**
  (`EXTERNAL_AGENT_PATTERNS.md`, OpenHands section). Have
  `perspective_router`'s response surface the matching `agent_eval` task
  category directly, so a caller doesn't hand-map `_ROUTE_RULES` categories
  to `VALID_TASKS` categories. In-repo change (`tools/perspective_router.py`
  only) — small enough to bundle into Phase 2 if a maintainer is already in
  that file.
- **Letta-style `fact_update`/`fact_forget` tool.** A narrow addition to
  the `memory` toolset for the agent to demote/correct a fact it now knows
  is stale, rather than only ever adding new facts. Natural fit once
  Phase 3's `fact_type` field exists (an update needs somewhere to record
  *why* trust changed). Sequence after Phase 3, alongside or after Phase 4.

## What this roadmap explicitly does not authorize

- Replacing any part of Hermes' core loop with LangGraph, the OpenAI
  Agents SDK, Google ADK, or any other orchestration framework — see
  `EXTERNAL_AGENT_PATTERNS.md`'s verdicts; none of them cleared the bar for
  adoption, only for borrowing a pattern.
- Any change to `~/.hermes`, `~/.hermes-enhanced`, systemd unit files, or
  `.env` as part of *this repo's* commits. Phases above that require such
  changes call it out explicitly as out-of-repo work for the user to do
  directly, separately from any PR against this repo.
- Restarting or reloading any live service. Every phase above that
  requires a restart to verify says so explicitly and defers it to the
  user.
