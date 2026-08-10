"""Tests for the Phase 3A `facts` schema migration in the holographic store.

Covers the additive, backward-compatible migration that introduces
`session_id`, `fact_type`, and `expires_at` on the `facts` table, following
the same PRAGMA-detect / ALTER-TABLE-if-missing pattern already used for
`hrr_vector`. Only `add_fact`, `search_facts`, `list_facts`, and
`record_feedback` semantics matter here — this phase does not populate the
new columns (extraction/backfill is out of scope), it only prepares the
schema.

All databases are built under `tmp_path`; nothing under `~/.hermes` or
`~/.hermes-enhanced` is read or written.
"""

import sqlite3

import pytest

from plugins.memory.holographic.store import MemoryStore

_NEW_COLUMNS = {"session_id", "fact_type", "expires_at"}

# Schema as it existed before Phase 3A (has hrr_vector, but not the three
# new columns) — used to simulate a pre-existing database on disk.
_OLD_SCHEMA = """
CREATE TABLE facts (
    fact_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    content         TEXT NOT NULL UNIQUE,
    category        TEXT DEFAULT 'general',
    tags            TEXT DEFAULT '',
    trust_score     REAL DEFAULT 0.5,
    retrieval_count INTEGER DEFAULT 0,
    helpful_count   INTEGER DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    hrr_vector      BLOB
);

CREATE TABLE entities (
    entity_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    entity_type TEXT DEFAULT 'unknown',
    aliases     TEXT DEFAULT '',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE fact_entities (
    fact_id   INTEGER REFERENCES facts(fact_id),
    entity_id INTEGER REFERENCES entities(entity_id),
    PRIMARY KEY (fact_id, entity_id)
);

CREATE INDEX idx_facts_trust    ON facts(trust_score DESC);
CREATE INDEX idx_facts_category ON facts(category);
CREATE INDEX idx_entities_name  ON entities(name);

CREATE VIRTUAL TABLE facts_fts
    USING fts5(content, tags, content=facts, content_rowid=fact_id);

CREATE TRIGGER facts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, content, tags)
        VALUES (new.fact_id, new.content, new.tags);
END;

CREATE TRIGGER facts_ad AFTER DELETE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, content, tags)
        VALUES ('delete', old.fact_id, old.content, old.tags);
END;

CREATE TRIGGER facts_au AFTER UPDATE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, content, tags)
        VALUES ('delete', old.fact_id, old.content, old.tags);
    INSERT INTO facts_fts(rowid, content, tags)
        VALUES (new.fact_id, new.content, new.tags);
END;

CREATE TABLE memory_banks (
    bank_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    bank_name  TEXT NOT NULL UNIQUE,
    vector     BLOB NOT NULL,
    dim        INTEGER NOT NULL,
    fact_count INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


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


def _columns(conn: sqlite3.Connection, table: str) -> set:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _index_names(conn: sqlite3.Connection, table: str) -> set:
    return {
        row[1]
        for row in conn.execute(f"PRAGMA index_list({table})").fetchall()
    }


def _build_old_schema_db(db_path, facts: list[tuple]) -> None:
    """Create a pre-Phase-3A database on disk with the given (content, category, trust) rows."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(_OLD_SCHEMA)
        for content, category, trust in facts:
            conn.execute(
                "INSERT INTO facts (content, category, trust_score) VALUES (?, ?, ?)",
                (content, category, trust),
            )
        conn.commit()
    finally:
        conn.close()


class TestNewDatabaseSchema:
    """A database created fresh already has the Phase 3A columns."""

    def test_new_db_has_new_columns(self, tmp_path):
        store = MemoryStore(tmp_path / "fresh.db")
        try:
            columns = _columns(store._conn, "facts")
            assert _NEW_COLUMNS <= columns
        finally:
            store.close()

    def test_fact_type_defaults_to_explicit(self, tmp_path):
        store = MemoryStore(tmp_path / "fresh.db")
        try:
            fact_id = store.add_fact("A brand new fact for testing.")
            row = store._conn.execute(
                "SELECT fact_type, session_id, expires_at FROM facts WHERE fact_id = ?",
                (fact_id,),
            ).fetchone()
            assert row["fact_type"] == "explicit"
            assert row["session_id"] is None
            assert row["expires_at"] is None
        finally:
            store.close()


class TestOldDatabaseMigration:
    """A pre-existing database missing the new columns migrates safely in place."""

    def test_migration_adds_missing_columns(self, tmp_path):
        db_path = tmp_path / "legacy.db"
        _build_old_schema_db(db_path, [("Old fact one", "general", 0.5)])

        conn = sqlite3.connect(str(db_path))
        try:
            assert not (_NEW_COLUMNS <= _columns(conn, "facts"))
        finally:
            conn.close()

        store = MemoryStore(db_path)
        try:
            assert _NEW_COLUMNS <= _columns(store._conn, "facts")
        finally:
            store.close()

    def test_migration_preserves_existing_facts(self, tmp_path):
        db_path = tmp_path / "legacy.db"
        seed = [
            ("First legacy fact", "general", 0.5),
            ("Second legacy fact", "work", 0.7),
        ]
        _build_old_schema_db(db_path, seed)

        store = MemoryStore(db_path)
        try:
            rows = store._conn.execute(
                "SELECT content, category, trust_score, fact_type, session_id, expires_at "
                "FROM facts ORDER BY fact_id"
            ).fetchall()
            assert [r["content"] for r in rows] == [s[0] for s in seed]
            assert [r["category"] for r in rows] == [s[1] for s in seed]
            assert [r["trust_score"] for r in rows] == [s[2] for s in seed]
            # Backfilled default applies to pre-existing rows too.
            assert all(r["fact_type"] == "explicit" for r in rows)
            assert all(r["session_id"] is None for r in rows)
            assert all(r["expires_at"] is None for r in rows)
        finally:
            store.close()

    def test_migration_preserves_indices(self, tmp_path):
        db_path = tmp_path / "legacy.db"
        _build_old_schema_db(db_path, [("Indexed fact", "general", 0.5)])

        store = MemoryStore(db_path)
        try:
            fact_indices = _index_names(store._conn, "facts")
            entity_indices = _index_names(store._conn, "entities")
            assert {"idx_facts_trust", "idx_facts_category"} <= fact_indices
            assert "idx_entities_name" in entity_indices
        finally:
            store.close()

    def test_migration_preserves_fts_search(self, tmp_path):
        db_path = tmp_path / "legacy.db"
        _build_old_schema_db(
            db_path, [("The quokka is a marsupial from Rottnest Island", "trivia", 0.5)]
        )

        store = MemoryStore(db_path)
        try:
            results = store.search_facts("quokka")
            assert len(results) == 1
            assert "quokka" in results[0]["content"]
        finally:
            store.close()

    def test_migration_preserves_fact_entities_links(self, tmp_path):
        """Legacy entity links (built before the new columns existed) survive the migration."""
        db_path = tmp_path / "legacy.db"
        conn = sqlite3.connect(str(db_path))
        try:
            conn.executescript(_OLD_SCHEMA)
            conn.execute(
                "INSERT INTO facts (content, category, trust_score) VALUES (?, ?, ?)",
                ("Guido van Rossum created Python", "general", 0.5),
            )
            conn.execute(
                "INSERT INTO entities (name) VALUES ('Guido van Rossum')"
            )
            conn.execute(
                "INSERT INTO fact_entities (fact_id, entity_id) VALUES (1, 1)"
            )
            conn.commit()
        finally:
            conn.close()

        store = MemoryStore(db_path)
        try:
            row = store._conn.execute(
                "SELECT e.name FROM entities e "
                "JOIN fact_entities fe ON fe.entity_id = e.entity_id "
                "WHERE fe.fact_id = 1"
            ).fetchone()
            assert row["name"] == "Guido van Rossum"
        finally:
            store.close()


class TestIdempotentReinitialisation:
    """Reopening a database that already has the new columns must not error or duplicate them."""

    def test_reopen_fresh_db_is_idempotent(self, tmp_path):
        db_path = tmp_path / "reinit.db"
        store1 = MemoryStore(db_path)
        store1.close()

        # Registry entry was fully released (refs hit 0) — a fresh MemoryStore
        # reopens the file and re-runs _init_db against existing tables/columns.
        store2 = MemoryStore(db_path)
        try:
            columns = _columns(store2._conn, "facts")
            assert _NEW_COLUMNS <= columns
            # PRAGMA table_info must list each column exactly once.
            names = [row[1] for row in store2._conn.execute("PRAGMA table_info(facts)").fetchall()]
            assert len(names) == len(set(names))
        finally:
            store2.close()

    def test_reopen_migrated_legacy_db_is_idempotent(self, tmp_path):
        db_path = tmp_path / "legacy_reinit.db"
        _build_old_schema_db(db_path, [("Some fact", "general", 0.5)])

        store1 = MemoryStore(db_path)
        store1.close()

        store2 = MemoryStore(db_path)
        try:
            names = [row[1] for row in store2._conn.execute("PRAGMA table_info(facts)").fetchall()]
            assert len(names) == len(set(names))
            assert _NEW_COLUMNS <= set(names)
            row = store2._conn.execute(
                "SELECT content FROM facts WHERE content = 'Some fact'"
            ).fetchone()
            assert row is not None
        finally:
            store2.close()


class TestFunctionalityPreservedAfterMigration:
    """add_fact / search_facts / list_facts / record_feedback keep their existing behavior."""

    def test_add_fact_dedup_after_migration(self, tmp_path):
        db_path = tmp_path / "legacy.db"
        _build_old_schema_db(db_path, [("Duplicate me", "general", 0.5)])

        store = MemoryStore(db_path)
        try:
            fact_id = store.add_fact("Duplicate me")
            row = store._conn.execute(
                "SELECT fact_id FROM facts WHERE content = 'Duplicate me'"
            ).fetchone()
            assert fact_id == row["fact_id"]
            count = store._conn.execute(
                "SELECT COUNT(*) AS n FROM facts WHERE content = 'Duplicate me'"
            ).fetchone()["n"]
            assert count == 1
        finally:
            store.close()

    def test_add_search_list_feedback_round_trip_after_migration(self, tmp_path):
        db_path = tmp_path / "legacy.db"
        _build_old_schema_db(db_path, [("Pre-existing legacy fact", "general", 0.5)])

        store = MemoryStore(db_path)
        try:
            fact_id = store.add_fact("Fresh fact about pytest fixtures", category="tech")

            search_results = store.search_facts("pytest")
            assert any(r["fact_id"] == fact_id for r in search_results)
            # New columns are not part of the public dict shape (out of scope
            # for this phase — no semantic change to search_facts).
            assert "session_id" not in search_results[0]
            assert "fact_type" not in search_results[0]
            assert "expires_at" not in search_results[0]

            listed = store.list_facts(category="tech")
            assert any(r["fact_id"] == fact_id for r in listed)

            feedback = store.record_feedback(fact_id, helpful=True)
            assert feedback["fact_id"] == fact_id
            assert feedback["new_trust"] > feedback["old_trust"]
        finally:
            store.close()
