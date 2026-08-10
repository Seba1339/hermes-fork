# Architecture — What Actually Runs

> **Audience:** Anyone about to touch `hermes_enhanced/`, `enhanced_init.py`,
> the perspective tools, or BuJo delivery.
> **Method:** Verified against live `systemctl` state, `journalctl`, git
> history (`git log -p -S`), and the actual source on this branch as of
> commit `0243d78b3` — not against assumptions. Where a claim below could
> not be verified, it's marked as such.
> **Last updated:** 2026-08-08

## 1. The three "Enhanced" things (don't confuse them)

There are three unrelated systems that all have "enhanced" in the name.
Confusing them is the most likely way to break something live.

| # | systemd unit | State (2026-08-08) | What it actually is |
|---|---|---|---|
| 1 | `hermes-enhanced.service` | **active, running** (system-level, port 5053) | A separate FastAPI app (`backend.main:app`) at `/home/ubuntu/projects/hermes-chat-enhanced` — **not in this repo**. Depends on an rclone mount (`hermes-enhanced.service.d/10-drive-dependency.conf` requires `rclone-drive.service`). Out of scope for this repo's changes. |
| 2 | `hermes-enhanced-gateway.service` (system-level) **and** `hermes-gateway-enhanced.service` (user-level, `~/.config/systemd/user/`) | **active, running** | This repo's `enhanced_init.py`, run with `HERMES_HOME=~/.hermes-enhanced`. This is the live personal Hermes gateway — the one that actually talks to Luna. Both unit files point at the same `enhanced_init.py`; treat them as the same logical service (the system-level one is the one enabled in `multi-user.target.wants`). |
| 3 | `hermes-enhanced-bridge.service` | **inactive** (exited cleanly, PID 337321, 2026-06-22) | Was `python -m hermes_enhanced.server`. `hermes_enhanced/server.py` **was deleted** in the 2026-07-15 purge (`e8a0feeef`) and never restored — only `hermes_enhanced/skill_router.py` and `hermes_enhanced/__init__.py` survived. The unit is still `enabled` in systemd but its target module no longer exists in this repo; if it's ever restarted (`systemctl start`), it will fail immediately with `ModuleNotFoundError`. **Do not delete the unit file or "fix" this without checking with the user first** — it may be intentionally retired, or the module may need restoring like `enhanced_init.py` was. Documented, not touched, in this phase. |

`hermes-chat.service` (user-level, Flask+SocketIO on :5051,
`/home/ubuntu/projects/hermes-chat`) is a fourth, older, unrelated chat
surface — also outside this repo.

## 2. The live entry point: `enhanced_init.py`

`enhanced_init.py` (repo root, 67 lines) is what `hermes-enhanced-gateway`
actually runs. It:

1. Sets `HERMES_HOME=~/.hermes-enhanced`, `HERMES_ENHANCED=1`,
   `HERMES_SESSION_SOURCE=enhanced` (redundant with the systemd unit's own
   `Environment=` lines — both set the same values; harmless but worth
   knowing if the two ever disagree, the unit file wins because env vars
   set by systemd exist before the process starts).
2. Monkey-patches `AIAgent.run_conversation` (imported from `run_agent.py`)
   to call `hermes_enhanced.skill_router.auto_load()` on every user message
   and appends the result to `system_message` as
   `"LOAD THESE SKILLS: [...]"`.
3. Starts the gateway via `runpy.run_module("hermes_cli.main", ...)` with
   `sys.argv = ["hermes", "gateway", "run", "--replace"]`.

**This is a core-loop patch applied from outside the plugin system.**
AGENTS.md documents a real, supported extension point for exactly this use
case — `pre_llm_call` / `post_llm_call` plugin hooks
(`hermes_cli/plugins.py`, see AGENTS.md "General plugins") — which would
let skill injection happen without monkey-patching `AIAgent.run_conversation`
directly. `enhanced_init.py` predates that being the obvious choice, or was
written before the hook was known to fit. **Recommendation (not done this
phase):** migrate `apply_patches()` to a `pre_llm_call` plugin hook. This
would remove the monkey-patch, make the skill-injection behavior visible to
the plugin hook chain, and let it compose with other plugins instead of
silently wrapping the class method. It's not done now because it changes
the live gateway's behavior and needs a restart + real-traffic verification
that's out of scope for a documentation-and-tests phase — see
`ROADMAP.md` Phase 2.

The docstring inside `apply_patches()` says *"Aplica parches en caliente al
AIAgent (solo skill router, critic purgado)"* — "critic purged." This
confirms finding #3 below: the critic-loop hook that `hermes_enhanced/__init__.py`
still defines is intentionally not called from `enhanced_init.py` anymore.

## 3. `hermes_enhanced/` package inventory

| File | Status | Notes |
|---|---|---|
| `hermes_enhanced/__init__.py` (92 lines) | **Live import, dead functions** | Imported by nothing except direct dotted access; `critic_evaluate()` and `estimate_task_complexity()` are fully implemented, heuristic (no LLM call), and referenced by **zero** call sites repo-wide (verified via `grep -rn "critic_evaluate\|estimate_task_complexity"`). This matches the "critic purgado" comment in `enhanced_init.py` — the hook that used to call `critic_evaluate()` after each response was removed in the 2026-07-15 purge, but the function itself survived because it lived in `__init__.py` rather than one of the deleted files. **This is genuinely dead code today** (not "dead but load-bearing" like `enhanced_init.py` was) — but per AGENTS.md's dead-code pitfall, no action was taken without the user weighing in; see ROADMAP.md. |
| `hermes_enhanced/skill_router.py` (853 lines) | **Live, actively used** | `auto_load()` is called on every message by `enhanced_init.py`. Pure regex/YAML-frontmatter classifier (`classify`, `load_skill_triggers`, `auto_load`) plus an optional sentence-transformers semantic path (`semantic_classify`, `auto_load_semantic`) that is not wired into `enhanced_init.py` at all (only reachable via `main()`, the file's own CLI entry point, or direct import). No test coverage before this phase — see `tests/hermes_enhanced/test_skill_router.py`. |
| `hermes_enhanced/bridge.py`, `changelog.py`, `coding.py`, `coding_practices.py`, `growth.py`, `project_kb.py`, `sandbox.py`, `server.py` | **Deleted, not restored** | Removed in `e8a0feeef` (2026-07-15). Stale `.pyc` files for all eight still sit in `hermes_enhanced/__pycache__/` — harmless (Python won't import a `.pyc` with no matching `.py` in a normal import), but a signal that the purge's blast radius was large and only `enhanced_init.py` got the "wait, that's live" correction. `hermes-enhanced-bridge.service` references `server.py` (see §1, item 3). |

## 4. Model / perspective routing ("model_router")

There is no file literally named `model_router.py`. The functional
equivalent — deciding which external model perspective(s) to consult for a
task — is two cooperating, already-shipped, already-wired core tools:

- **`tools/perspective_router.py`** (`perspective_router` tool) — pure,
  deterministic classifier. Given a task description, matches it against an
  ordered list of `(category, keywords, perspectives)` rules
  (`_ROUTE_RULES`) and returns a recommended panel (e.g. `architecture` →
  `("gemini", "claude")`). Escalates to include `claude` whenever
  `risk="alto"/"high"/"crítico"/"critical"`. Never calls a model. First
  matching rule wins — order in `_ROUTE_RULES` is significant (pinned by
  `tests/tools/test_perspective_router.py::TestClassify::test_first_matching_rule_wins_when_keywords_overlap_across_categories`).
- **`tools/perspective_quota.py`** — SQLite-backed rate limiter
  (`~/.hermes*/data/perspective_usage.sqlite`) enforcing
  `max_calls_per_session` / `max_calls_per_hour` per `(session_id,
  perspective)` pair, configured under a `perspectives:` key in
  `config.yaml` (not `.env` — correctly follows AGENTS.md's config-vs-secret
  rule). `reserve_perspective_call()` is the gate; `tools/claude_perspective_tool.py`
  (not audited in depth this phase) is presumably the caller that actually
  invokes a perspective once a reservation is granted.

Both are registered as core tools under the `perspectives` toolset in
`toolsets.py` (`_HERMES_CORE_TOOLS` line ~67, `TOOLSETS["perspectives"]`
line ~253) — this is correctly wired, not orphaned. `website/docs/integrations/providers.md`
mentions "model router" only in the generic sense (provider selection docs
for users), unrelated to this personal-routing layer.

## 5. Gateway BuJo delivery

`gateway/platforms/api_server.py`, `APIServerAdapter.send()` (~line 5105).
This is the only code path in this repo that writes to the personal
journal database (`bujo.sqlite`). Key invariant, stated in the method's own
docstring and enforced in code:

> Cron output is intentionally not persisted to BuJo by default. BuJo is
> for explicit human intent, not telemetry or scheduled-job output.

Behavior, pinned by `tests/gateway/test_api_server_bujo_delivery.py`:

- `metadata` without `bujo_write: True` (or no metadata at all) → no-op,
  `SendResult(success=True)`. This is what makes routine cron noise not
  flood the journal.
- `bujo_write: True` → content is cleaned (noise-line regexes strip cron
  banners/separators), collapsed into one summary line capped at 600 chars,
  and inserted as a single row in `bujo_entries` (columns: `date`,
  `section`, `item_type`, `content`, `depth`, `sort_order`), with
  `sort_order` computed as `MAX(sort_order)+1` for that `(date, section)` —
  i.e. appended, never overwritten.
- If cleaning removes every line (all-noise output), the write is skipped
  entirely — no empty/junk entries.
- Missing `bujo.sqlite` → `SendResult(success=False, error=...)`, not an
  exception — callers see a clean failure.
- After a successful write, it best-effort POSTs a `bujo_update` webhook to
  `http://127.0.0.1:5052/api/notify` (a local web UI, presumably part of
  `hermes-enhanced.service` or `hermes-chat.service`) inside a bare
  `except Exception: pass` — a failed notify never fails the delivery.

**Observation, not fixed this phase:** the cleaning/summarizing logic
(noise-pattern stripping, 4-line/600-char truncation) is inlined in the
`async def send()` method rather than extracted into a pure, unit-testable
function. The behavior test above exercises it end-to-end through a real
temp SQLite file, which is sufficient for a regression net, but a future
refactor extracting e.g. `_clean_cron_output(content: str) -> str | None`
would make edge cases (unicode truncation, multi-byte `600`-char cutoffs)
easier to test directly. Left as a `ROADMAP.md` candidate, not done here —
it would touch a live gateway file for a readability improvement with no
behavior change, which doesn't clear the bar for this phase.

## 6. Cron

Covered fully by `AGENTS.md`'s own "Cron (scheduled jobs)" section
(`cron/jobs.py`, `cron/scheduler.py`) — no in-repo inconsistency found. The
personal deployment's real `jobs.json` lives under
`~/.hermes-enhanced/cron/` (not inspected/modified this phase — real
schedule data). The `memoria_activa_architecture.md` proposal (see
`ROADMAP.md`) assumes several new cron scripts
(`memory_extract.py`, `cross_session_patterns.py`, etc.) will be added to
`~/.hermes/scripts/` following the same pattern as the existing
`cross_salud_finanzas.py` / `bujo_cross_insights.py` — i.e. **outside this
repo**, following the "script" cron-job field documented in AGENTS.md, not
as new core tools.

## 7. Memory landscape (as of this phase)

See `memoria_activa_architecture.md` for the full proposal. Summary of what
exists today, verified in this repo:

- **`plugins/memory/holographic/`** — the in-tree `MemoryProvider` ABC
  implementation (HRR 1024-dim vectors, FTS5+Jaccard+HRR hybrid search,
  trust scoring). Has existing test coverage
  (`tests/plugins/memory/test_holographic_*.py`). This is the component the
  memory proposal wants to extend (`prefetch()`, new `session_id`/`fact_type`
  fields, daily/weekly banks) rather than replace.
- **`agent/memory_manager.py`**, **`agent/memory_provider.py`** — the
  orchestration layer and ABC, per AGENTS.md's "Memory-provider plugins"
  section. Not modified this phase.
- Everything else the proposal references (`agent_memory.py`,
  `session_context.py`, `bujo_insights.py`, `cross_salud_finanzas.py`,
  `health_briefing.py`, `agent_eval.py`, ~200 other scripts) lives in
  `~/.hermes/scripts/` — **outside this repo, real personal automation,
  not inspected beyond file listing and a docstring-level read of
  `agent_eval.py`** (a small, low-risk performance-logging CLI:
  `agent_eval.py log --agent <name> --task <category> --useful yes/no`,
  appending to `~/.hermes-enhanced/data/agent_performance.jsonl`).

## 8. Audit findings summary (TODO/FIXME/inconsistency sweep)

- No `TODO`/`FIXME`/`XXX`/`HACK` markers found in `hermes_enhanced/*.py`,
  `enhanced_init.py`, `tools/perspective_router.py`,
  `tools/perspective_quota.py`, or `tools/claude_perspective_tool.py`.
- `hermes_enhanced/skill_router.py` resolves `HERMES_HOME` via
  `os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes-enhanced"))`
  at **module import time**, instead of calling
  `hermes_constants.get_hermes_home()` (whose default is `~/.hermes`, not
  `~/.hermes-enhanced`) at call time. In production this is harmless today —
  the systemd unit always sets `HERMES_HOME` explicitly before the process
  starts — but it means (a) the module's default silently disagrees with
  every other component's default if it's ever imported without
  `HERMES_HOME` set, and (b) the value is frozen at import time, so changing
  `HERMES_HOME` later in the same process (as profile-switching code does)
  would not be picked up. Not fixed this phase (this file is outside core,
  and the live gateway always sets the env var first) — noted for
  `ROADMAP.md`.
- `hermes_enhanced/__init__.py`'s `critic_evaluate()` /
  `estimate_task_complexity()` are dead code (see §3). Recommendation:
  either wire `critic_evaluate()` back in as a `post_llm_call` plugin hook
  (it's already side-effect-free and heuristic-only) or delete it — a call
  the user should make, not an automatic cleanup.
- `hermes-enhanced-bridge.service` targets a deleted module (see §1). Same
  category of decision: restore `hermes_enhanced/server.py` from git
  history (`git show e8a0feeef~1:hermes_enhanced/server.py`) if the bridge
  is still wanted, or formally retire the unit if not.
- No duplicate BuJo-writing code paths found — `APIServerAdapter.send()` is
  the only writer to `bujo_entries` in this repo (the rest of BuJo's CRUD
  presumably lives in the external `hermes-chat-enhanced` / BuJo app, out
  of scope).
- **Stale existing test found (not authored this phase, not fixed this
  phase):** `tests/gateway/test_api_server.py::TestSendMethod::test_send_returns_not_supported`
  asserts `adapter.send("chat1", "hello")` with no metadata returns
  `success is False`. Current code returns `success=True` for that exact
  case (§5's "no-op by default" behavior). This mismatch is present on
  `main` (verified: `git diff main -- gateway/platforms/api_server.py` is
  empty), not introduced by this phase's tests. Likely explanation: the
  test predates the BuJo-delivery feature being added to `send()`, back
  when `send()` really was an unconditional no-op stub (per the
  still-present class-level comment above `APIServerAdapter`). See
  `IMPLEMENTATION_LOG.md` Entry 1 for the full verification, and
  `ROADMAP.md` for why it wasn't fixed here (touching a core gateway test
  file is outside this phase's scope).
