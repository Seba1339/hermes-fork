"""Tests for Phase 3C: extraction dry-run preview.

Covers `HolographicMemoryProvider.preview_extracted_facts`, the pure
extraction-detection method factored out of `_auto_extract_facts`:

1. It returns the same candidate facts `_auto_extract_facts` would persist
   (preference + decision detection, `role="user"` only, `"[IMPORTANT:"`
   prefix filtering, mid-message `"[IMPORTANT:"` is NOT filtered) — but as
   plain dicts, with no SQLite access and no call to `add_fact`.
2. `_auto_extract_facts` still persists — via `add_fact`, dedup included —
   only when called explicitly (i.e. `on_session_end` with `auto_extract`
   truthy). `auto_extract` itself stays `false` by default.

All databases are built under `tmp_path`; nothing under `~/.hermes` or
`~/.hermes-enhanced` is read or written. Several tests instantiate
`HolographicMemoryProvider` directly without calling `initialize()`, to
prove `preview_extracted_facts` needs no store/DB at all.
"""

import sqlite3

import pytest

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


def _make_provider(tmp_path, session_id="test-session", auto_extract=True):
    db_path = str(tmp_path / "memory_store.db")
    provider = HolographicMemoryProvider(
        config={"db_path": db_path, "auto_extract": auto_extract, "hrr_dim": 64}
    )
    provider.initialize(session_id=session_id)
    return provider


class TestPreviewIsPure:
    """preview_extracted_facts touches no store/DB and has no side effects."""

    def test_no_db_file_created_by_uninitialized_provider(self, tmp_path):
        db_path = tmp_path / "memory_store.db"
        provider = HolographicMemoryProvider(
            config={"db_path": str(db_path), "auto_extract": False}
        )
        # Deliberately not calling initialize(): preview must not require a
        # store at all.
        provider._session_id = "preview-only-session"

        messages = [
            {"role": "user", "content": "I prefer dark mode editors for long sessions"},
            {"role": "user", "content": "We decided to use SQLite for the memory store"},
        ]
        candidates = provider.preview_extracted_facts(messages)

        assert len(candidates) == 2
        assert not db_path.exists()
        assert provider._store is None

    def test_no_db_file_created_by_initialized_provider(self, tmp_path):
        # Even with a real store wired up, preview alone must not write to it.
        provider = _make_provider(tmp_path, session_id="sess-preview", auto_extract=False)
        try:
            messages = [
                {"role": "user", "content": "I prefer dark mode editors for long sessions"},
            ]
            provider.preview_extracted_facts(messages)

            count = provider._store._conn.execute(
                "SELECT COUNT(*) AS n FROM facts"
            ).fetchone()["n"]
            assert count == 0
        finally:
            provider.shutdown()


class TestPreviewDetection:
    """preview_extracted_facts detects the same candidates _auto_extract_facts would persist."""

    def test_preference_message_is_returned(self, tmp_path):
        provider = HolographicMemoryProvider(config={"auto_extract": False})
        provider._session_id = "sess-pref"

        messages = [
            {"role": "user", "content": "I prefer dark mode editors for long sessions"},
        ]
        candidates = provider.preview_extracted_facts(messages)

        assert len(candidates) == 1
        assert candidates[0]["category"] == "user_pref"
        assert candidates[0]["content"] == "I prefer dark mode editors for long sessions"
        assert candidates[0]["fact_type"] == "extracted"
        assert candidates[0]["session_id"] == "sess-pref"

    def test_decision_message_is_returned(self, tmp_path):
        provider = HolographicMemoryProvider(config={"auto_extract": False})
        provider._session_id = "sess-decision"

        messages = [
            {"role": "user", "content": "We decided to use SQLite for the memory store"},
        ]
        candidates = provider.preview_extracted_facts(messages)

        assert len(candidates) == 1
        assert candidates[0]["category"] == "project"
        assert candidates[0]["content"] == "We decided to use SQLite for the memory store"
        assert candidates[0]["fact_type"] == "extracted"
        assert candidates[0]["session_id"] == "sess-decision"

    def test_assistant_and_system_messages_are_ignored(self, tmp_path):
        provider = HolographicMemoryProvider(config={"auto_extract": False})
        provider._session_id = "sess-role"

        messages = [
            {"role": "assistant", "content": "I prefer dark mode editors too"},
            {"role": "system", "content": "We decided to use SQLite"},
        ]
        candidates = provider.preview_extracted_facts(messages)

        assert candidates == []

    def test_important_prefixed_message_is_excluded(self, tmp_path):
        provider = HolographicMemoryProvider(config={"auto_extract": False})
        provider._session_id = "sess-important"

        messages = [
            {"role": "user", "content": "[IMPORTANT: I prefer dark mode editors]"},
        ]
        candidates = provider.preview_extracted_facts(messages)

        assert candidates == []

    def test_important_string_mid_message_is_not_filtered(self, tmp_path):
        """Only a leading "[IMPORTANT:" excludes a message — the same text
        appearing later must still be detected."""
        provider = HolographicMemoryProvider(config={"auto_extract": False})
        provider._session_id = "sess-mid-important"

        messages = [
            {
                "role": "user",
                "content": "I prefer dark mode editors, [IMPORTANT: always enable it]",
            },
        ]
        candidates = provider.preview_extracted_facts(messages)

        assert len(candidates) == 1
        assert candidates[0]["session_id"] == "sess-mid-important"
        assert candidates[0]["fact_type"] == "extracted"

    def test_no_candidates_returns_empty_list(self, tmp_path):
        provider = HolographicMemoryProvider(config={"auto_extract": False})
        provider._session_id = "sess-empty"

        messages = [
            {"role": "user", "content": "What's the weather like today?"},
            {"role": "user", "content": "short"},
        ]
        candidates = provider.preview_extracted_facts(messages)

        assert candidates == []

    def test_empty_message_list_returns_empty_list(self, tmp_path):
        provider = HolographicMemoryProvider(config={"auto_extract": False})
        provider._session_id = "sess-none"

        assert provider.preview_extracted_facts([]) == []


class TestAutoExtractStillRequiresExplicitCall:
    """_auto_extract_facts only persists when invoked explicitly."""

    def test_on_session_end_does_not_persist_when_auto_extract_is_false(self, tmp_path):
        provider = _make_provider(tmp_path, session_id="sess-off", auto_extract=False)
        try:
            messages = [
                {"role": "user", "content": "I prefer dark mode editors for long sessions"},
            ]
            provider.on_session_end(messages)

            count = provider._store._conn.execute(
                "SELECT COUNT(*) AS n FROM facts"
            ).fetchone()["n"]
            assert count == 0
        finally:
            provider.shutdown()

    def test_direct_call_to_auto_extract_facts_persists_via_add_fact(self, tmp_path):
        provider = _make_provider(tmp_path, session_id="sess-direct", auto_extract=False)
        try:
            messages = [
                {"role": "user", "content": "I prefer dark mode editors for long sessions"},
            ]
            # auto_extract is False, but calling the persistence method
            # directly (as on_session_end would if auto_extract were True)
            # must still store the fact via add_fact, dedup included.
            provider._auto_extract_facts(messages)

            rows = provider._store._conn.execute(
                "SELECT content, category, session_id, fact_type FROM facts"
            ).fetchall()
            assert len(rows) == 1
            assert rows[0]["category"] == "user_pref"
            assert rows[0]["session_id"] == "sess-direct"
            assert rows[0]["fact_type"] == "extracted"
        finally:
            provider.shutdown()

    def test_direct_call_twice_dedups_via_add_fact(self, tmp_path):
        provider = _make_provider(tmp_path, session_id="sess-dedup", auto_extract=False)
        try:
            messages = [
                {"role": "user", "content": "I prefer dark mode editors for long sessions"},
            ]
            provider._auto_extract_facts(messages)
            provider._auto_extract_facts(messages)

            count = provider._store._conn.execute(
                "SELECT COUNT(*) AS n FROM facts"
            ).fetchone()["n"]
            assert count == 1
        finally:
            provider.shutdown()
