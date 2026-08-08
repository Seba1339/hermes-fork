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
