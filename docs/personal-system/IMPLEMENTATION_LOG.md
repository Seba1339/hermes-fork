# Implementation Log — Personal System Foundation

> Append-only. One entry per phase/session. Do not edit past entries except
> to fix a factual error (note the correction inline, don't silently rewrite
> history).

---

## Entry 1 — 2026-08-08 — Phase 1: Foundation (docs + invariant tests)

**Branch:** `feature/personal-system-foundation`
**Base commit:** `0243d78b3d4127da116e970a118707f087196266` (`main`, "auto: backup 2026-08-04 18:00")
**Author:** Claude (agent), directed by Sebastián Alvarez

### Scope

Authorized scope: build a low-risk, verifiable foundation for the personal
Agenda/BuJo/memory/collaboration system — documentation, an implementation
log, and behavior tests for existing, already-shipped invariants. No core
refactor, no new framework, no behavior change to the live agent.

### What was inspected (read-only, no changes)

- Full `AGENTS.md` (1412 lines) — prompt-caching rules, footprint ladder,
  testing standards, plugin rules.
- `git log`, `git status`, `git remote -v`, branch list (local + `origin/*`
  + `upstream/*`).
- Live systemd state (`systemctl list-units`, `systemctl status`,
  `journalctl`) for every `hermes*` unit, both system-level
  (`/etc/systemd/system/`) and user-level (`~/.config/systemd/user/`) —
  **read-only**, nothing started/stopped/reloaded/edited.
- `hermes_enhanced/` package contents, including stale `__pycache__/*.pyc`
  for modules deleted from source.
- `enhanced_init.py`, `tools/perspective_router.py`,
  `tools/perspective_quota.py`, `tools/claude_perspective_tool.py`,
  `gateway/platforms/api_server.py` (BuJo delivery section),
  `hermes_enhanced/skill_router.py`.
- `memoria_activa_architecture.md` (pre-existing, repo root, 509 lines) —
  the "Memoria Activa" proposal, treated as source of truth for the memory
  system design rather than re-derived.
- `git log -p -S` on the 2026-07-15 purge/restore commits (`e8a0feeef`,
  `b49b378aa`) to understand exactly what was deleted vs. restored.
- File listing (names only) of `~/.hermes/scripts/` and the first ~40
  lines of `~/.hermes/scripts/agent_eval.py` (docstring + constants only —
  no personal data read or copied into the repo).
- Existing test coverage map for the touched area
  (`tests/plugins/memory/test_holographic_*.py`,
  `tests/gateway/test_api_server*.py`) to confirm gaps before adding tests.

Full findings: `docs/personal-system/ARCHITECTURE.md`.

### Files added

| File | Purpose |
|---|---|
| `docs/personal-system/README.md` | Index and ground rules for this directory. |
| `docs/personal-system/ARCHITECTURE.md` | Verified map of what's live (3 "Enhanced" services, `hermes_enhanced/` package inventory, model/perspective routing, BuJo delivery, cron, memory landscape) + audit findings. |
| `docs/personal-system/EXTERNAL_AGENT_PATTERNS.md` | Per-framework benchmark (Letta, LangGraph, Goose, OpenAI Agents SDK, Google ADK, OpenHands, Browser Use, OpenClaw) against concrete gaps found in this repo. |
| `docs/personal-system/ROADMAP.md` | Phased plan (Phase 2 decisions, Phase 3-6 memory system sequencing per `memoria_activa_architecture.md`, cross-cutting items). Explicitly states what is NOT authorized (framework adoption, live restarts, out-of-repo edits). |
| `docs/personal-system/IMPLEMENTATION_LOG.md` | This file. |
| `tests/tools/test_perspective_router.py` | 11 tests: `_classify()` rule ordering/precedence, `_handle_perspective_router()` validation, risk escalation, dedup, output contract stability. |
| `tests/tools/test_perspective_quota.py` | 10 tests: default limits, per-session/per-hour/per-perspective quota enforcement, zero-means-unlimited, SQLite ledger persistence. |
| `tests/hermes_enhanced/__init__.py` | Empty — makes the directory an importable test package (matches `tests/tools/__init__.py` convention). |
| `tests/hermes_enhanced/test_skill_router.py` | 13 tests: `classify()`/`auto_load()` determinism, HIGH_PRIORITY cap (≤2), max_skills cap, dedup, frontmatter trigger loading/merging, malformed-frontmatter resilience. |
| `tests/gateway/test_api_server_bujo_delivery.py` | 7 tests: cron output is a no-op by default, explicit `bujo_write=True` persists exactly one cleaned entry, all-noise output is skipped, missing DB fails cleanly, section override, and — the invariant the task brief called out by name — repeated calls append rather than duplicate/overwrite. |

**No existing file was modified.** No file outside `docs/personal-system/`
and `tests/` was touched. No file outside this repository was read for
anything beyond identifying what exists (`ls`, `systemctl status`,
docstring-only peek) — no personal data (health, finance, real BuJo
content) was copied into any committed file.

### Commands executed (all against a temp `HERMES_HOME`, real data never touched)

```bash
git checkout -b feature/personal-system-foundation      # from 0243d78b3 (main)
bash scripts/run_tests.sh tests/tools/test_perspective_router.py -q
bash scripts/run_tests.sh tests/tools/test_perspective_quota.py -q
bash scripts/run_tests.sh tests/hermes_enhanced/test_skill_router.py -q
bash scripts/run_tests.sh tests/gateway/test_api_server_bujo_delivery.py -q
bash scripts/run_tests.sh tests/tools/test_perspective_router.py \
    tests/tools/test_perspective_quota.py \
    tests/hermes_enhanced/test_skill_router.py \
    tests/gateway/test_api_server_bujo_delivery.py -q
python3 -m py_compile <every new test file>              # syntax check
bash scripts/run_tests.sh tests/gateway/test_api_server.py tests/plugins/memory/ tests/tools/ -q
```

`scripts/run_tests.sh` is this repo's hermetic wrapper (per `AGENTS.md`):
unsets credential env vars, forces `TZ=UTC`/`LANG=C.UTF-8`, and every test
file gets its own subprocess with `HERMES_HOME` redirected to a per-test
tempdir via the `_isolate_hermes_home` autouse fixture in
`tests/conftest.py`. New tests that needed a custom skills directory
(`hermes_enhanced.skill_router`'s module-level `SKILLS_DIR` constant, which
resolves at import time rather than reading the env var lazily) used
explicit `monkeypatch.setattr` instead of relying on env-var isolation —
noted in the test file's own docstring so a future reader doesn't assume
the env var alone is enough.

### Results

- **New tests: 41/41 passed** (11 + 10 + 13 + 7), individually and run
  together in one invocation.
- **Syntax check:** all 5 new/modified Python files compile cleanly.
- **Broader regression check** (`tests/gateway/test_api_server.py`,
  `tests/plugins/memory/`, `tests/tools/` — chosen because they're the
  suites nearest the files this phase reads/reasons about, not because
  this phase modified any of them):
  - `tests/plugins/memory/` — **460/460 passed**, 0 failed. No regression.
  - `tests/gateway/test_api_server.py` — **195/196 passed, 1 pre-existing
    failure**, unrelated to this phase:
    `TestSendMethod::test_send_returns_not_supported` asserts
    `adapter.send("chat1", "hello")` (no metadata) returns
    `result.success is False`, but current `APIServerAdapter.send()`
    returns `SendResult(success=True)` for the no-`bujo_write` path (the
    intentional "skip implicit BuJo capture" no-op documented in
    `ARCHITECTURE.md` §5). **Verified pre-existing and unrelated to this
    branch:** `git diff main --stat -- gateway/platforms/api_server.py`
    is empty and `git log -1 -- gateway/platforms/api_server.py` resolves
    to `0243d78b3` — this branch's own base commit — so the file is
    byte-identical to `main`; the test fails identically there. Read
    literally, this looks like a stale test written before the BuJo
    delivery feature was added to `send()` (the class-level comment above
    `APIServerAdapter` still calls `send()` "a no-op stub", which stopped
    being fully true once BuJo delivery was added). **Not fixed in this
    phase** — `test_api_server.py` is a core gateway test file, outside
    this phase's authorized scope (docs + new tests only, no core
    behavior/test changes). Flagged here and in the final summary for a
    maintainer decision; see `ROADMAP.md`.
  - `tests/tools/test_search_error_guard.py` — 4 failures, all
    `ripgrep`-version-dependent (the installed `rg` rejects a literal
    `\n` in a pattern with a message this test suite doesn't expect).
    Pre-existing, environment-dependent, unrelated to this phase.
  - `tests/tools/test_process_registry.py` — 1 failure
    (`TestStdinHelpers::test_close_stdin_allows_eof_driven_process_to_finish`),
    caused by `ptyprocess not installed, falling back to pipe mode`
    (logged warning in the failure output) — an environment/dependency
    gap, pre-existing, unrelated to this phase.
  - None of the four failing test files were touched by this phase, and
    none exercise `hermes_enhanced/`, `perspective_router`,
    `perspective_quota`, or the BuJo delivery path this phase added tests
    for.

### Risks

- **Documentation accuracy risk:** `ARCHITECTURE.md` states live systemd
  state as observed on 2026-08-08. If services are restarted, reconfigured,
  or the purge-affected modules are restored/retired before this is read
  again, the "State" column will be stale. Mitigated by citing the exact
  verification method (systemctl/journalctl output, specific commit SHAs)
  so staleness is checkable, not just assumed away.
- **Test-fixture coupling risk:** `test_skill_router.py` monkeypatches
  `skill_router.SKILLS_DIR` rather than `HERMES_HOME`, because the module
  reads the env var at import time. If `skill_router.py` is later changed
  to resolve `HERMES_HOME` lazily (e.g. as part of Phase 2's `get_hermes_home()`
  fix, noted in `ARCHITECTURE.md` §8), these tests will still pass
  unchanged — the `SKILLS_DIR` patch point is stable either way.
- **No risk to production:** nothing in this phase can affect the running
  `hermes-enhanced-gateway` service — no file it imports was changed, no
  restart was performed, and no config/`.env`/systemd file was touched.

### Rollback

Every change in this phase is additive (new files only). Rollback is:

```bash
git checkout main
git branch -D feature/personal-system-foundation   # local only; do not force-delete a pushed branch without checking with the user first
```

or, to revert just this commit while keeping the branch:

```bash
git revert <this-entry's-commit-sha>
```

No data migration, schema change, or config change occurred, so there is
nothing else to undo — reverting the commit fully reverses this phase's
footprint.

### Pending / explicitly not done this phase

See `ROADMAP.md` Phase 2 onward in full. Summary: (1) decide the fate of
`hermes_enhanced/__init__.py`'s dead `critic_evaluate()`; (2) migrate
`enhanced_init.py`'s monkey-patch to a `pre_llm_call` plugin hook (needs a
live-gateway restart the user must supervise); (3) decide whether to
restore `hermes_enhanced/server.py` or retire `hermes-enhanced-bridge.service`;
(4) extract the BuJo cron-output cleaning logic into a pure function; (5)
the memory system itself (`memoria_activa_architecture.md` Fases 0-5),
almost entirely outside this repo.

### Push

Pushed to `origin/feature/personal-system-foundation` (see push output in
the surrounding conversation for the exact result — recorded there rather
than duplicated here to avoid this log going stale if the push is retried).
No PR opened, no merge to `main`, per the task brief.

---

## Entry 2 — 2026-08-08 — Phase 2 item 4: BuJo cron-output cleaning extraction

**Branch:** `feature/personal-system-phase2`
**Author:** Claude (agent), directed by Sebastián Alvarez

### Scope

Authorized scope: extract the inline noise-stripping/truncation logic in
`APIServerAdapter.send()` (`gateway/platforms/api_server.py`) into a pure
function (`_clean_cron_output`), plus its unit tests
(`tests/gateway/test_api_server_bujo_delivery.py`). Pure refactor, no
behavior change, per `ROADMAP.md` Phase 2 item 4.

### Commands executed and results

```bash
git diff --check
bash scripts/run_tests.sh tests/gateway/test_api_server_bujo_delivery.py -q
python3 -m py_compile gateway/platforms/api_server.py tests/gateway/test_api_server_bujo_delivery.py
```

- `git diff --check` — clean, no whitespace errors.
- Test suite — **19/19 passed**, 0 failed.
- `py_compile` — both files compile cleanly.

### Push

Pushed to `origin/feature/personal-system-phase2`.

---

## Entry 3 — 2026-08-08 — Phase 3A: memory schema traceability fields

**Branch:** `feature/personal-system-memory-foundation`
**Author:** Claude (agent), directed by Sebastián Alvarez

### Scope

Authorized scope: schema preparation only. Add `session_id` (TEXT),
`fact_type` (TEXT, default `'explicit'`), and `expires_at` (TIMESTAMP)
columns to the `facts` table in `plugins/memory/holographic/store.py`,
following the same additive PRAGMA-detect / `ALTER TABLE ... ADD COLUMN`
pattern already used for `hrr_vector`. Nullable/additive, no change to
`add_fact`/`search_facts`/`list_facts`/`update_fact`/`remove_fact`
semantics or dedup behavior; new columns are not yet populated or exposed
in any returned dict. Mechanical finalization of work already staged on
this branch — see `ROADMAP.md` Phase 3A for the full rationale and what
remains out of scope (Phase 3B+).

### Files changed

- `plugins/memory/holographic/store.py` — schema + migration.
- `tests/plugins/memory/test_holographic_schema_migration.py` — new, 11
  tests covering fresh-DB creation and migration of a pre-existing DB
  (with the `hrr_vector`-only schema) up to the new columns.
- `docs/personal-system/ROADMAP.md` — Phase 3A marked done, Phase 3B+
  scope reaffirmed as not started.

### Commands executed and results

```bash
git diff --check
bash scripts/run_tests.sh tests/plugins/memory/test_holographic_schema_migration.py -q
bash scripts/run_tests.sh tests/plugins/memory/ -q
python3 -m py_compile plugins/memory/holographic/store.py tests/plugins/memory/test_holographic_schema_migration.py
```

- `git diff --check` — clean, no whitespace errors.
- New test file — **11/11 passed**, 0 failed.
- Full `tests/plugins/memory/` suite — **471/471 passed**, 0 failed. No
  regression.
- `py_compile` — both files compile cleanly.

### Push

Pushed to `origin/feature/personal-system-memory-foundation`.

---

## Entry 4 — 2026-08-08 — Phase 3B-preparación: extraction provenance

**Branch:** `feature/personal-system-extraction-foundation`
**Author:** Claude (agent), directed by Sebastián Alvarez

### Scope

`add_fact` conserva compatibilidad total y ahora guarda `session_id`,
`fact_type` y `expires_at` cuando se proveen. El auto-extract marca los
hechos capturados con `fact_type='extracted'`. Filtro inicial de
extracción limitado al prefijo `[IMPORTANT:]`. `auto_extract` permanece
en `false` por defecto — sin cron ni datos reales involucrados en este
cambio.

### Files changed

- `plugins/memory/holographic/store.py`
- `plugins/memory/holographic/__init__.py`
- `docs/personal-system/ROADMAP.md`
- `tests/plugins/memory/test_holographic_extraction_metadata.py` — new.

### Commands executed and results

- New test file — **9/9 passed**, 0 failed.

### Push

Pushed to `origin/feature/personal-system-extraction-foundation`.

## Entry 5 — 2026-08-08 — Phase 3C: extraction dry-run preview

**Branch:** `feature/personal-system-extraction-dry-run`
**Author:** Claude (agent), directed by Sebastián Alvarez

### Scope

`HolographicMemoryProvider` gana `preview_extracted_facts()`, que reutiliza
las mismas reglas de detección de `_auto_extract_facts` para devolver los
hechos candidatos que una sesión produciría, sin tocar SQLite ni llamar a
`add_fact`. `auto_extract` permanece en `false` por defecto — sin cron ni
datos reales involucrados en este cambio.

### Files changed

- `plugins/memory/holographic/__init__.py`
- `docs/personal-system/ROADMAP.md`
- `tests/plugins/memory/test_holographic_extraction_dry_run.py` — new.

### Commands executed and results

- New test file — **12/12 passed**, 0 failed.

### Push

Pushed to `origin/feature/personal-system-extraction-dry-run`.

## Entry 6 — 2026-08-08 — Phase 3D: extraction preview CLI

**Branch:** `feature/personal-system-extraction-cli`
**Author:** Claude (agent), directed by Sebastián Alvarez

### Scope

`scripts/memory_preview.py` envuelve
`HolographicMemoryProvider.preview_extracted_facts()` en un CLI
independiente:

```
python3 scripts/memory_preview.py --input transcript.json
python3 scripts/memory_preview.py --input transcript.jsonl --session-id sess-42
```

Toma un transcript explícito vía `--input` (JSON: array de mensajes, o
JSONL: un objeto por línea), y escribe el resultado (`candidates`, `count`,
`session_id`) como JSON a stdout. No abre SQLite ni resuelve `HERMES_HOME`;
`auto_extract` permanece en `false` por defecto y no hay wiring de cron ni
datos reales migrados o extraídos.

### Files changed

- `scripts/memory_preview.py` — new.
- `tests/scripts/test_memory_preview.py` — new.
- `docs/personal-system/ROADMAP.md`
- `docs/personal-system/IMPLEMENTATION_LOG.md`

### Commands executed and results

- New test file — **13/13 passed** (CLI), 0 failed.
- Dry-run detection rules — **12/12 passed**, 0 failed.

### Rollback

Delete the `feature/personal-system-extraction-cli` branch; no other
system was touched.

### Push

Pushed to `origin/feature/personal-system-extraction-cli`.

## Entry 7 — 2026-08-08 — Fase 4-verificación: prefetch holográfico

**Branch:** `feature/personal-system-prefetch-verification`
**Author:** Claude (agent), directed by Sebastián Alvarez

### Scope

Verificación del prefetch holográfico y de `MemoryManager` mediante
`tests/plugins/memory/test_holographic_prefetch.py` (nuevo) y
`tests/agent/test_memory_skill_scaffolding.py` (actualizado), usando
SQLite temporal (sin datos reales), cubriendo el umbral `min_trust` y el
comportamiento fail-closed. Sin cambios de producción, configuración,
wiring de cron ni datos reales.

### Files changed

- `tests/plugins/memory/test_holographic_prefetch.py` — new.
- `tests/agent/test_memory_skill_scaffolding.py`
- `docs/personal-system/ROADMAP.md`
- `docs/personal-system/IMPLEMENTATION_LOG.md`

### Commands executed and results

- `tests/plugins/memory/test_holographic_prefetch.py` — **7/7 passed**, 0 failed.
- `tests/agent/test_memory_skill_scaffolding.py` (MemoryManager) — **14/14 passed**, 0 failed.

### Rollback

Delete the `feature/personal-system-prefetch-verification` branch; no other
system was touched.

### Push

Pushed to `origin/feature/personal-system-prefetch-verification`.

## Entry 8 — 2026-08-08 — Fase 5A: herramienta de migración offline

**Branch:** `feature/personal-system-memory-migration-tool`
**Author:** Claude (agent), directed by Sebastián Alvarez

### Scope

Herramienta offline `scripts/memory_migrate.py` para migrar filas de la
tabla legacy `agent_memory` a la tabla `facts` compatible con
`MemoryStore` (`fact`->`content`, `category`->`category`,
`confidence`->`trust_score`, `source`->`fact_type`, `expires_at`
verbatim; `session_id` siempre `NULL`). Dry-run es el comportamiento por
defecto y no escribe nada; `--apply` exige `--backup-dir` y toma copias
byte-a-byte con timestamp de `--source`/`--target` antes de escribir. Las
rutas reales de Hermes (`~/.hermes`, `~/.hermes-enhanced`,
`agent_memory.db`, `memory_store.db`, `state.db`, `bujo.sqlite`) quedan
bloqueadas salvo `--allow-real-paths` (y `--confirm-real-migration` para
`--apply`). Sin wiring de cron ni migración de datos reales.

### Files changed

- `scripts/memory_migrate.py` — new.
- `tests/scripts/test_memory_migrate.py` — new.
- `docs/personal-system/ROADMAP.md`
- `docs/personal-system/IMPLEMENTATION_LOG.md`

### Commands executed and results

- `tests/scripts/test_memory_migrate.py` — **18/18 passed**, 0 failed.

### Rollback

Restaurar backups tomados por `--apply` (timestamped, byte-for-byte de
`--source`/`--target`) o eliminar la rama
`feature/personal-system-memory-migration-tool`; ningún otro sistema fue
tocado, y no se ejecutó migración contra datos reales.

### Push

Pushed to `origin/feature/personal-system-memory-migration-tool`.

## Entry 9 — 2026-08-08 — Fase 5A: migración real ejecutada (una vez)

**Branch:** `feature/personal-system-memory-migration-tool`
**Author:** Claude (agent), directed by Sebastián Alvarez

### Scope

Ejecución real, única, de `scripts/memory_migrate.py --apply` contra las
rutas reales de Hermes, tras la verificación offline de la Entry 8
(18/18 tests). Requirió `--allow-real-paths --confirm-real-migration`
explícitos, ya que las rutas reales están bloqueadas por defecto.
`state.db` y `bujo.sqlite` no fueron tocadas por esta herramienta — solo
lee/escribe `agent_memory.db` (origen) y `memory_store.db` (destino).

### Backup

`/home/ubuntu/hermes-backups/memory-migration-20260808T191738Z` — copias
byte-a-byte con timestamp de `--source` y `--target`, tomadas antes de
cualquier escritura, tal como hace `--apply` por diseño.

### Resultado

- **18/18 filas** de `agent_memory` insertadas en `facts` (destino).
- **`agent_memory`, `state.db` y `bujo.sqlite` intactas**, verificado por
  hash contra el backup.
- Destino (`memory_store.db`) contiene **18 facts** tras la migración.
- Dry-run posterior contra el mismo origen/destino: **18 duplicadas / 0
  insertables** — confirma que la migración fue completa y que el
  dedup de la herramienta evita reinserciones si se vuelve a ejecutar.

### Rollback

Restaurar `agent_memory.db` y `memory_store.db` desde
`/home/ubuntu/hermes-backups/memory-migration-20260808T191738Z`.

## Entry 10 — 2026-08-08 — Gobernanza: mutaciones auditadas de hechos

**Branch:** `feature/personal-system-memory-governance`
**Author:** Claude (agent), directed by Sebastián Alvarez

### Scope

Store-level governance layer for `MemoryStore`: `update_fact_audited` and
`forget_fact_audited`, the explicit-by-id counterparts to the existing
`update_fact`/`remove_fact` (which stay unchanged). Both new methods
require a non-blank `reason` (raise `ValueError` otherwise), operate only
on an existing `fact_id` (raise `KeyError` if absent), and write the
mutation plus a row in a new, purely additive `fact_governance_audit`
table (`CREATE TABLE IF NOT EXISTS`, same pattern as `memory_banks`) in
one explicit SQLite transaction (`BEGIN IMMEDIATE` / `COMMIT`, with
`ROLLBACK` on any failure) — a failed audit write rolls back the
mutation and vice versa. `update_fact_audited` never `INSERT`s into
`facts` (UPDATE-only) and preserves the `content` UNIQUE dedup invariant
(a colliding update is rejected via `IntegrityError` → `ValueError`, no
silent merge); if every provided field already matches the current row
(or no fields are provided), it is an explicit no-op — the `facts` row
is left untouched but the attempt is still audited with the `reason`
recorded and `old`/`new` fields absent. `forget_fact_audited` deletes the
`facts` row (and its `fact_entities` links) and audits the prior
content/category/trust before removal. The audit table stores only the
fact's own before/after fields, `reason`, and optional `session_id` —
never secrets or conversation transcripts.

### Files changed

- `plugins/memory/holographic/store.py` — `fact_governance_audit` table +
  index (additive schema), `update_fact_audited`, `forget_fact_audited`.
- `tests/plugins/memory/test_holographic_governance.py` — new.

### Results

- New test file — **21/21 passed**: audit-table additivity on fresh and
  legacy DBs (idempotent reopen, no data loss), `update_fact_audited`
  (content/category/trust changes, category bank moves, missing-reason
  rejection, nonexistent `fact_id`, empty content, out-of-range
  `trust_score`, dedup-collision rejection, no-op when values already
  match or no fields given, never creates a new fact, rollback on
  simulated audit-write failure), `forget_fact_audited` (removal +
  audit record, missing-reason rejection, nonexistent `fact_id`, leaves
  unrelated facts untouched, rollback on simulated audit-write failure).
- Broader relevant suite — **58/58 passed**, 0 failed.
- All databases used in tests are built under `tmp_path`; nothing under
  `~/.hermes` or `~/.hermes-enhanced` was read or written. No config,
  cron, or systemd/service interaction. No real data touched.

### Rollback

Every change in this entry is additive (new table, new methods; no
existing method's behavior changed). Rollback is reverting this commit,
or deleting the `feature/personal-system-memory-governance` branch — no
data migration or config change occurred.

### Pending / explicitly not done this entry

Exposing `update_fact_audited`/`forget_fact_audited` as an agent-facing
tool (schema + handler wiring in `plugins/memory/holographic/__init__.py`)
remains not started — see `ROADMAP.md`'s cross-cutting section.

### Push

Pushed to `origin/feature/personal-system-memory-governance`.

## Entry 11 — 2026-08-08 — Lector de auditoría de solo lectura

**Branch:** `feature/personal-system-memory-audit-readonly`
**Author:** Claude (agent), directed by Sebastián Alvarez

### Scope

CLI de solo lectura `scripts/memory_audit.py` sobre la tabla
`fact_governance_audit` que Entry 10 introdujo. `--db PATH` es obligatorio
y nunca se infiere `HERMES_HOME`. La conexión se abre con los flags URI de
SQLite `mode=ro&immutable=1`, de modo que cualquier escritura falla al
nivel de SQLite (no solo por convención del código) y, como `MemoryStore`
usa modo WAL, `immutable=1` evita además que este CLI abra o cree los
archivos secundarios `-wal`/`-shm`. Soporta `--fact-id` (filtra a un solo
`fact_id`) y `--limit` (por defecto 50, debe ser positivo), con filas
ordenadas por `audit_id DESC`. Las rutas reales de Hermes quedan protegidas
reutilizando el mismo guard `is_guarded_path` de `memory_migrate.py`
(`~/.hermes`, `~/.hermes-enhanced`, o archivos llamados
`agent_memory.db`/`memory_store.db`/`state.db`/`bujo.sqlite`) salvo
`--allow-real-paths` — esa bandera solo levanta el chequeo de ruta, nunca
hace escribible la conexión. Como `immutable=1` omite por completo el
chequeo de WAL/locking, este CLI no verá filas que sigan sin checkpoint en
un `-wal` pendiente: apuntar `--db` a un snapshot/backup estable, o
ejecutar `PRAGMA wal_checkpoint(TRUNCATE);` contra la base de datos en vivo
primero, si se necesitan las filas de auditoría más recientes.

### Files changed

- `scripts/memory_audit.py` — new.
- `tests/scripts/test_memory_audit.py` — new.
- `docs/personal-system/ROADMAP.md`
- `docs/personal-system/IMPLEMENTATION_LOG.md`

### Commands executed and results

```bash
bash scripts/run_tests.sh tests/scripts/test_memory_audit.py -q
```

- New test file — **24/24 passed**, 0 failed.

### Rollback

Additive-only change (new script + new test file). Rollback is reverting
this commit, or deleting the `feature/personal-system-memory-audit-readonly`
branch — no data migration or config change occurred, and no real database
was read or touched.

### Push

Pushed to `origin/feature/personal-system-memory-audit-readonly`.

## Entry 12 — 2026-08-08 — CLI de gobernanza (mutaciones auditadas)

**Branch:** `feature/personal-system-memory-governance-cli`
**Author:** Claude (agent), directed by Sebastián Alvarez

### Scope

CLI `scripts/memory_governance.py`, contraparte de escritura del lector
de solo lectura de Entry 11, sobre `MemoryStore.update_fact_audited`/
`forget_fact_audited`. Soporta `--action update` (cambia
`content`/`category`/`trust_score`) y `--action forget` (borra el fact),
ambas sobre un `--fact-id` único. Preview (dry-run) es el comportamiento
por defecto y permanece inmutable: nunca importa ni construye
`MemoryStore`, nunca resuelve `HERMES_HOME` y nunca toma backup — solo
abre `--db` en modo lectura (`mode=ro&immutable=1`, igual que
`memory_audit.py`) para imprimir un diff JSON de lo que haría `--apply`.
`--reason` es obligatorio en ambas acciones y queda registrado en
`fact_governance_audit`; `--action forget` exige además
`--confirm-forget`. `--apply` requiere un `--backup-dir` explícito y toma
una copia byte-a-byte y timestamped de `--db` antes de cualquier
escritura — si el backup falla, no se escribe nada. Las rutas reales de
Hermes quedan protegidas por el mismo guard `is_guarded_path` que usan
`memory_migrate.py`/`memory_audit.py` (`~/.hermes`, `~/.hermes-enhanced`,
o archivos llamados `agent_memory.db`/`memory_store.db`/`state.db`/
`bujo.sqlite`) salvo `--allow-real-paths`, y `--apply` contra una ruta así
exige además `--confirm-real-governance`.

### Files changed

- `scripts/memory_governance.py` — new.
- `tests/scripts/test_memory_governance.py` — new.
- `docs/personal-system/ROADMAP.md`
- `docs/personal-system/IMPLEMENTATION_LOG.md`

### Commands executed and results

```bash
bash scripts/run_tests.sh tests/scripts/test_memory_governance.py -q
```

- New test file — **36/36 passed**, 0 failed. Suites de auditoría y
  gobernanza combinadas: **81/81 passed**.

### Rollback

Additive-only change (new script + new test file). Rollback is reverting
this commit, or deleting the `feature/personal-system-memory-governance-cli`
branch — no data migration or config change occurred, and no real database
was touched.

### Push

Pushed to `origin/feature/personal-system-memory-governance-cli`.

## Entry 13 — Atomicidad de la migración por lote

`MemoryStore.transaction()` agrupa la aplicación de una migración completa y
realiza rollback total si falla una inserción o actualización intermedia.
La corrección fue validada con un test de fallo parcial y no implica ninguna
migración real ni modificación de bases de producción.

---

## Entry 14 — 2026-08-09 — Aclaración documental: `auto_extract`, default vs. configuración efectiva

**Branch:** `feature/personal-system-auto-extract-docs`
**Author:** Claude (agent), directed by Sebastián Alvarez

### Scope

Solo documentación. Ningún archivo de código, configuración, servicio,
cron o `.env` fue leído para modificarse, y ninguno de esos fue tocado.
Se leyó (sin modificar) `~/.hermes-enhanced/config.yaml`, fuera de este
repo, únicamente para verificar el valor de una clave — `auto_extract`
bajo `plugins.hermes-memory-store` — sin copiar ningún otro contenido
del archivo (que incluye una clave de API en otra línea) a ningún
archivo de este repo.

### Qué se corrigió

Varias entradas históricas de este log (Entries 4-6) y varios "Update"
de `ROADMAP.md` (Updates (2)-(7)) afirman "`auto_extract` remains
`false` by default". Esa frase es correcta como descripción del **default
del código** (`plugins/memory/holographic/__init__.py`: esquema de
configuración con `"default": "false"`, y
`on_session_end()` usando `self._config.get("auto_extract", False)` como
fallback), pero ninguna de esas entradas verificó la **configuración
efectiva** del gateway real. Esa verificación, hecha en esta entrada,
confirma que `~/.hermes-enhanced/config.yaml` fija explícitamente
`plugins.hermes-memory-store.auto_extract: true` — es decir, en el
sistema tal como está configurado hoy, la extracción automática al
final de sesión sí está activa, al margen de cuál sea el default del
código.

Las entradas históricas no se editaron ni se borraron — describen
correctamente el estado del código en el momento en que se escribieron
y se conservan como registro de implementación. `ROADMAP.md` recibió una
sección nueva ("Update — 2026-08-09 — `auto_extract`: estado actual
(código vs. configuración efectiva)") que distingue explícitamente
default del código, configuración efectiva, y riesgos derivados de la
discrepancia entre ambos.

### Qué NO hace esta entrada

- No activa, desactiva ni modifica `auto_extract` en ningún archivo de
  configuración, real o de prueba.
- No modifica `plugins/memory/holographic/__init__.py` ni ningún otro
  archivo de código.
- No implica ni ejecuta ninguna migración real de datos.
- No valida todavía la extracción automática en producción — esa
  validación (facts efectivamente extraídos, asociación correcta de
  `session_id`, tasa de falsos positivos, comportamiento de la
  deduplicación de `add_fact` bajo extracción repetida) queda pendiente
  y explícitamente fuera de esta fase, per `ROADMAP.md`.

### Files changed

- `docs/personal-system/ROADMAP.md`
- `docs/personal-system/IMPLEMENTATION_LOG.md` — this entry.

### Commands executed and results

```bash
grep -n "auto_extract" ~/.hermes-enhanced/config.yaml   # read-only
grep -n "auto_extract" plugins/memory/holographic/__init__.py   # read-only
git diff --check
```

- `~/.hermes-enhanced/config.yaml` — confirmed
  `plugins.hermes-memory-store.auto_extract: true` (real, effective
  configuration; file not modified, not copied into the repo beyond this
  one key/value pair).
- `plugins/memory/holographic/__init__.py` — confirmed code-level default
  is `false` (schema `"default": "false"`; `on_session_end()` fallback
  `False`). File not modified.
- `git diff --check` — clean, no whitespace errors.

### Rollback

Docs-only, additive change (new section in `ROADMAP.md`, new entry here).
Rollback is reverting this commit, or deleting the
`feature/personal-system-auto-extract-docs` branch — no code, config,
service, cron, `.env`, database, or real data was touched.

### Push

Pushed to `origin/feature/personal-system-auto-extract-docs`.

## Entry 15 — Runner de migración desacoplado

Se añadió `scripts/memory_migrate_detached.py` junto con
`docs/personal-system/DETACHED_MIGRATION_RUNNER.md`. La herramienta es
plan-only por defecto, fija el Python del venv, exige rutas explícitas,
protege rutas Hermes reales y usa rollback local y resultado JSON/log cuando
se ejecuta sobre una copia offline. No detiene, inicia, reinicia ni cambia la
base activa del gateway; esta fase no ejecuta ni afirma una nueva migración
real.

## Entry 16 — 2026-08-09 — Primera unidad funcional: verificación end-to-end del ciclo de extracción

**Branch:** `feature/personal-system-functional-extraction`
**Base commit:** `8b46911b8` (`feature/personal-system-detached-migration-runner`)
**Author:** Claude (agent), directed by Sebastián Alvarez

### Scope

Asegurar y verificar el ciclo de extracción automática al cierre de sesión
para `HolographicMemoryProvider`, siguiendo el camino real
`AIAgent`/`MemoryManager`/gateway. Autorizado: corregir un bug de wiring si
existiera (mínima superficie), o si no existiera, añadir la observabilidad/
test de contrato que falte, sin inventar extracción ni tocar configuración
efectiva, servicios, cron, `.env`, o bases reales bajo `~/.hermes`/
`~/.hermes-enhanced`.

### Investigación (solo lectura)

Se trazó el camino completo con lectura de código, sin modificar nada
inicialmente:

- `run_agent.py`: `AIAgent._session_messages` (línea 1704, actualizado en
  cada turno por `_persist_session`), `shutdown_memory_provider()` (línea
  3380), `commit_memory_session()` (línea 3407).
- `cli.py:1103-1149` (salida de CLI) y `gateway/run.py:6138-6169`
  (`_cleanup_agent_resources`, limpieza de agente cacheado): ambos ya
  reenvían `_session_messages` real (fix histórico #15165, con tests
  dedicados `tests/gateway/test_shutdown_memory_provider_messages.py` y
  `tests/cli/test_cli_shutdown_memory_messages.py`).
- `gateway/run.py:16619-16673` (`_commit_memory_before_soft_evict`, fix
  histórico #11205): compensa el caso de soft-eviction por presión de
  caché LRU antes de que el watcher de expiración vea el agente.
- `agent/memory_manager.py:774-833` (`MemoryManager.on_session_end`,
  `commit_session_boundary_async`): fan-out a todos los providers
  registrados, sin filtrar ni transformar el transcript.
- `plugins/memory/holographic/__init__.py:254-449`
  (`HolographicMemoryProvider.on_session_end`, `_auto_extract_facts`,
  `preview_extracted_facts`): sí evalúa `auto_extract` desde config, sí
  aborta con `messages` vacío o sin store, sí filtra `role == "user"` y
  contenido con prefijo `"[IMPORTANT:"`, sí pasa `session_id` y
  `fact_type="extracted"` a `add_fact`.
- `plugins/memory/holographic/store.py:290-349` (`MemoryStore.add_fact`):
  dedup vía `UNIQUE(content)`, devuelve `fact_id` existente sin pisar
  metadata en duplicado.

Un subagente de exploración (`Explore`) hizo el mismo trazado en paralelo,
de forma independiente, y llegó a la misma conclusión con las mismas citas
de línea — usado como segunda verificación, no como fuente única.

### Hallazgo

**No hay bug de wiring.** El camino `AIAgent`/`MemoryManager`/
`HolographicMemoryProvider` ya estaba correctamente cableado antes de esta
fase, incluyendo dos fixes históricos (#15165 transcript vacío, #11205
soft-eviction) ya verificados con tests propios. Por lo tanto esta fase no
modificó ningún archivo de producción — cero cambios en
`plugins/memory/holographic/__init__.py`, `plugins/memory/holographic/store.py`,
`agent/memory_manager.py`, `run_agent.py`, `cli.py`, ni `gateway/run.py`.

Lo que sí faltaba: un test que ejercitara el camino completo
*`config.yaml` real en disco → `MemoryManager.on_session_end` → persistencia*
en una sola prueba end-to-end, incluyendo dedup en una segunda llamada real
(no una llamada directa a `_auto_extract_facts`) y el caso de transcript
vacío bajo `auto_extract: true` activo (antes solo estaba cubierto el caso
`auto_extract: false`).

### Files changed

- `tests/plugins/memory/test_holographic_session_extraction_e2e.py` — new,
  4 tests.
- `docs/personal-system/ROADMAP.md` — nueva sección de update.
- `docs/personal-system/IMPLEMENTATION_LOG.md` — this entry.

### Qué cubre el test nuevo

1. `test_mixed_transcript_persists_exactly_one_fact_with_session_id` —
   config real en disco con `auto_extract: true`, provider construido sin
   pasar `config=` (fuerza `_load_plugin_config()` a leer el archivo),
   registrado en un `MemoryManager` real, transcript con roles
   `system`/`user`/`assistant`/`tool` y un mensaje `"[IMPORTANT:"` —
   verifica exactamente 1 fact con `session_id`/`fact_type` correctos.
2. `test_second_session_end_call_dedups_instead_of_duplicating` — llama
   `manager.on_session_end(...)` dos veces con el mismo transcript;
   verifica que la segunda no duplica.
3. `test_empty_transcript_writes_nothing` — `on_session_end([])` con
   `auto_extract: true`; verifica cero facts.
4. `test_auto_extract_false_from_disk_config_skips_extraction` — mismo
   camino de carga desde disco, pero `auto_extract: false`; verifica que
   el gate lee el valor real del archivo.

### Commands executed and results

```bash
bash scripts/run_tests.sh tests/plugins/memory/test_holographic_session_extraction_e2e.py -q
bash scripts/run_tests.sh tests/plugins/memory/ tests/agent/test_memory_boundary_commit.py \
    tests/agent/test_memory_skill_scaffolding.py \
    tests/gateway/test_shutdown_memory_provider_messages.py \
    tests/cli/test_cli_shutdown_memory_messages.py \
    tests/run_agent/test_commit_memory_session_context_engine.py -q
python3 -m py_compile tests/plugins/memory/test_holographic_session_extraction_e2e.py
git diff --check
```

- New test file — **4/4 passed**, 0 failed.
- Broader regression check (memory plugin suite + all session-boundary /
  shutdown-messages tests, chosen because they're the tests nearest the
  code this phase read/reasoned about) — **572/572 passed**, 0 failed. No
  regression.
- `py_compile` — clean.
- `git diff --check` — clean, no whitespace errors.

### No production code, config, service, or real data touched

`git diff main --stat` for this branch shows only the test file and the two
docs files. No file under `~/.hermes` or `~/.hermes-enhanced` was read or
written (the test's `HERMES_HOME` is the per-test tempdir the
`_hermetic_environment` autouse fixture already provides). No service was
restarted, no cron/`.env` file touched, no `sudo` used.

### What this entry does NOT establish

Same limitation `ROADMAP.md`'s 2026-08-09 update already flagged before this
phase, restated because it remains true after this phase too: **natural
extraction in production is still unmeasured.** This entry proves the code
path is wired correctly against synthetic transcripts and temp SQLite; it
does not measure real extraction volume, false-positive rate of
`_PREF_PATTERNS`/`_DECISION_PATTERNS` against real conversational language,
or dedup behavior across real sessions with differently-worded restatements
of the same fact. No preview/diagnostic route was added because
`preview_extracted_facts()` (Phase 3C) and `scripts/memory_preview.py`
(Phase 3D) already provide a safe, read-only way to inspect what a real
transcript would extract without writing anything — duplicating that would
have been unnecessary surface.

### Rollback

Additive-only change (new test file, doc updates only). Rollback is
reverting this commit, or deleting the
`feature/personal-system-functional-extraction` branch — no production
code, config, or real data was touched.

### Push

Pushed to `origin/feature/personal-system-functional-extraction`.

## Entry 17 — Handoffs persistentes

Se añadió `memory_handoffs` como tabla additive e idempotente en
`MemoryStore`, con CRUD transaccional y estados válidos (`open`,
`in_progress`, `blocked`, `done`, `abandoned`). El provider expone el tool
`memory_handoff` con acciones de crear, obtener, listar y actualizar; cada
creación puede conservar el `session_id` actual. Los handoffs son estado de
trabajo mutable, separado de facts y BuJo: no generan tareas, eventos,
recordatorios ni escrituras fuera de la base configurada por el provider.
La fase se prueba solamente contra SQLite temporal.

## Entry 18 — Gobernanza agent-facing auditada

El provider ahora expone `memory_governance` para `update` y `forget`. Cada
acción requiere `fact_id` y `reason`; olvidar requiere además
`confirm_forget: true`. Las acciones mutantes antiguas de `fact_store` ya no
se exponen y sus invocaciones directas son rechazadas. Las operaciones
válidas llaman exclusivamente a `update_fact_audited`/
`forget_fact_audited`, con auditoría transaccional en
`fact_governance_audit`. Tests y validación usan SQLite temporal; no se
modificaron facts reales.
