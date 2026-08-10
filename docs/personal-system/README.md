# Personal System — Agenda / BuJo / Memory / Collaboration

> **Audience:** Whoever (human or agent) continues this work in a future session.
> **Scope:** The personal deployment layered on top of this Hermes fork — the
> "Enhanced" gateway, the BuJo journal, the perspective/model routing tools,
> and the proposed unified memory system.
> **Status:** Phase 1 — foundation (documentation, invariant tests,
> no behavior changes). See [IMPLEMENTATION_LOG.md](IMPLEMENTATION_LOG.md)
> for exactly what changed and when.
> **Last updated:** 2026-08-08

## Why this directory exists

This fork runs a personal AI agent system ("Luna") on top of upstream Hermes:
a bullet journal (BuJo), health/finance tracking, a deterministic
multi-perspective advisor (Claude/Gemini/DeepSeek), scheduled cron jobs, and
(proposed, not yet built) a unified vectorized memory layer. Most of that
lives **outside** this repo, in the live `HERMES_HOME` directories
(`~/.hermes`, `~/.hermes-enhanced`) and in standalone scripts
(`~/.hermes/scripts/*.py`). This repo (`hermes-fork`) is where the code that
*ships* — the gateway adapter changes, the model tools, the skill router —
actually lives and gets tested.

Before this phase, that split was undocumented and easy to get wrong: a
2026-07-15 refactor (`e8a0feeef`, "purge dead Enhanced modules") deleted
`enhanced_init.py`, the live entry point for the `hermes-enhanced-gateway`
systemd service, because it looked like dead code from inside the repo. It
had to be restored the same day (`b49b378aa`). **The lesson this directory
is built to prevent repeating:** nothing under `hermes_enhanced/` or
referenced by `enhanced_init.py` is dead code just because it has no
in-repo caller — it's invoked by a live systemd service. See
[ARCHITECTURE.md](ARCHITECTURE.md) for the full map of what's real.

## Documents in this directory

| Document | Purpose |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | What actually runs today: services, entry points, the BuJo/telemetry/memory/session/knowledge boundary, and the audit findings from Phase 1 (TODOs, dead code, inconsistencies found — not yet fixed). |
| [EXTERNAL_AGENT_PATTERNS.md](EXTERNAL_AGENT_PATTERNS.md) | Benchmark of OpenClaw, OpenHands, Letta, Browser Use, Goose, OpenAI Agents SDK, Google ADK, and LangGraph — which patterns are concretely applicable here and which are not, and why. |
| [ROADMAP.md](ROADMAP.md) | Phased plan for the memory/collaboration system, building on the existing `memoria_activa_architecture.md` proposal. Phase 1 (this phase) is documentation + tests only. |
| [IMPLEMENTATION_LOG.md](IMPLEMENTATION_LOG.md) | Append-only log: what changed, why, how it was verified, and how to roll it back. One entry per phase. |

## Related documents outside this directory

- **`/home/ubuntu/hermes-fork/memoria_activa_architecture.md`** (repo root,
  pre-existing) — the detailed "Memoria Activa" proposal: a 5-layer memory
  architecture (extraction → storage → proactive retrieval → cross-domain
  correlation → lifecycle) built on the existing holographic memory plugin.
  `ROADMAP.md` treats this as the source of truth for the memory system's
  design and does not duplicate it.
- **`AGENTS.md`** (repo root) — the fork's general contribution rules:
  prompt-caching safety, the narrow-core/wide-edges philosophy, the
  footprint ladder for new capability, and the testing standards this
  phase's tests follow (behavior contracts, no source-reading tests, no
  change-detectors).

## Ground rules this phase followed (and future phases should too)

1. **Nothing outside this repo was modified.** `~/.hermes`, `~/.hermes-enhanced`,
   real SQLite databases, `jobs.json`, systemd unit files, and `.env` are
   read-only inputs to this documentation — never write targets.
2. **No core-invasive changes.** No edits to `run_agent.py`, `cli.py`,
   `gateway/run.py`, or the message loop. Everything added is either
   documentation or a new test file exercising existing, already-shipped
   behavior.
3. **Tests use temp `HERMES_HOME`.** Every new test relies on this repo's
   `_isolate_hermes_home` autouse fixture (`tests/conftest.py`) or an
   explicit `monkeypatch`, never the real `~/.hermes*` trees.
4. **No speculative infrastructure.** Where a real gap exists but building
   it now would be premature (e.g., the unified memory store), the gap is
   documented with a recommendation in `ROADMAP.md`, not scaffolded.
