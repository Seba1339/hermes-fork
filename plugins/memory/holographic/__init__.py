"""hermes-memory-store — holographic memory plugin using MemoryProvider interface.

Registers as a MemoryProvider plugin, giving the agent structured fact storage
with entity resolution, trust scoring, and HRR-based compositional retrieval.

Original plugin by dusterbloom (PR #2351), adapted to the MemoryProvider ABC.

Config in $HERMES_HOME/config.yaml (profile-scoped):
  plugins:
    hermes-memory-store:
      db_path: $HERMES_HOME/memory_store.db   # omit to use the default
      auto_extract: false
      default_trust: 0.5
      min_trust_threshold: 0.3
      temporal_decay_half_life: 0
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List

from agent.memory_provider import MemoryProvider
from tools.registry import tool_error
from .store import MemoryStore, VALID_HANDOFF_STATUSES
from .retrieval import FactRetriever
from hermes_cli.config import cfg_get

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Auto-extraction detection patterns (shared by preview_extracted_facts and
# _auto_extract_facts — see HolographicMemoryProvider below)
# ---------------------------------------------------------------------------

_PREF_PATTERNS = [
    re.compile(r'\bI\s+(?:prefer|like|love|use|want|need)\s+(.+)', re.IGNORECASE),
    re.compile(r'\bmy\s+(?:favorite|preferred|default)\s+\w+\s+is\s+(.+)', re.IGNORECASE),
    re.compile(r'\bI\s+(?:always|never|usually)\s+(.+)', re.IGNORECASE),
]
_DECISION_PATTERNS = [
    re.compile(r'\bwe\s+(?:decided|agreed|chose)\s+(?:to\s+)?(.+)', re.IGNORECASE),
    re.compile(r'\bthe\s+project\s+(?:uses|needs|requires)\s+(.+)', re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# Tool schemas (unchanged from original PR)
# ---------------------------------------------------------------------------

FACT_STORE_SCHEMA = {
    "name": "fact_store",
    "description": (
        "Deep structured memory with algebraic reasoning. "
        "Use alongside the memory tool — memory for always-on context, "
        "fact_store for deep recall and compositional queries.\n\n"
        "ACTIONS (simple → powerful):\n"
        "• add — Store a fact the user would expect you to remember.\n"
        "• search — Keyword lookup ('editor config', 'deploy process').\n"
        "• probe — Entity recall: ALL facts about a person/thing.\n"
        "• related — What connects to an entity? Structural adjacency.\n"
        "• reason — Compositional: facts connected to MULTIPLE entities simultaneously.\n"
        "• contradict — Memory hygiene: find facts making conflicting claims.\n"
        "• list — Browse stored facts.\n\n"
        "To correct or delete an existing fact, use the separate memory_governance "
        "tool instead — every correction/removal there requires an explicit reason "
        "and is written to a local audit trail.\n\n"
        "IMPORTANT: Before answering questions about the user, ALWAYS probe or reason first."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "search", "probe", "related", "reason", "contradict", "list"],
            },
            "content": {"type": "string", "description": "Fact content (required for 'add')."},
            "query": {"type": "string", "description": "Search query (required for 'search')."},
            "entity": {"type": "string", "description": "Entity name for 'probe'/'related'."},
            "entities": {"type": "array", "items": {"type": "string"}, "description": "Entity names for 'reason'."},
            "category": {"type": "string", "enum": ["user_pref", "project", "tool", "general"]},
            "tags": {"type": "string", "description": "Comma-separated tags."},
            "min_trust": {"type": "number", "description": "Minimum trust filter (default: 0.3)."},
            "limit": {"type": "integer", "description": "Max results (default: 10)."},
        },
        "required": ["action"],
    },
}

MEMORY_GOVERNANCE_SCHEMA = {
    "name": "memory_governance",
    "description": (
        "Audited correction/removal of an existing fact_store entry — the ONLY "
        "agent-facing way to change or delete a fact once stored. Every call "
        "requires 'fact_id' and a non-empty 'reason'; the mutation and its "
        "audit row are written atomically to fact_governance_audit, so there "
        "is no way to change or remove a fact without a recorded reason.\n\n"
        "ACTIONS:\n"
        "• update — Correct content/category/trust_score on an existing fact. "
        "Only these three fields may change.\n"
        "• forget — Permanently delete a fact. Also requires confirm_forget=true; "
        "a call without it is rejected before anything is read or written.\n\n"
        "This tool never adds new facts (use fact_store action='add') and "
        "handles exactly one fact_id per call."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["update", "forget"]},
            "fact_id": {"type": "integer", "description": "The existing fact to update or forget. Required."},
            "reason": {
                "type": "string",
                "description": "Mandatory human-readable reason, recorded in fact_governance_audit.",
            },
            "content": {"type": "string", "description": "[update] New content."},
            "category": {
                "type": "string",
                "enum": ["user_pref", "project", "tool", "general"],
                "description": "[update] New category.",
            },
            "trust_score": {
                "type": "number",
                "description": "[update] New absolute trust_score in [0.0, 1.0] (not a delta).",
            },
            "confirm_forget": {
                "type": "boolean",
                "description": "[forget] Must be true to confirm permanent deletion.",
            },
        },
        "required": ["action", "fact_id", "reason"],
    },
}

HANDOFF_SCHEMA = {
    "name": "memory_handoff",
    "description": (
        "Persistent handoffs for work/projects that need to be resumed later "
        "(e.g. by a future session or another agent). Explicit and separate "
        "from fact_store — a handoff is mutable work-in-progress state "
        "(title/status/summary/next_steps/blockers), not a durable fact — "
        "but it is retrievable across sessions.\n\n"
        "ACTIONS:\n"
        "• handoff_create — Start a new resumable handoff (title required).\n"
        "• handoff_get — Fetch one handoff by handoff_id.\n"
        "• handoff_list — Browse handoffs, optionally filtered by status/session_id/owner.\n"
        "• handoff_update — Update status/summary/next_steps/blockers/owner/title "
        "on an existing handoff by handoff_id.\n\n"
        "This tool does NOT create BuJo entries, tasks, or reminders — it only "
        "tracks resumable work state."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["handoff_create", "handoff_get", "handoff_list", "handoff_update"],
            },
            "handoff_id": {
                "type": "integer",
                "description": "Handoff ID (required for handoff_get/handoff_update).",
            },
            "title": {
                "type": "string",
                "description": "Short title (required for handoff_create; optional rename for handoff_update).",
            },
            "status": {
                "type": "string",
                "enum": sorted(VALID_HANDOFF_STATUSES),
                "description": "Handoff status (default 'open' on handoff_create).",
            },
            "summary": {"type": "string", "description": "Current state of the work."},
            "next_steps": {"type": "string", "description": "What to do when resuming."},
            "blockers": {"type": "string", "description": "What's blocking progress, if anything."},
            "owner": {"type": "string", "description": "Optional owner/assignee."},
            "session_id": {
                "type": "string",
                "description": (
                    "Optional session filter for handoff_list, or an explicit "
                    "session to stamp on handoff_create. Defaults to the current "
                    "session on handoff_create if omitted."
                ),
            },
            "limit": {"type": "integer", "description": "Max results for handoff_list (default: 20)."},
        },
        "required": ["action"],
    },
}

FACT_FEEDBACK_SCHEMA = {
    "name": "fact_feedback",
    "description": (
        "Rate a fact after using it. Mark 'helpful' if accurate, 'unhelpful' if outdated. "
        "This trains the memory — good facts rise, bad facts sink."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["helpful", "unhelpful"]},
            "fact_id": {"type": "integer", "description": "The fact ID to rate."},
        },
        "required": ["action", "fact_id"],
    },
}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _load_plugin_config() -> dict:
    from hermes_constants import get_hermes_home
    config_path = get_hermes_home() / "config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml
        with open(config_path, encoding="utf-8-sig") as f:
            all_config = yaml.safe_load(f) or {}
        return cfg_get(all_config, "plugins", "hermes-memory-store", default={}) or {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# MemoryProvider implementation
# ---------------------------------------------------------------------------

class HolographicMemoryProvider(MemoryProvider):
    """Holographic memory with structured facts, entity resolution, and HRR retrieval."""

    def __init__(self, config: dict | None = None):
        self._config = config or _load_plugin_config()
        self._store = None
        self._retriever = None
        self._session_id: "str | None" = None
        self._min_trust = float(self._config.get("min_trust_threshold", 0.3))

    @property
    def name(self) -> str:
        return "holographic"

    def is_available(self) -> bool:
        return True  # SQLite is always available, numpy is optional

    def save_config(self, values, hermes_home):
        """Write config to config.yaml under plugins.hermes-memory-store."""
        from pathlib import Path
        config_path = Path(hermes_home) / "config.yaml"
        try:
            import yaml
            existing = {}
            if config_path.exists():
                with open(config_path, encoding="utf-8-sig") as f:
                    existing = yaml.safe_load(f) or {}
            existing.setdefault("plugins", {})
            existing["plugins"]["hermes-memory-store"] = values
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(existing, f, default_flow_style=False)
        except Exception:
            pass

    def get_config_schema(self):
        from hermes_constants import display_hermes_home
        _default_db = f"{display_hermes_home()}/memory_store.db"
        return [
            {"key": "db_path", "description": "SQLite database path", "default": _default_db},
            {"key": "auto_extract", "description": "Auto-extract facts at session end", "default": "false", "choices": ["true", "false"]},
            {"key": "default_trust", "description": "Default trust score for new facts", "default": "0.5"},
            {"key": "hrr_dim", "description": "HRR vector dimensions", "default": "1024"},
        ]

    def initialize(self, session_id: str, **kwargs) -> None:
        from hermes_constants import get_hermes_home
        _hermes_home = str(get_hermes_home())
        _default_db = _hermes_home + "/memory_store.db"
        db_path = self._config.get("db_path", _default_db)
        # Expand $HERMES_HOME in user-supplied paths so config values like
        # "$HERMES_HOME/memory_store.db" or "~/.hermes/memory_store.db" both
        # resolve to the active profile's directory.
        if isinstance(db_path, str):
            db_path = db_path.replace("$HERMES_HOME", _hermes_home)
            db_path = db_path.replace("${HERMES_HOME}", _hermes_home)
        default_trust = float(self._config.get("default_trust", 0.5))
        hrr_dim = int(self._config.get("hrr_dim", 1024))
        hrr_weight = float(self._config.get("hrr_weight", 0.3))
        temporal_decay = int(self._config.get("temporal_decay_half_life", 0))

        self._store = MemoryStore(db_path=db_path, default_trust=default_trust, hrr_dim=hrr_dim)
        self._retriever = FactRetriever(
            store=self._store,
            temporal_decay_half_life=temporal_decay,
            hrr_weight=hrr_weight,
            hrr_dim=hrr_dim,
        )
        self._session_id = session_id

    def system_prompt_block(self) -> str:
        if not self._store:
            return ""
        try:
            total = self._store._conn.execute(
                "SELECT COUNT(*) FROM facts"
            ).fetchone()[0]
        except Exception:
            total = 0
        if total == 0:
            return (
                "# Holographic Memory\n"
                "Active. Empty fact store — proactively add facts the user would expect you to remember.\n"
                "Use fact_store(action='add') to store durable structured facts about people, projects, preferences, decisions.\n"
                "Use fact_feedback to rate facts after using them (trains trust scores)."
            )
        return (
            f"# Holographic Memory\n"
            f"Active. {total} facts stored with entity resolution and trust scoring.\n"
            f"Use fact_store to search, probe entities, reason across entities, or add facts.\n"
            f"Use fact_feedback to rate facts after using them (trains trust scores)."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if not self._retriever or not query:
            return ""
        try:
            results = self._retriever.search(query, min_trust=self._min_trust, limit=5)
            if not results:
                return ""
            lines = []
            for r in results:
                trust = r.get("trust_score", r.get("trust", 0))
                lines.append(f"- [{trust:.1f}] {r.get('content', '')}")
            return "## Holographic Memory\n" + "\n".join(lines)
        except Exception as e:
            logger.debug("Holographic prefetch failed: %s", e)
            return ""

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        # Holographic memory stores explicit facts via tools, not auto-sync.
        # The on_session_end hook handles auto-extraction if configured.
        pass

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [FACT_STORE_SCHEMA, FACT_FEEDBACK_SCHEMA, HANDOFF_SCHEMA, MEMORY_GOVERNANCE_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if tool_name == "fact_store":
            return self._handle_fact_store(args)
        elif tool_name == "fact_feedback":
            return self._handle_fact_feedback(args)
        elif tool_name == "memory_handoff":
            return self._handle_memory_handoff(args)
        elif tool_name == "memory_governance":
            return self._handle_memory_governance(args)
        return tool_error(f"Unknown tool: {tool_name}")

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        if not self._config.get("auto_extract", False):
            return
        if not self._store or not messages:
            return
        self._auto_extract_facts(messages)

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        """Mirror built-in memory writes as facts."""
        if action == "add" and self._store and content:
            try:
                category = "user_pref" if target == "user" else "general"
                self._store.add_fact(content, category=category)
            except Exception as e:
                logger.debug("Holographic memory_write mirror failed: %s", e)

    def shutdown(self) -> None:
        # Release the shared SQLite connection deterministically on the
        # caller's thread. Dropping the reference alone leaves fd finalization
        # to GC, which keeps the connection (and its write lock) alive on a
        # long-running gateway and prolongs the "database is locked" contention
        # this store's shared-connection refcounting is meant to eliminate.
        # close() is idempotent and refcount-guarded, so siblings stay safe.
        if self._store is not None:
            try:
                self._store.close()
            except Exception as e:
                logger.debug("Holographic shutdown close() failed: %s", e)
        self._store = None
        self._retriever = None

    # -- Tool handlers -------------------------------------------------------

    def _handle_fact_store(self, args: dict) -> str:
        try:
            action = args["action"]
            store = self._store
            retriever = self._retriever

            if action == "add":
                fact_id = store.add_fact(
                    args["content"],
                    category=args.get("category", "general"),
                    tags=args.get("tags", ""),
                )
                return json.dumps({"fact_id": fact_id, "status": "added"})

            elif action == "search":
                results = retriever.search(
                    args["query"],
                    category=args.get("category"),
                    min_trust=float(args.get("min_trust", self._min_trust)),
                    limit=int(args.get("limit", 10)),
                )
                return json.dumps({"results": results, "count": len(results)})

            elif action == "probe":
                results = retriever.probe(
                    args["entity"],
                    category=args.get("category"),
                    limit=int(args.get("limit", 10)),
                )
                return json.dumps({"results": results, "count": len(results)})

            elif action == "related":
                results = retriever.related(
                    args["entity"],
                    category=args.get("category"),
                    limit=int(args.get("limit", 10)),
                )
                return json.dumps({"results": results, "count": len(results)})

            elif action == "reason":
                entities = args.get("entities", [])
                if not entities:
                    return tool_error("reason requires 'entities' list")
                results = retriever.reason(
                    entities,
                    category=args.get("category"),
                    limit=int(args.get("limit", 10)),
                )
                return json.dumps({"results": results, "count": len(results)})

            elif action == "contradict":
                results = retriever.contradict(
                    category=args.get("category"),
                    limit=int(args.get("limit", 10)),
                )
                return json.dumps({"results": results, "count": len(results)})

            elif action in ("update", "remove"):
                return tool_error(
                    f"action '{action}' is not available on fact_store; use the "
                    "memory_governance tool instead (requires fact_id + reason; "
                    "'forget' also requires confirm_forget=true)."
                )

            elif action == "list":
                facts = store.list_facts(
                    category=args.get("category"),
                    min_trust=float(args.get("min_trust", 0.0)),
                    limit=int(args.get("limit", 10)),
                )
                return json.dumps({"facts": facts, "count": len(facts)})

            else:
                return tool_error(f"Unknown action: {action}")

        except KeyError as exc:
            return tool_error(f"Missing required argument: {exc}")
        except Exception as exc:
            return tool_error(str(exc))

    def _handle_fact_feedback(self, args: dict) -> str:
        try:
            fact_id = int(args["fact_id"])
            helpful = args["action"] == "helpful"
            result = self._store.record_feedback(fact_id, helpful=helpful)
            return json.dumps(result)
        except KeyError as exc:
            return tool_error(f"Missing required argument: {exc}")
        except Exception as exc:
            return tool_error(str(exc))

    def _handle_memory_handoff(self, args: dict) -> str:
        try:
            action = args["action"]
            store = self._store
            if store is None:
                return tool_error("memory store not initialized")

            if action == "handoff_create":
                handoff_id = store.create_handoff(
                    args["title"],
                    status=args.get("status", "open"),
                    summary=args.get("summary", ""),
                    next_steps=args.get("next_steps", ""),
                    blockers=args.get("blockers", ""),
                    owner=args.get("owner"),
                    session_id=args.get("session_id") or self._session_id,
                )
                return json.dumps({"handoff_id": handoff_id, "status": "created"})

            elif action == "handoff_get":
                handoff_id = int(args["handoff_id"])
                handoff = store.get_handoff(handoff_id)
                if handoff is None:
                    return tool_error(f"handoff_id {handoff_id} not found")
                return json.dumps({"handoff": handoff})

            elif action == "handoff_list":
                handoffs = store.list_handoffs(
                    status=args.get("status"),
                    session_id=args.get("session_id"),
                    owner=args.get("owner"),
                    limit=int(args.get("limit", 20)),
                )
                return json.dumps({"handoffs": handoffs, "count": len(handoffs)})

            elif action == "handoff_update":
                handoff_id = int(args["handoff_id"])
                updated = store.update_handoff(
                    handoff_id,
                    title=args.get("title"),
                    status=args.get("status"),
                    summary=args.get("summary"),
                    next_steps=args.get("next_steps"),
                    blockers=args.get("blockers"),
                    owner=args.get("owner"),
                )
                if updated is None:
                    return tool_error(f"handoff_id {handoff_id} not found")
                return json.dumps({"handoff": updated})

            else:
                return tool_error(f"Unknown action: {action}")

        except KeyError as exc:
            return tool_error(f"Missing required argument: {exc}")
        except ValueError as exc:
            return tool_error(str(exc))
        except Exception as exc:
            return tool_error(str(exc))

    def _handle_memory_governance(self, args: dict) -> str:
        """Sole agent-facing route to `update_fact_audited`/`forget_fact_audited`.

        Deliberately separate from `_handle_fact_store`: this is the only path
        by which the agent can mutate or delete an existing fact, and it never
        falls back to the unaudited `store.update_fact`/`store.remove_fact`
        (those stay as internal APIs for scripts/memory_migrate.py and callers
        that don't need a reason, but are not reachable from this tool).
        """
        try:
            action = args["action"]
            fact_id = int(args["fact_id"])
            reason = args["reason"]
        except KeyError as exc:
            return tool_error(f"Missing required argument: {exc}")

        reason = (reason or "").strip()
        if not reason:
            return tool_error("reason must not be empty")

        store = self._store
        if store is None:
            return tool_error("memory store not initialized")

        try:
            if action == "update":
                result = store.update_fact_audited(
                    fact_id,
                    reason=reason,
                    content=args.get("content"),
                    category=args.get("category"),
                    trust_score=args.get("trust_score"),
                    session_id=self._session_id,
                )
                return json.dumps(result)

            elif action == "forget":
                if not args.get("confirm_forget"):
                    return tool_error(
                        "forget requires confirm_forget=true to confirm permanent deletion"
                    )
                result = store.forget_fact_audited(
                    fact_id, reason=reason, session_id=self._session_id
                )
                return json.dumps(result)

            else:
                return tool_error(f"Unknown action: {action}")

        except KeyError as exc:
            return tool_error(str(exc))
        except ValueError as exc:
            return tool_error(str(exc))
        except Exception as exc:
            return tool_error(str(exc))

    # -- Auto-extraction (on_session_end) ------------------------------------

    def preview_extracted_facts(self, messages: list) -> List[Dict[str, Any]]:
        """Detect candidate facts in `messages` without storing anything.

        Pure function of (self._session_id, messages): no SQLite access, no
        calls to `add_fact`, no other side effects. Applies the same rules
        `_auto_extract_facts` persists — `role == "user"` only, skip content
        starting with `"[IMPORTANT:"`, preference/decision regex detection —
        so a caller can inspect what auto-extraction *would* store before
        `auto_extract` is ever turned on.

        Returns a list of dicts, each with the same keys `_auto_extract_facts`
        passes to `add_fact`: `content`, `category`, `fact_type="extracted"`,
        `session_id`.
        """
        candidates: List[Dict[str, Any]] = []
        for msg in messages:
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            if not isinstance(content, str) or len(content) < 10:
                continue
            if content.startswith("[IMPORTANT:"):
                continue

            for pattern in _PREF_PATTERNS:
                if pattern.search(content):
                    candidates.append({
                        "content": content[:400],
                        "category": "user_pref",
                        "fact_type": "extracted",
                        "session_id": self._session_id,
                    })
                    break

            for pattern in _DECISION_PATTERNS:
                if pattern.search(content):
                    candidates.append({
                        "content": content[:400],
                        "category": "project",
                        "fact_type": "extracted",
                        "session_id": self._session_id,
                    })
                    break

        return candidates

    def _auto_extract_facts(self, messages: list) -> None:
        extracted = 0
        for candidate in self.preview_extracted_facts(messages):
            try:
                self._store.add_fact(
                    candidate["content"],
                    category=candidate["category"],
                    session_id=candidate["session_id"],
                    fact_type=candidate["fact_type"],
                )
                extracted += 1
            except Exception:
                pass

        if extracted:
            logger.info("Auto-extracted %d facts from conversation", extracted)


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    """Register the holographic memory provider with the plugin system."""
    config = _load_plugin_config()
    provider = HolographicMemoryProvider(config=config)
    ctx.register_memory_provider(provider)
