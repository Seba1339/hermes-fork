"""Tests for the agent-facing `memory_governance` tool.

Covers the wiring added on top of the store-level `update_fact_audited`/
`forget_fact_audited` methods (see test_holographic_governance.py for the
store-level tests): the `memory_governance` schema/handler in
`plugins/memory/holographic/__init__.py`, and the removal of `update`/
`remove` from the agent-facing `fact_store` schema so a fact can no longer
be mutated without a `reason` landing in `fact_governance_audit`.

All databases are built under `tmp_path`; nothing under `~/.hermes` or
`~/.hermes-enhanced` is read or written. No auto-extraction, no config,
no cron/systemd/service interaction.
"""

import json
import sqlite3

import pytest

from plugins.memory.holographic import (
    FACT_STORE_SCHEMA,
    MEMORY_GOVERNANCE_SCHEMA,
    HolographicMemoryProvider,
)
from plugins.memory.holographic.store import MemoryStore

_AUDIT_TABLE = "fact_governance_audit"


@pytest.fixture(autouse=True)
def clean_shared_registry():
    MemoryStore._shared.clear()
    yield
    for entry in list(MemoryStore._shared.values()):
        try:
            entry["conn"].close()
        except sqlite3.Error:
            pass
    MemoryStore._shared.clear()


def make_provider(tmp_path, session_id="governance-session"):
    provider = HolographicMemoryProvider({"db_path": str(tmp_path / "memory.db"), "hrr_dim": 16})
    provider.initialize(session_id=session_id)
    return provider


def audit_rows(provider, fact_id=None):
    conn = provider._store._conn
    if fact_id is None:
        return conn.execute(f"SELECT * FROM {_AUDIT_TABLE}").fetchall()
    return conn.execute(
        f"SELECT * FROM {_AUDIT_TABLE} WHERE fact_id = ?", (fact_id,)
    ).fetchall()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def test_fact_store_schema_no_longer_exposes_update_or_remove():
    action_enum = FACT_STORE_SCHEMA["parameters"]["properties"]["action"]["enum"]
    assert "update" not in action_enum
    assert "remove" not in action_enum
    # Untouched actions remain available.
    assert set(action_enum) == {
        "add", "search", "probe", "related", "reason", "contradict", "list",
    }


def test_memory_governance_schema_shape():
    assert MEMORY_GOVERNANCE_SCHEMA["name"] == "memory_governance"
    props = MEMORY_GOVERNANCE_SCHEMA["parameters"]["properties"]
    assert set(props["action"]["enum"]) == {"update", "forget"}
    assert MEMORY_GOVERNANCE_SCHEMA["parameters"]["required"] == ["action", "fact_id", "reason"]
    assert "confirm_forget" in props
    assert "content" in props and "category" in props and "trust_score" in props


def test_provider_exposes_memory_governance_tool_schema(tmp_path):
    provider = make_provider(tmp_path)
    try:
        schema_names = {schema["name"] for schema in provider.get_tool_schemas()}
        assert "memory_governance" in schema_names
        assert "fact_store" in schema_names
    finally:
        provider.shutdown()


# ---------------------------------------------------------------------------
# Legacy fact_store update/remove actions are rejected, not silently routed
# ---------------------------------------------------------------------------

def test_fact_store_update_action_is_rejected_not_routed(tmp_path):
    provider = make_provider(tmp_path)
    try:
        fact_id = json.loads(provider.handle_tool_call("fact_store", {
            "action": "add", "content": "Legacy path test fact",
        }))["fact_id"]

        result = json.loads(provider.handle_tool_call("fact_store", {
            "action": "update", "fact_id": fact_id, "content": "Mutated without audit",
        }))
        assert "error" in result
        assert "memory_governance" in result["error"]

        # Nothing changed, nothing audited.
        row = provider._store._conn.execute(
            "SELECT content FROM facts WHERE fact_id = ?", (fact_id,)
        ).fetchone()
        assert row["content"] == "Legacy path test fact"
        assert audit_rows(provider, fact_id) == []
    finally:
        provider.shutdown()


def test_fact_store_remove_action_is_rejected_not_routed(tmp_path):
    provider = make_provider(tmp_path)
    try:
        fact_id = json.loads(provider.handle_tool_call("fact_store", {
            "action": "add", "content": "Legacy remove test fact",
        }))["fact_id"]

        result = json.loads(provider.handle_tool_call("fact_store", {
            "action": "remove", "fact_id": fact_id,
        }))
        assert "error" in result
        assert "memory_governance" in result["error"]

        count = provider._store._conn.execute(
            "SELECT COUNT(*) FROM facts WHERE fact_id = ?", (fact_id,)
        ).fetchone()[0]
        assert count == 1
        assert audit_rows(provider, fact_id) == []
    finally:
        provider.shutdown()


# ---------------------------------------------------------------------------
# memory_governance: update
# ---------------------------------------------------------------------------

def test_memory_governance_update_audits_and_returns_changed_fields(tmp_path):
    provider = make_provider(tmp_path)
    try:
        fact_id = json.loads(provider.handle_tool_call("fact_store", {
            "action": "add", "content": "Original content", "category": "general",
        }))["fact_id"]

        result = json.loads(provider.handle_tool_call("memory_governance", {
            "action": "update",
            "fact_id": fact_id,
            "reason": "correcting a typo",
            "content": "Corrected content",
            "trust_score": 0.9,
        }))

        assert result["fact_id"] == fact_id
        assert result["action"] == "update"
        assert result["noop"] is False
        assert set(result["changed_fields"]) == {"content", "trust_score"}
        assert result["new"]["content"] == "Corrected content"
        assert result["new"]["trust_score"] == 0.9
        assert result["old"]["content"] == "Original content"

        row = provider._store._conn.execute(
            "SELECT content, trust_score FROM facts WHERE fact_id = ?", (fact_id,)
        ).fetchone()
        assert row["content"] == "Corrected content"
        assert row["trust_score"] == 0.9

        rows = audit_rows(provider, fact_id)
        assert len(rows) == 1
        assert rows[0]["action"] == "update"
        assert rows[0]["reason"] == "correcting a typo"
        assert rows[0]["old_content"] == "Original content"
        assert rows[0]["new_content"] == "Corrected content"
        assert rows[0]["session_id"] == "governance-session"
    finally:
        provider.shutdown()


def test_memory_governance_update_only_allows_content_category_trust_score(tmp_path):
    provider = make_provider(tmp_path)
    try:
        fact_id = json.loads(provider.handle_tool_call("fact_store", {
            "action": "add", "content": "Category swap fact", "category": "general",
        }))["fact_id"]

        result = json.loads(provider.handle_tool_call("memory_governance", {
            "action": "update",
            "fact_id": fact_id,
            "reason": "reclassify",
            "category": "project",
        }))
        assert result["changed_fields"] == ["category"]
        assert result["new"]["category"] == "project"
    finally:
        provider.shutdown()


def test_memory_governance_update_requires_reason(tmp_path):
    provider = make_provider(tmp_path)
    try:
        fact_id = json.loads(provider.handle_tool_call("fact_store", {
            "action": "add", "content": "Needs a reason to update",
        }))["fact_id"]

        result = json.loads(provider.handle_tool_call("memory_governance", {
            "action": "update", "fact_id": fact_id, "content": "No reason given",
        }))
        assert "error" in result

        row = provider._store._conn.execute(
            "SELECT content FROM facts WHERE fact_id = ?", (fact_id,)
        ).fetchone()
        assert row["content"] == "Needs a reason to update"
        assert audit_rows(provider, fact_id) == []
    finally:
        provider.shutdown()


def test_memory_governance_update_rejects_blank_reason(tmp_path):
    provider = make_provider(tmp_path)
    try:
        fact_id = json.loads(provider.handle_tool_call("fact_store", {
            "action": "add", "content": "Blank reason fact",
        }))["fact_id"]

        result = json.loads(provider.handle_tool_call("memory_governance", {
            "action": "update", "fact_id": fact_id, "reason": "   ", "content": "x",
        }))
        assert "error" in result
        assert audit_rows(provider, fact_id) == []
    finally:
        provider.shutdown()


def test_memory_governance_update_requires_fact_id(tmp_path):
    provider = make_provider(tmp_path)
    try:
        result = json.loads(provider.handle_tool_call("memory_governance", {
            "action": "update", "reason": "no fact id given", "content": "x",
        }))
        assert "error" in result
    finally:
        provider.shutdown()


def test_memory_governance_update_unknown_fact_id_errors(tmp_path):
    provider = make_provider(tmp_path)
    try:
        result = json.loads(provider.handle_tool_call("memory_governance", {
            "action": "update", "fact_id": 999, "reason": "does not exist", "content": "x",
        }))
        assert "error" in result
        assert audit_rows(provider) == []
    finally:
        provider.shutdown()


# ---------------------------------------------------------------------------
# memory_governance: forget
# ---------------------------------------------------------------------------

def test_memory_governance_forget_requires_confirm_forget(tmp_path):
    provider = make_provider(tmp_path)
    try:
        fact_id = json.loads(provider.handle_tool_call("fact_store", {
            "action": "add", "content": "Do not forget me yet",
        }))["fact_id"]

        result = json.loads(provider.handle_tool_call("memory_governance", {
            "action": "forget", "fact_id": fact_id, "reason": "obsolete",
        }))
        assert "error" in result
        assert "confirm_forget" in result["error"]

        count = provider._store._conn.execute(
            "SELECT COUNT(*) FROM facts WHERE fact_id = ?", (fact_id,)
        ).fetchone()[0]
        assert count == 1
        assert audit_rows(provider, fact_id) == []
    finally:
        provider.shutdown()


def test_memory_governance_forget_audits_and_deletes(tmp_path):
    provider = make_provider(tmp_path)
    try:
        fact_id = json.loads(provider.handle_tool_call("fact_store", {
            "action": "add", "content": "Forget this fact", "category": "general",
        }))["fact_id"]

        result = json.loads(provider.handle_tool_call("memory_governance", {
            "action": "forget",
            "fact_id": fact_id,
            "reason": "user retracted this",
            "confirm_forget": True,
        }))
        assert result["fact_id"] == fact_id
        assert result["action"] == "forget"
        assert result["removed"] is True
        assert result["old"]["content"] == "Forget this fact"

        count = provider._store._conn.execute(
            "SELECT COUNT(*) FROM facts WHERE fact_id = ?", (fact_id,)
        ).fetchone()[0]
        assert count == 0

        rows = audit_rows(provider, fact_id)
        assert len(rows) == 1
        assert rows[0]["action"] == "forget"
        assert rows[0]["reason"] == "user retracted this"
        assert rows[0]["old_content"] == "Forget this fact"
        assert rows[0]["new_content"] is None
        assert rows[0]["session_id"] == "governance-session"
    finally:
        provider.shutdown()


def test_memory_governance_forget_requires_reason(tmp_path):
    provider = make_provider(tmp_path)
    try:
        fact_id = json.loads(provider.handle_tool_call("fact_store", {
            "action": "add", "content": "Needs reason to forget",
        }))["fact_id"]

        result = json.loads(provider.handle_tool_call("memory_governance", {
            "action": "forget", "fact_id": fact_id, "confirm_forget": True,
        }))
        assert "error" in result

        count = provider._store._conn.execute(
            "SELECT COUNT(*) FROM facts WHERE fact_id = ?", (fact_id,)
        ).fetchone()[0]
        assert count == 1
        assert audit_rows(provider, fact_id) == []
    finally:
        provider.shutdown()


def test_memory_governance_unknown_action_errors(tmp_path):
    provider = make_provider(tmp_path)
    try:
        result = json.loads(provider.handle_tool_call("memory_governance", {
            "action": "delete", "fact_id": 1, "reason": "typo'd action name",
        }))
        assert "error" in result
    finally:
        provider.shutdown()
