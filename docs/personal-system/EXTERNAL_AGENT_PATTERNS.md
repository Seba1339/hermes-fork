# External Agent Framework Patterns — What Applies Here

> **Audience:** Whoever designs Phase 2+ of the memory/collaboration system.
> **Method:** Each framework is assessed against a concrete gap identified
> in `ARCHITECTURE.md`, not surveyed generically. "Not applicable" is a
> real, common verdict here — Hermes already has a narrow core
> (`AGENTS.md`'s footprint ladder) and most of these frameworks solve
> problems Hermes solves differently on purpose.
> **Last updated:** 2026-08-08

Per the task brief: extract patterns from OpenClaw, OpenHands, Letta,
Browser Use, Goose, OpenAI Agents SDK, Google ADK, and LangGraph only where
they concretely fit. This is not a "should Hermes adopt framework X"
survey — Hermes is not being replaced or wrapped by any of these.

## Letta (formerly MemGPT) — memory tiering

**Concrete fit: high.** Letta's core idea — a fixed-size "core memory"
block always in context, plus a much larger "archival memory" retrieved
on demand via search — is *already* the shape of
`memoria_activa_architecture.md`'s Capa 1-3 proposal (MEMORY.md as the
small always-in-context block; HRR-vectorized facts as the searchable
archive, `prefetch()` as the retrieval-on-demand step). The useful
borrowed detail is Letta's **self-editing memory**: the agent itself can
call a tool to rewrite its core memory block, rather than only a
background cron process updating it. Hermes' existing `memory` toolset
already exposes something similar (agent-callable `fact_store`, per
`memoria_activa_architecture.md`'s note that explicit facts only get saved
"si el modelo llama `fact_store`"). The gap Letta highlights: there's no
tool for the agent to *demote* or *correct* a memory it now knows is
stale, only to add new ones. Worth a narrow addition (a `fact_update` /
`fact_forget` tool) in a later memory phase — not built now (see
`ROADMAP.md`).

## LangGraph — durable, resumable state graphs

**Concrete fit: medium, narrow.** LangGraph's checkpointing (a state graph
that can persist mid-execution and resume after a crash) is conceptually
close to what Hermes' own `checkpoints` config section and cron's
"catchup window" / "grace window" already do for the top-level agent loop
(see AGENTS.md's Cron hardening invariants). Adopting LangGraph itself
would mean running a second orchestration engine alongside Hermes' own
loop — directly against AGENTS.md's "narrow waist" principle and the
"third-party products in the core tree" policy. The one transferable
pattern: LangGraph models cross-domain correlation (Capa 4 of the memory
proposal — salud↔finanzas↔bujo↔sessions) as an explicit graph of typed
nodes with conditional edges, which is a cleaner mental model than four
independent cron scripts each hand-rolling its own correlation logic. If
Capa 4 is built, structuring it as one small in-repo state machine
(plain Python, not the LangGraph library) borrows the idea without the
dependency.

## Goose (Block) — extension/toolkit architecture

**Concrete fit: low, already satisfied.** Goose's headline feature is a
pluggable "extension" system for adding capability without touching core.
Hermes already has this, more granularly, via the footprint ladder
(AGENTS.md): CLI+skill → service-gated tool → plugin → MCP → core tool.
Nothing to import here; if anything, Goose's flatter single-tier plugin
model is *less* expressive than what's already shipped.

## OpenAI Agents SDK / Google ADK — typed handoffs and sub-agent orchestration

**Concrete fit: low for the core loop, medium for `delegate_task` UX.**
Both SDKs formalize "handoff" as a first-class typed object (which agent,
what context, what's returned) between cooperating agents. Hermes'
`delegate_task` (see AGENTS.md's "Delegation" section) already does
single/batch delegation with `role="leaf"/"orchestrator"` and depth
limits. The one pattern worth borrowing conceptually: both SDKs make the
**output contract of a handoff explicit and typed** (a Pydantic/JSON
schema the receiving agent must fill in), which is exactly the shape
`tools/perspective_router.py`'s `output_contract` field
(`["conclusion", "supuestos", "riesgos", "confianza",
"evidencia_necesaria"]`) already independently arrived at for perspective
consultations. No code change suggested — this is confirmation that the
existing design in this repo already matches the pattern these SDKs
formalize, not a gap.

## OpenHands — sandboxed execution + structured task/eval loop

**Concrete fit: medium, for `agent_eval.py` specifically.** OpenHands'
evaluation harness runs an agent against a benchmark and records
structured pass/fail + trace data per task. The personal system's
`~/.hermes/scripts/agent_eval.py` (outside this repo — see
`ARCHITECTURE.md` §7) already does a lightweight version of this: one
JSONL line per (agent, task-category, useful yes/no, notes). The
OpenHands-style refinement worth considering later: bucket by task
category *and* by which perspective/model handled it (the categories in
`agent_eval.py`'s `VALID_TASKS` — `arquitectura`, `clasificacion`,
`critica`, etc. — already line up with `tools/perspective_router.py`'s
`_ROUTE_RULES` categories almost 1:1, which suggests the two were designed
together but aren't currently cross-referenced). A future improvement:
have `perspective_router`'s response include the `agent_eval` task
category directly, so logging which perspective handled a routed task
doesn't require re-deriving the category by hand. Small, deferred to
`ROADMAP.md` — it touches a real file outside this repo.

## Browser Use — not applicable here

Browser Use solves reliable browser automation (DOM grounding, action
retries). Hermes already ships its own `browser_navigate` core tool and
environments backend for this. Nothing in the Agenda/BuJo/memory/
collaboration scope in this phase touches browser automation. No
recommendation.

## OpenClaw — not independently verifiable, no concrete fit found

No pattern from OpenClaw was identified that maps onto a gap found during
this phase's audit (§8 of `ARCHITECTURE.md`). Recorded here rather than
silently dropped, per the task brief's instruction to only extract
patterns "cuando encajen de manera concreta" — this is the case where none
did.

## Summary table

| Framework | Fit | Action this phase | Action recommended later |
|---|---|---|---|
| Letta | High | None | Design a narrow `fact_update`/`fact_forget` tool alongside memory Phase 2 |
| LangGraph | Medium (idea only) | None | If Capa 4 (cross-domain correlation) is built, structure it as an explicit small state machine, not ad-hoc cron scripts — don't add the dependency |
| Goose | Low | None | None — already satisfied by the footprint ladder |
| OpenAI Agents SDK / Google ADK | Low (confirms existing design) | None | None |
| OpenHands | Medium | None | Cross-reference `agent_eval.py` task categories with `perspective_router` categories |
| Browser Use | None | None | None |
| OpenClaw | None found | None | None |
