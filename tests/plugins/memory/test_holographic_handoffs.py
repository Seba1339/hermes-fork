"""Functional tests for persistent work handoffs."""

import json
import sqlite3

import pytest

from plugins.memory.holographic import HolographicMemoryProvider
from plugins.memory.holographic.store import MemoryStore, VALID_HANDOFF_STATUSES


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


def make_store(tmp_path):
    return MemoryStore(db_path=str(tmp_path / "memory.db"), hrr_dim=16)


def test_handoff_schema_is_additive_and_crud_is_stable(tmp_path):
    store = make_store(tmp_path)
    try:
        first = store.create_handoff(
            "Repair tractor 12",
            status="open",
            summary="Diagnostics started",
            next_steps="Check hydraulic pressure",
            blockers="",
            owner="mechanic-a",
            session_id="session-1",
        )
        second = store.create_handoff("Repair tractor 12", session_id="session-2")
        assert first != second
        row = store.get_handoff(first)
        assert row["handoff_id"] == first
        assert row["status"] == "open"
        assert row["session_id"] == "session-1"
        assert store.list_handoffs(status="open", owner="mechanic-a")[0]["handoff_id"] == first

        updated = store.update_handoff(first, status="in_progress", next_steps="Measure pressure")
        assert updated["handoff_id"] == first
        assert updated["status"] == "in_progress"
        assert updated["next_steps"] == "Measure pressure"
        assert updated["summary"] == "Diagnostics started"
        assert store.get_handoff(first)["handoff_id"] == first
    finally:
        store.close()


def test_handoff_validation_and_missing_ids(tmp_path):
    store = make_store(tmp_path)
    try:
        with pytest.raises(ValueError):
            store.create_handoff("", status="open")
        with pytest.raises(ValueError):
            store.create_handoff("x", status="unknown")
        with pytest.raises(ValueError):
            store.list_handoffs(status="unknown")
        with pytest.raises(ValueError):
            store.update_handoff(999, title="")
        assert store.get_handoff(999) is None
        assert store.update_handoff(999, summary="missing") is None
        assert VALID_HANDOFF_STATUSES == {"open", "in_progress", "blocked", "done", "abandoned"}
    finally:
        store.close()


def test_provider_handoff_tool_is_retrievable_and_separate_from_facts(tmp_path):
    provider = HolographicMemoryProvider({"db_path": str(tmp_path / "memory.db"), "hrr_dim": 16})
    provider.initialize(session_id="provider-session")
    try:
        schema_names = {schema["name"] for schema in provider.get_tool_schemas()}
        assert "memory_handoff" in schema_names
        created = json.loads(provider.handle_tool_call("memory_handoff", {
            "action": "handoff_create",
            "title": "Resume field inspection",
            "summary": "Unit is stopped pending inspection",
            "next_steps": "Inspect fuel system",
            "blockers": "Missing filter",
        }))
        handoff_id = created["handoff_id"]
        fetched = json.loads(provider.handle_tool_call("memory_handoff", {
            "action": "handoff_get", "handoff_id": handoff_id,
        }))
        assert fetched["handoff"]["session_id"] == "provider-session"
        listed = json.loads(provider.handle_tool_call("memory_handoff", {
            "action": "handoff_list", "status": "open",
        }))
        assert listed["count"] == 1
        updated = json.loads(provider.handle_tool_call("memory_handoff", {
            "action": "handoff_update", "handoff_id": handoff_id, "status": "blocked",
        }))
        assert updated["handoff"]["status"] == "blocked"
        assert provider._store._conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 0
    finally:
        provider.shutdown()
