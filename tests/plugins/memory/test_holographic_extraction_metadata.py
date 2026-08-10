"""Tests for Phase 3B-preparación: extraction provenance metadata.

Covers two things:

1. `MemoryStore.add_fact`'s optional `session_id`/`fact_type`/`expires_at`
   keyword-only traceability fields — legacy calls are unaffected, explicit
   metadata is persisted, dedup returns the existing fact_id without
   overwriting metadata, and an invalid `fact_type` raises.
2. `HolographicMemoryProvider._auto_extract_facts` (invoked via
   `on_session_end`) now tags every fact it stores with
   `session_id=self._session_id` and `fact_type="extracted"`, only considers
   `role="user"` messages, and skips messages whose content starts with
   `"[IMPORTANT:"` (without filtering that string if it appears later in the
   message).

All databases are built under `tmp_path`; nothing under `~/.hermes` or
`~/.hermes-enhanced` is read or written. `auto_extract` stays `false` at the
config-schema-default level — these tests opt in explicitly per instance.
"""

import sqlite3

import pytest

from plugins.memory.holographic import HolographicMemoryProvider
from plugins.memory.holographic.store import VALID_FACT_TYPES, MemoryStore


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


def _make_provider(tmp_path, session_id="test-session"):
    db_path = str(tmp_path / "memory_store.db")
    provider = HolographicMemoryProvider(
        config={"db_path": db_path, "auto_extract": True, "hrr_dim": 64}
    )
    provider.initialize(session_id=session_id)
    return provider


class TestAddFactMetadata:
    """MemoryStore.add_fact keyword-only traceability fields."""

    def test_legacy_call_defaults_to_explicit_with_no_provenance(self, tmp_path):
        store = MemoryStore(tmp_path / "store.db")
        try:
            fact_id = store.add_fact("Legacy call with no metadata kwargs")
            row = store._conn.execute(
                "SELECT fact_type, session_id, expires_at FROM facts WHERE fact_id = ?",
                (fact_id,),
            ).fetchone()
            assert row["fact_type"] == "explicit"
            assert row["session_id"] is None
            assert row["expires_at"] is None
        finally:
            store.close()

    def test_explicit_metadata_is_persisted(self, tmp_path):
        store = MemoryStore(tmp_path / "store.db")
        try:
            fact_id = store.add_fact(
                "Fact with explicit provenance",
                session_id="sess-123",
                fact_type="extracted",
                expires_at="2026-12-31T00:00:00",
            )
            row = store._conn.execute(
                "SELECT fact_type, session_id, expires_at FROM facts WHERE fact_id = ?",
                (fact_id,),
            ).fetchone()
            assert row["fact_type"] == "extracted"
            assert row["session_id"] == "sess-123"
            assert row["expires_at"] == "2026-12-31T00:00:00"
        finally:
            store.close()

    def test_dedup_returns_existing_id_without_overwriting_metadata(self, tmp_path):
        store = MemoryStore(tmp_path / "store.db")
        try:
            first_id = store.add_fact(
                "Duplicate content for dedup test",
                session_id="sess-original",
                fact_type="explicit",
            )
            second_id = store.add_fact(
                "Duplicate content for dedup test",
                session_id="sess-new",
                fact_type="extracted",
            )
            assert second_id == first_id

            row = store._conn.execute(
                "SELECT fact_type, session_id FROM facts WHERE fact_id = ?",
                (first_id,),
            ).fetchone()
            # Existing row's metadata is untouched by the duplicate insert attempt.
            assert row["session_id"] == "sess-original"
            assert row["fact_type"] == "explicit"

            count = store._conn.execute(
                "SELECT COUNT(*) AS n FROM facts WHERE content = 'Duplicate content for dedup test'"
            ).fetchone()["n"]
            assert count == 1
        finally:
            store.close()

    def test_invalid_fact_type_raises(self, tmp_path):
        store = MemoryStore(tmp_path / "store.db")
        try:
            assert "bogus" not in VALID_FACT_TYPES
            with pytest.raises(ValueError):
                store.add_fact("Some content", fact_type="bogus")
        finally:
            store.close()


class TestAutoExtractProvenance:
    """HolographicMemoryProvider._auto_extract_facts via on_session_end."""

    def test_preference_extraction_saves_session_id_and_fact_type(self, tmp_path):
        provider = _make_provider(tmp_path, session_id="sess-pref")
        try:
            messages = [
                {"role": "user", "content": "I prefer dark mode editors for long sessions"},
            ]
            provider.on_session_end(messages)

            rows = provider._store._conn.execute(
                "SELECT content, category, session_id, fact_type FROM facts"
            ).fetchall()
            assert len(rows) == 1
            assert rows[0]["category"] == "user_pref"
            assert rows[0]["session_id"] == "sess-pref"
            assert rows[0]["fact_type"] == "extracted"
        finally:
            provider.shutdown()

    def test_decision_extraction_saves_session_id_and_fact_type(self, tmp_path):
        provider = _make_provider(tmp_path, session_id="sess-decision")
        try:
            messages = [
                {"role": "user", "content": "We decided to use SQLite for the memory store"},
            ]
            provider.on_session_end(messages)

            rows = provider._store._conn.execute(
                "SELECT content, category, session_id, fact_type FROM facts"
            ).fetchall()
            assert len(rows) == 1
            assert rows[0]["category"] == "project"
            assert rows[0]["session_id"] == "sess-decision"
            assert rows[0]["fact_type"] == "extracted"
        finally:
            provider.shutdown()

    def test_non_user_messages_are_ignored(self, tmp_path):
        provider = _make_provider(tmp_path, session_id="sess-role")
        try:
            messages = [
                {"role": "assistant", "content": "I prefer dark mode editors too"},
                {"role": "system", "content": "We decided to use SQLite"},
            ]
            provider.on_session_end(messages)

            count = provider._store._conn.execute(
                "SELECT COUNT(*) AS n FROM facts"
            ).fetchone()["n"]
            assert count == 0
        finally:
            provider.shutdown()

    def test_important_prefixed_message_is_excluded(self, tmp_path):
        provider = _make_provider(tmp_path, session_id="sess-important")
        try:
            messages = [
                {"role": "user", "content": "[IMPORTANT: I prefer dark mode editors]"},
            ]
            provider.on_session_end(messages)

            count = provider._store._conn.execute(
                "SELECT COUNT(*) AS n FROM facts"
            ).fetchone()["n"]
            assert count == 0
        finally:
            provider.shutdown()

    def test_important_string_mid_message_is_not_filtered(self, tmp_path):
        """Only a leading "[IMPORTANT:" excludes a message — the same text
        appearing later must not suppress extraction."""
        provider = _make_provider(tmp_path, session_id="sess-mid-important")
        try:
            messages = [
                {
                    "role": "user",
                    "content": "I prefer dark mode editors, [IMPORTANT: always enable it]",
                },
            ]
            provider.on_session_end(messages)

            rows = provider._store._conn.execute(
                "SELECT content, session_id, fact_type FROM facts"
            ).fetchall()
            assert len(rows) == 1
            assert rows[0]["session_id"] == "sess-mid-important"
            assert rows[0]["fact_type"] == "extracted"
        finally:
            provider.shutdown()
