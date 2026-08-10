"""Tests for the governance layer added to the holographic MemoryStore.

Covers `update_fact_audited` / `forget_fact_audited`: explicit-by-id
correction/removal of an existing fact with a mandatory `reason` and a
local audit trail (`fact_governance_audit`, additive + idempotent, added
via the same `CREATE TABLE IF NOT EXISTS` pattern already used for
`memory_banks`). Neither method can create a new fact, neither stores
secrets or conversation transcripts, and the mutation + its audit row are
written in one explicit SQLite transaction.

All databases are built under `tmp_path`; nothing under `~/.hermes` or
`~/.hermes-enhanced` is read or written. No auto-extraction, no config,
no cron/systemd/service interaction.
"""

import sqlite3

import pytest

from plugins.memory.holographic.store import MemoryStore

_AUDIT_TABLE = "fact_governance_audit"

# Pre-Phase-3A style legacy schema, used to prove the audit table addition
# is additive and does not disturb pre-existing data in other tables.
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
    """Each test starts and ends with an empty shared-connection registry."""
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


def _table_names(conn: sqlite3.Connection) -> set:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    return {row[0] for row in rows}


def _audit_rows(conn: sqlite3.Connection, fact_id: int) -> list:
    return conn.execute(
        f"SELECT * FROM {_AUDIT_TABLE} WHERE fact_id = ? ORDER BY audit_id",
        (fact_id,),
    ).fetchall()


class TestAuditTableIsAdditive:
    """The new table is additive/idempotent and never disturbs other tables."""

    def test_fresh_db_has_audit_table(self, tmp_path):
        store = MemoryStore(tmp_path / "fresh.db")
        try:
            assert _AUDIT_TABLE in _table_names(store._conn)
        finally:
            store.close()

    def test_legacy_db_gains_audit_table_without_losing_data(self, tmp_path):
        db_path = tmp_path / "legacy.db"
        conn = sqlite3.connect(str(db_path))
        try:
            conn.executescript(_OLD_SCHEMA)
            conn.execute(
                "INSERT INTO facts (content, category, trust_score) VALUES (?, ?, ?)",
                ("Legacy fact one", "general", 0.5),
            )
            conn.execute("INSERT INTO entities (name) VALUES ('Someone')")
            conn.execute("INSERT INTO fact_entities (fact_id, entity_id) VALUES (1, 1)")
            conn.commit()
        finally:
            conn.close()

        store = MemoryStore(db_path)
        try:
            assert _AUDIT_TABLE in _table_names(store._conn)
            facts = store._conn.execute("SELECT content, category FROM facts").fetchall()
            assert [dict(r) for r in facts] == [
                {"content": "Legacy fact one", "category": "general"}
            ]
            entities = store._conn.execute("SELECT name FROM entities").fetchall()
            assert [dict(r) for r in entities] == [{"name": "Someone"}]
            links = store._conn.execute("SELECT * FROM fact_entities").fetchall()
            assert len(links) == 1
        finally:
            store.close()

    def test_reopen_is_idempotent(self, tmp_path):
        db_path = tmp_path / "reinit.db"
        store1 = MemoryStore(db_path)
        store1.close()

        store2 = MemoryStore(db_path)
        try:
            names = [
                row[0]
                for row in store2._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' "
                    f"AND name = '{_AUDIT_TABLE}'"
                ).fetchall()
            ]
            assert names == [_AUDIT_TABLE]
        finally:
            store2.close()

    def test_reopen_legacy_db_twice_is_idempotent(self, tmp_path):
        db_path = tmp_path / "legacy_reinit.db"
        conn = sqlite3.connect(str(db_path))
        try:
            conn.executescript(_OLD_SCHEMA)
            conn.execute(
                "INSERT INTO facts (content) VALUES ('Some fact')"
            )
            conn.commit()
        finally:
            conn.close()

        store1 = MemoryStore(db_path)
        store1.close()
        store2 = MemoryStore(db_path)
        try:
            count = store2._conn.execute(
                "SELECT COUNT(*) AS n FROM sqlite_master "
                f"WHERE type = 'table' AND name = '{_AUDIT_TABLE}'"
            ).fetchone()["n"]
            assert count == 1
            row = store2._conn.execute(
                "SELECT content FROM facts WHERE content = 'Some fact'"
            ).fetchone()
            assert row is not None
        finally:
            store2.close()


class TestUpdateFactAudited:
    def test_update_content_records_audit(self, tmp_path):
        store = MemoryStore(tmp_path / "db.sqlite")
        try:
            fact_id = store.add_fact("Original content", category="general")

            result = store.update_fact_audited(
                fact_id, reason="correcting typo", content="Corrected content"
            )

            assert result["noop"] is False
            assert result["changed_fields"] == ["content"]
            assert result["old"]["content"] == "Original content"
            assert result["new"]["content"] == "Corrected content"

            row = store._conn.execute(
                "SELECT content FROM facts WHERE fact_id = ?", (fact_id,)
            ).fetchone()
            assert row["content"] == "Corrected content"

            audit = _audit_rows(store._conn, fact_id)
            assert len(audit) == 1
            assert audit[0]["action"] == "update"
            assert audit[0]["old_content"] == "Original content"
            assert audit[0]["new_content"] == "Corrected content"
            assert audit[0]["reason"] == "correcting typo"
            # category/trust were not part of this update -> not applicable
            assert audit[0]["old_category"] is None
            assert audit[0]["new_category"] is None
            assert audit[0]["old_trust"] is None
            assert audit[0]["new_trust"] is None
            assert audit[0]["session_id"] is None
            assert audit[0]["created_at"] is not None
        finally:
            store.close()

    def test_update_trust_and_category_records_both(self, tmp_path):
        store = MemoryStore(tmp_path / "db.sqlite")
        try:
            fact_id = store.add_fact("A fact", category="general")

            result = store.update_fact_audited(
                fact_id,
                reason="recategorize and boost trust",
                category="project",
                trust_score=0.9,
                session_id="sess-1",
            )

            assert set(result["changed_fields"]) == {"category", "trust_score"}
            row = store._conn.execute(
                "SELECT category, trust_score FROM facts WHERE fact_id = ?", (fact_id,)
            ).fetchone()
            assert row["category"] == "project"
            assert row["trust_score"] == pytest.approx(0.9)

            audit = _audit_rows(store._conn, fact_id)[0]
            assert audit["old_category"] == "general"
            assert audit["new_category"] == "project"
            assert audit["old_trust"] == pytest.approx(0.5)
            assert audit["new_trust"] == pytest.approx(0.9)
            assert audit["old_content"] is None
            assert audit["new_content"] is None
            assert audit["session_id"] == "sess-1"
        finally:
            store.close()

    def test_update_moves_between_category_banks(self, tmp_path):
        store = MemoryStore(tmp_path / "db.sqlite")
        try:
            if not store._hrr_available:
                pytest.skip("hrr optional dependency is not installed")
            fact_id = store.add_fact("Bank move fact", category="general")
            store.update_fact_audited(fact_id, reason="move category", category="project")

            general_bank = store._conn.execute(
                "SELECT fact_count FROM memory_banks WHERE bank_name = 'cat:general'"
            ).fetchone()
            project_bank = store._conn.execute(
                "SELECT fact_count FROM memory_banks WHERE bank_name = 'cat:project'"
            ).fetchone()
            # general bank either gone or zero facts; project bank has the fact.
            assert general_bank is None or general_bank["fact_count"] == 0
            assert project_bank is not None and project_bank["fact_count"] == 1
        finally:
            store.close()

    def test_update_rejects_missing_reason(self, tmp_path):
        store = MemoryStore(tmp_path / "db.sqlite")
        try:
            fact_id = store.add_fact("A fact")
            with pytest.raises(ValueError, match="reason"):
                store.update_fact_audited(fact_id, reason="", content="New content")
            with pytest.raises(ValueError, match="reason"):
                store.update_fact_audited(fact_id, reason="   ", content="New content")
        finally:
            store.close()

    def test_update_rejects_nonexistent_fact_id(self, tmp_path):
        store = MemoryStore(tmp_path / "db.sqlite")
        try:
            with pytest.raises(KeyError):
                store.update_fact_audited(9999, reason="test", content="New content")
        finally:
            store.close()

    def test_update_rejects_empty_content(self, tmp_path):
        store = MemoryStore(tmp_path / "db.sqlite")
        try:
            fact_id = store.add_fact("A fact")
            with pytest.raises(ValueError, match="content"):
                store.update_fact_audited(fact_id, reason="test", content="   ")
        finally:
            store.close()

    def test_update_rejects_out_of_range_trust(self, tmp_path):
        store = MemoryStore(tmp_path / "db.sqlite")
        try:
            fact_id = store.add_fact("A fact")
            with pytest.raises(ValueError, match="trust_score"):
                store.update_fact_audited(fact_id, reason="test", trust_score=1.5)
            with pytest.raises(ValueError, match="trust_score"):
                store.update_fact_audited(fact_id, reason="test", trust_score=-0.1)
        finally:
            store.close()

    def test_update_rejects_duplicate_content_preserves_dedup(self, tmp_path):
        store = MemoryStore(tmp_path / "db.sqlite")
        try:
            other_id = store.add_fact("Existing unique content")
            fact_id = store.add_fact("Fact to update")

            with pytest.raises(ValueError, match="already exists"):
                store.update_fact_audited(
                    fact_id, reason="test dedup", content="Existing unique content"
                )

            # Neither fact's content changed; no audit row for the failed attempt.
            row = store._conn.execute(
                "SELECT content FROM facts WHERE fact_id = ?", (fact_id,)
            ).fetchone()
            assert row["content"] == "Fact to update"
            assert _audit_rows(store._conn, fact_id) == []

            count = store._conn.execute(
                "SELECT COUNT(*) AS n FROM facts WHERE content = 'Existing unique content'"
            ).fetchone()["n"]
            assert count == 1
            assert other_id != fact_id
        finally:
            store.close()

    def test_update_noop_when_values_match_current(self, tmp_path):
        store = MemoryStore(tmp_path / "db.sqlite")
        try:
            fact_id = store.add_fact("Stable content", category="general")
            before = store._conn.execute(
                "SELECT updated_at FROM facts WHERE fact_id = ?", (fact_id,)
            ).fetchone()["updated_at"]

            result = store.update_fact_audited(
                fact_id,
                reason="confirming this fact is still correct",
                content="Stable content",
                category="general",
                trust_score=0.5,
            )

            assert result["noop"] is True
            assert result["changed_fields"] == []

            after = store._conn.execute(
                "SELECT updated_at FROM facts WHERE fact_id = ?", (fact_id,)
            ).fetchone()["updated_at"]
            assert after == before

            # The attempt is still audited, with reason recorded and no
            # old/new field populated (nothing was "applicable").
            audit = _audit_rows(store._conn, fact_id)
            assert len(audit) == 1
            assert audit[0]["reason"] == "confirming this fact is still correct"
            assert audit[0]["old_content"] is None
            assert audit[0]["new_content"] is None

        finally:
            store.close()

    def test_update_noop_with_no_fields_provided(self, tmp_path):
        store = MemoryStore(tmp_path / "db.sqlite")
        try:
            fact_id = store.add_fact("Untouched fact")
            result = store.update_fact_audited(fact_id, reason="just checking")
            assert result["noop"] is True
            assert result["changed_fields"] == []
        finally:
            store.close()

    def test_update_never_creates_a_new_fact(self, tmp_path):
        store = MemoryStore(tmp_path / "db.sqlite")
        try:
            fact_id = store.add_fact("Countable fact")
            count_before = store._conn.execute(
                "SELECT COUNT(*) AS n FROM facts"
            ).fetchone()["n"]

            store.update_fact_audited(fact_id, reason="tweak", content="Countable fact tweaked")
            store.update_fact_audited(fact_id, reason="noop check", content="Countable fact tweaked")

            count_after = store._conn.execute(
                "SELECT COUNT(*) AS n FROM facts"
            ).fetchone()["n"]
            assert count_after == count_before
        finally:
            store.close()

    def test_update_rollback_on_audit_write_failure(self, tmp_path, monkeypatch):
        """If the audit INSERT fails, the facts UPDATE in the same transaction must roll back."""

        class FaultyConnection(sqlite3.Connection):
            fail_on = None

            def execute(self, sql, params=()):
                if self.fail_on and self.fail_on in sql:
                    raise sqlite3.OperationalError("simulated audit failure")
                return super().execute(sql, params)

        real_connect = sqlite3.connect

        def faulty_connect(*args, **kwargs):
            kwargs["factory"] = FaultyConnection
            return real_connect(*args, **kwargs)

        monkeypatch.setattr(sqlite3, "connect", faulty_connect)

        store = MemoryStore(tmp_path / "rollback.db")
        try:
            fact_id = store.add_fact("Pre-rollback content", category="general")

            store._conn.fail_on = "INSERT INTO fact_governance_audit"
            with pytest.raises(sqlite3.OperationalError):
                store.update_fact_audited(
                    fact_id, reason="should roll back", content="Post-rollback content"
                )
            store._conn.fail_on = None

            row = store._conn.execute(
                "SELECT content FROM facts WHERE fact_id = ?", (fact_id,)
            ).fetchone()
            assert row["content"] == "Pre-rollback content"
            assert _audit_rows(store._conn, fact_id) == []
        finally:
            store.close()


class TestForgetFactAudited:
    def test_forget_removes_fact_and_records_audit(self, tmp_path):
        store = MemoryStore(tmp_path / "db.sqlite")
        try:
            fact_id = store.add_fact("Fact to forget", category="general")

            result = store.forget_fact_audited(fact_id, reason="user asked to delete this")

            assert result["removed"] is True
            assert result["old"]["content"] == "Fact to forget"

            row = store._conn.execute(
                "SELECT * FROM facts WHERE fact_id = ?", (fact_id,)
            ).fetchone()
            assert row is None

            links = store._conn.execute(
                "SELECT * FROM fact_entities WHERE fact_id = ?", (fact_id,)
            ).fetchall()
            assert links == []

            audit = _audit_rows(store._conn, fact_id)
            assert len(audit) == 1
            assert audit[0]["action"] == "forget"
            assert audit[0]["old_content"] == "Fact to forget"
            assert audit[0]["old_category"] == "general"
            assert audit[0]["old_trust"] == pytest.approx(0.5)
            assert audit[0]["new_content"] is None
            assert audit[0]["new_category"] is None
            assert audit[0]["new_trust"] is None
            assert audit[0]["reason"] == "user asked to delete this"
        finally:
            store.close()

    def test_forget_rejects_missing_reason(self, tmp_path):
        store = MemoryStore(tmp_path / "db.sqlite")
        try:
            fact_id = store.add_fact("A fact")
            with pytest.raises(ValueError, match="reason"):
                store.forget_fact_audited(fact_id, reason="")
        finally:
            store.close()

    def test_forget_rejects_nonexistent_fact_id(self, tmp_path):
        store = MemoryStore(tmp_path / "db.sqlite")
        try:
            with pytest.raises(KeyError):
                store.forget_fact_audited(9999, reason="test")
        finally:
            store.close()

    def test_forget_does_not_touch_unrelated_facts(self, tmp_path):
        store = MemoryStore(tmp_path / "db.sqlite")
        try:
            keep_id = store.add_fact("Keep this fact", category="general")
            forget_id = store.add_fact("Forget this fact", category="general")

            store.forget_fact_audited(forget_id, reason="stale")

            row = store._conn.execute(
                "SELECT content FROM facts WHERE fact_id = ?", (keep_id,)
            ).fetchone()
            assert row["content"] == "Keep this fact"
        finally:
            store.close()

    def test_forget_rollback_on_audit_write_failure(self, tmp_path, monkeypatch):
        class FaultyConnection(sqlite3.Connection):
            fail_on = None

            def execute(self, sql, params=()):
                if self.fail_on and self.fail_on in sql:
                    raise sqlite3.OperationalError("simulated audit failure")
                return super().execute(sql, params)

        real_connect = sqlite3.connect

        def faulty_connect(*args, **kwargs):
            kwargs["factory"] = FaultyConnection
            return real_connect(*args, **kwargs)

        monkeypatch.setattr(sqlite3, "connect", faulty_connect)

        store = MemoryStore(tmp_path / "rollback_forget.db")
        try:
            fact_id = store.add_fact("Should survive rollback", category="general")

            store._conn.fail_on = "INSERT INTO fact_governance_audit"
            with pytest.raises(sqlite3.OperationalError):
                store.forget_fact_audited(fact_id, reason="should roll back")
            store._conn.fail_on = None

            row = store._conn.execute(
                "SELECT content FROM facts WHERE fact_id = ?", (fact_id,)
            ).fetchone()
            assert row is not None
            assert row["content"] == "Should survive rollback"
            assert _audit_rows(store._conn, fact_id) == []
        finally:
            store.close()
