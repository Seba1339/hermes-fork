"""End-to-end contract tests for the session-end auto-extraction cycle.

Unlike ``test_holographic_extraction_metadata.py`` / ``_extraction_dry_run.py``
(which pass ``config={...}`` straight into the constructor and mostly call
``_auto_extract_facts`` directly), these tests exercise the actual production
wiring: a real ``config.yaml`` written to a temp ``HERMES_HOME``, loaded by
``HolographicMemoryProvider``'s own ``_load_plugin_config()``, fanned out
through ``MemoryManager.on_session_end`` (the same entry point
``AIAgent.shutdown_memory_provider``/``commit_memory_session`` and the
gateway's ``_cleanup_agent_resources``/``_commit_memory_before_soft_evict``
call in production), against a mixed-role transcript.

This closes the gap flagged in ``docs/personal-system/ROADMAP.md``'s
2026-08-09 update: prior tests proved the detection rules and the provenance
plumbing in isolation, but nothing exercised the disk-config-load → manager
fan-out → gated persistence path together, including repeat-session dedup and
the true empty-transcript no-write case through the ``auto_extract=True``
gate (as opposed to the already-covered ``auto_extract=False`` no-op).

No file under ``~/.hermes`` or ``~/.hermes-enhanced`` is read or written —
``HERMES_HOME`` is the per-test tempdir the ``_hermetic_environment`` autouse
fixture in ``tests/conftest.py`` already points at.
"""

from __future__ import annotations

import sqlite3

import pytest
import yaml

from agent.memory_manager import MemoryManager
from hermes_constants import get_hermes_home
from plugins.memory.holographic import HolographicMemoryProvider
from plugins.memory.holographic.store import MemoryStore


@pytest.fixture(autouse=True)
def _clean_shared_registry():
    """Each test starts and ends with an empty shared-connection registry.

    MemoryStore keys its process-wide shared connection by resolved db path;
    without this, a leaked connection from one test could make a later test
    silently reuse a stale schema/connection instead of opening its own
    tmp_path database.
    """
    for entry in list(MemoryStore._shared.values()):
        try:
            entry["conn"].close()
        except sqlite3.Error:
            pass
    MemoryStore._shared.clear()
    yield
    leaked = list(MemoryStore._shared)
    for entry in list(MemoryStore._shared.values()):
        try:
            entry["conn"].close()
        except sqlite3.Error:
            pass
    MemoryStore._shared.clear()
    assert not leaked, f"test leaked shared connections: {leaked}"


def _write_plugin_config(auto_extract: bool) -> None:
    """Write a real config.yaml under the test's HERMES_HOME.

    Mirrors exactly what a user's ``~/.hermes-enhanced/config.yaml`` looks
    like for this plugin (see ``HolographicMemoryProvider``'s module
    docstring) — this is the file ``_load_plugin_config()`` reads in
    production, not a config dict handed straight to the constructor.
    """
    hermes_home = get_hermes_home()
    config_path = hermes_home / "config.yaml"
    config = {
        "plugins": {
            "hermes-memory-store": {
                "db_path": "$HERMES_HOME/memory_store.db",
                "auto_extract": auto_extract,
                "hrr_dim": 64,  # small dim keeps HRR vector math fast in tests
            }
        }
    }
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False)


def _make_manager_with_disk_provider(session_id: str) -> MemoryManager:
    """Build a MemoryManager wired to a provider whose config comes from disk.

    No config dict is passed to the constructor — this forces
    ``HolographicMemoryProvider.__init__`` to call ``_load_plugin_config()``,
    which reads ``$HERMES_HOME/config.yaml`` exactly as production does.
    """
    provider = HolographicMemoryProvider()
    provider.initialize(session_id=session_id)
    manager = MemoryManager()
    manager.add_provider(provider)
    return manager


def _facts_rows(manager: MemoryManager) -> list:
    provider = manager.get_provider("holographic")
    return provider._store._conn.execute(
        "SELECT content, category, session_id, fact_type FROM facts"
    ).fetchall()


_MIXED_TRANSCRIPT = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "I prefer dark mode editors for long sessions"},
    {"role": "assistant", "content": "Got it, I'll remember that."},
    # Same preference phrasing, but role="assistant" — must never be stored.
    {"role": "assistant", "content": "I prefer dark mode editors too, for what it's worth"},
    {"role": "system", "content": "We decided to use SQLite for the memory store"},
    {"role": "tool", "content": "some tool output, not extractable"},
    # Explicit-memory marker: a leading "[IMPORTANT:" must be skipped even
    # though it matches the preference pattern — it's the user's own
    # explicit-memory channel, not implicit auto-extraction's job.
    {"role": "user", "content": "[IMPORTANT: I prefer tabs over spaces, always]"},
    {"role": "user", "content": "What's the weather like today?"},  # no pattern match
]


class TestSessionEndExtractionEndToEnd:
    """auto_extract=true, config loaded from disk, wired through MemoryManager."""

    def test_mixed_transcript_persists_exactly_one_fact_with_session_id(self, tmp_path):
        _write_plugin_config(auto_extract=True)
        manager = _make_manager_with_disk_provider(session_id="sess-e2e-1")
        try:
            manager.on_session_end(_MIXED_TRANSCRIPT)

            rows = _facts_rows(manager)
            assert len(rows) == 1, f"expected exactly 1 fact, got: {[dict(r) for r in rows]}"
            row = rows[0]
            assert row["content"] == "I prefer dark mode editors for long sessions"
            assert row["category"] == "user_pref"
            assert row["session_id"] == "sess-e2e-1"
            assert row["fact_type"] == "extracted"
        finally:
            manager.shutdown_all()

    def test_second_session_end_call_dedups_instead_of_duplicating(self, tmp_path):
        """Simulates a second real session (e.g. gateway restart, re-run of
        the same idle-eviction/shutdown path) reprocessing an overlapping
        transcript — the store's content-UNIQUE dedup must hold through the
        gated on_session_end path, not just a direct _auto_extract_facts call."""
        _write_plugin_config(auto_extract=True)
        manager = _make_manager_with_disk_provider(session_id="sess-e2e-2")
        try:
            manager.on_session_end(_MIXED_TRANSCRIPT)
            first_count = len(_facts_rows(manager))
            assert first_count == 1

            # Second on_session_end call with the same transcript (e.g. a
            # retried shutdown, or a resumed session re-ending) must not
            # double-insert.
            manager.on_session_end(_MIXED_TRANSCRIPT)
            second_count = len(_facts_rows(manager))
            assert second_count == 1, "second on_session_end call duplicated a fact"
        finally:
            manager.shutdown_all()

    def test_empty_transcript_writes_nothing(self, tmp_path):
        _write_plugin_config(auto_extract=True)
        manager = _make_manager_with_disk_provider(session_id="sess-e2e-empty")
        try:
            manager.on_session_end([])

            rows = _facts_rows(manager)
            assert rows == [], f"empty session must not write facts, got: {[dict(r) for r in rows]}"
        finally:
            manager.shutdown_all()

    def test_auto_extract_false_from_disk_config_skips_extraction(self, tmp_path):
        """Same disk-config-load path, but auto_extract: false — the
        production default when a user hasn't opted in. Complements the
        auto_extract=True tests above by proving the gate reads the disk
        value, not just a hardcoded True passed by test fixtures elsewhere."""
        _write_plugin_config(auto_extract=False)
        manager = _make_manager_with_disk_provider(session_id="sess-e2e-off")
        try:
            manager.on_session_end(_MIXED_TRANSCRIPT)

            rows = _facts_rows(manager)
            assert rows == [], f"auto_extract=false must not write facts, got: {[dict(r) for r in rows]}"
        finally:
            manager.shutdown_all()
