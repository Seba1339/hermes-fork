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

**Phase 3A (schema preparation) — done on
`feature/personal-system-memory-foundation`.** The `session_id`, `fact_type`
(default `"explicit"`), and `expires_at` columns now exist on `facts`, added
via the same additive PRAGMA-detect / `ALTER TABLE ... ADD COLUMN` pattern
already used for `hrr_vector` — nullable, backward-compatible, no change to
`add_fact`/`search_facts`/`list_facts`/`update_fact`/`remove_fact` semantics
or dedup behavior, and the new columns are not yet exposed in any returned
dict. Covered by
`tests/plugins/memory/test_holographic_schema_migration.py`. See
`IMPLEMENTATION_LOG.md` for the exact diff and verification steps.

**Still not started (Phase 3B+):** `memory_extract.py`, the
`on_session_end()`/`on_pre_compress()` wiring, the real
`agent_memory.db` → `memory_store.db` migration script and its backup/
rollback procedure, and actually populating the new columns on write. None
of this is authorized by the work above — it needs its own explicit
go-ahead per this roadmap's framing, since it touches files and live data
outside this repo.

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
  **Store-level half done (2026-08-08, see Phase 3E below):**
  `MemoryStore.update_fact_audited`/`forget_fact_audited` exist and are
  tested; exposing them as an agent-facing tool (schema + handler wiring in
  `plugins/memory/holographic/__init__.py`) is still not started.

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

## Update — 2026-08-08

Phase 2 item 4 (BuJo cron-output cleaning logic extraction) has been
implemented on `feature/personal-system-phase2`: the inline logic in
`APIServerAdapter.send()` (`gateway/platforms/api_server.py`) is now the
pure function `_clean_cron_output`, covered by
`tests/gateway/test_api_server_bujo_delivery.py`. No semantic change — see
`IMPLEMENTATION_LOG.md` for verification steps and results.

## Update — 2026-08-08 (2)

Phase 3A (schema preparation only) has been implemented on
`feature/personal-system-memory-foundation`: the `session_id`, `fact_type`,
and `expires_at` columns were added to `plugins/memory/holographic/store.py`'s
`facts` table via an additive migration, covered by
`tests/plugins/memory/test_holographic_schema_migration.py`. Fase 1's
automatic extraction (`memory_extract.py`), the `on_session_end()`/
`on_pre_compress()` wiring, and the real `agent_memory.db` →
`memory_store.db` migration script remain **not started** — see the Phase 3
section above and `IMPLEMENTATION_LOG.md` for details.

## Update — 2026-08-08 (3)

Phase 3B-preparación (provenance + filter, still not the full auto-extraction
rollout) has been implemented on
`feature/personal-system-extraction-foundation`: `MemoryStore.add_fact` gained
optional `session_id`/`fact_type`/`expires_at` keyword-only fields, and
`HolographicMemoryProvider._auto_extract_facts` now stamps every extracted
fact with `session_id`/`fact_type="extracted"` and skips messages whose
content starts with `"[IMPORTANT:"`. `auto_extract` remains `false` by
default; there is still no cron wiring and no real data has been migrated or
extracted — see `IMPLEMENTATION_LOG.md` for verification steps and results.

## Update — 2026-08-08 (4)

Phase 3C (extraction dry-run preview) has been implemented on
`feature/personal-system-extraction-dry-run`: `HolographicMemoryProvider`
gained `preview_extracted_facts()`, a pure function that reuses
`_auto_extract_facts`'s detection rules to return the list of candidate
facts a session *would* produce — no SQLite access, no `add_fact` calls, no
side effects. `auto_extract` remains `false` by default; there is still no
cron wiring and no real data has been migrated or extracted — see
`IMPLEMENTATION_LOG.md` for verification steps and results.

## Update — 2026-08-08 (5)

Phase 3D (CLI wrapper for the dry-run preview) has been implemented on
`feature/personal-system-extraction-cli`: `scripts/memory_preview.py`
exposes `preview_extracted_facts()` as a standalone command,
`python3 scripts/memory_preview.py --input transcript.json[l]
[--session-id ID]`, taking an explicit JSON array or JSONL transcript
file and printing candidate facts as JSON to stdout. It never opens
SQLite and never resolves `HERMES_HOME`; there is still no cron wiring
and no real data has been migrated or extracted. Rollback: delete the
`feature/personal-system-extraction-cli` branch — see
`IMPLEMENTATION_LOG.md` for verification steps and results.

## Update — 2026-08-08 (6)

Phase 4 (holographic prefetch verification) has been checked on
`feature/personal-system-prefetch-verification`: temporal prefetch,
`min_trust` filtering, and fail-closed behavior for the holographic
memory provider were verified with **7/7** passing tests
(`tests/plugins/memory/test_holographic_prefetch.py`), and
`MemoryManager` skill scaffolding was verified with **14/14** passing
tests (`tests/agent/test_memory_skill_scaffolding.py`). This is a
test-only verification pass: no production code, configuration, or
cron wiring was changed, and no real data was migrated or extracted —
see `IMPLEMENTATION_LOG.md` for details.

## Update — 2026-08-08 (7)

Phase 5A (offline migration tool) has been implemented on
`feature/personal-system-memory-migration-tool`: `scripts/memory_migrate.py`
migrates legacy `agent_memory` rows into a `MemoryStore`-compatible `facts`
table, mapping `fact`->`content`, `category`->`category`,
`confidence`->`trust_score`, `source`->`fact_type`, and `expires_at`
verbatim. Dry-run is the default and writes nothing; `--apply` requires an
explicit `--backup-dir`, and byte-for-byte timestamped backups of
`--source`/`--target` are taken before any write, giving rollback by
restoring those backups. Real Hermes paths (`~/.hermes`,
`~/.hermes-enhanced`, or files named `agent_memory.db`, `memory_store.db`,
`state.db`, `bujo.sqlite`) are refused unless `--allow-real-paths` (and,
for `--apply`, `--confirm-real-migration`) is passed. Verified with
**18/18** passing tests (`tests/scripts/test_memory_migrate.py`); no real
data has been migrated — see `IMPLEMENTATION_LOG.md` for details.

## Update — 2026-08-08 (8)

Phase 5A's tool was tested offline first (18/18 passing tests, no real
data touched — see Update 7 above) and was then applied exactly once
against real Hermes paths with `--allow-real-paths
--confirm-real-migration`, per its explicit real-path guard. `state.db`
and `bujo.sqlite` were not touched by this run — the tool only reads
`agent_memory.db` and writes `memory_store.db`. Backups were taken before
any write; see `IMPLEMENTATION_LOG.md` Entry 9 for the backup path, row
counts, and post-migration dry-run verification.

## Update — 2026-08-08 (9)

Clarifying note, not a correction of fact: Updates (2) through (7) above
each state "`auto_extract` remains `false` by default" and/or "no real
data has been migrated or extracted." Those sentences describe the state
*at the time each of those entries was written* (Phase 3A through 5A's
offline-tool verification, all still-unapplied at that point). They are
left unedited per this document's history — see Update (8) immediately
above and `IMPLEMENTATION_LOG.md` Entry 9, which already record that
Phase 5A's migration was subsequently applied for real, once, against
the real Hermes paths, with backups taken first. `auto_extract` itself
remains `false` by default independent of that migration — the two are
separate switches (one governs automatic extraction from live sessions;
the other was a one-time, manually-invoked backfill of pre-existing
legacy rows) and neither this note nor Update (8) changes that default.

## Update — 2026-08-08 (10)

Read-only audit reader for `fact_governance_audit` (Entry 10's governance
table) implemented on `feature/personal-system-memory-audit-readonly`:
`scripts/memory_audit.py`. `--db PATH` is required and is the only database
this process ever opens — it is never inferred from `HERMES_HOME`. The
connection is opened via SQLite's `mode=ro&immutable=1` URI flags, so writes
are rejected at the SQLite level (not just by convention) and, because
`MemoryStore` runs in WAL mode, `immutable=1` also means no `-wal`/`-shm`
sidecar files are ever opened or created by this CLI. Supports `--fact-id`
(filter to one fact) and `--limit` (default 50, must stay positive) over
rows ordered `audit_id DESC`. Real Hermes paths are refused by the same
`is_guarded_path` guard `memory_migrate.py` uses (`~/.hermes`,
`~/.hermes-enhanced`, or files named `agent_memory.db`/`memory_store.db`/
`state.db`/`bujo.sqlite`) unless `--allow-real-paths` — that flag only lifts
the path check, it never makes the connection writable. Because
`immutable=1` skips WAL/locking checks entirely, this CLI will not see rows
still sitting in an un-checkpointed `-wal` file: point `--db` at a stable
snapshot/backup, or run `PRAGMA wal_checkpoint(TRUNCATE);` against the live
database first if the very latest audit rows are needed. Verified with
**24/24** passing tests (`tests/scripts/test_memory_audit.py`); no real data
was read or touched — see `IMPLEMENTATION_LOG.md` for details.

## Update — 2026-08-08 (11)

Governance *write* CLI `scripts/memory_governance.py`, companion to
Update (10)'s read-only `memory_audit.py`, wraps
`MemoryStore.update_fact_audited`/`forget_fact_audited` for `--action
update`/`--action forget` against a single `--fact-id`. Preview (dry-run)
is the default and stays immutable: it never imports or constructs
`MemoryStore`, never resolves `HERMES_HOME`, and never takes a backup — it
only opens `--db` read-only (`mode=ro&immutable=1`) to print a JSON diff
of what `--apply` would do. `--reason` is mandatory for both actions and
is recorded in `fact_governance_audit`; `--action forget` additionally
requires `--confirm-forget`. `--apply` requires an explicit `--backup-dir`
and takes a byte-for-byte, timestamped backup of `--db` before any write —
if the backup fails, nothing is written. The same `is_guarded_path` guard
`memory_migrate.py`/`memory_audit.py` use refuses real Hermes paths
(`~/.hermes`, `~/.hermes-enhanced`, or files named `agent_memory.db`,
`memory_store.db`, `state.db`, `bujo.sqlite`) unless `--allow-real-paths`,
and `--apply` against such a path additionally requires
`--confirm-real-governance`. Verified with **36/36** passing tests
(`tests/scripts/test_memory_governance.py`; 81/81 across the audit and
governance suites combined); no real data has been touched — see
`IMPLEMENTATION_LOG.md` for details.
