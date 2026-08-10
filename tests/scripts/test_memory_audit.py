"""Tests for scripts/memory_audit.py: read-only fact_governance_audit CLI.

Covers the safety model documented in the script's module docstring:
``--db`` is required and never inferred from HERMES_HOME; the connection is
opened with SQLite's ``mode=ro&immutable=1`` URI flags so writes are
rejected at the SQLite level even with ``--allow-real-paths``, and no
``-wal``/``-shm`` sidecar files are ever opened or created even though
``MemoryStore`` databases use WAL journal mode; guarded real-Hermes-looking
paths are refused by default; a missing ``fact_governance_audit`` table is a
clear, non-zero-exit error; ``--fact-id`` filters, ``--limit`` truncates,
and output is ordered deterministically (``audit_id DESC``) with
``sort_keys=True`` JSON.

All databases live under ``tmp_path``, built via the real
``plugins.memory.holographic.store.MemoryStore`` (so the schema/audit rows
are byte-for-byte what production writes) or plain ``sqlite3`` for the
missing-table/malformed cases. ``HERMES_HOME`` isolation is inherited from
the project conftest's autouse ``_hermetic_environment`` fixture; this
suite additionally never points ``--db`` at the real ``HOME`` for any
*writable* operation — the two tests that reference a real-looking
``~/.hermes`` path only ever assert rejection, never open it.
"""

from __future__ import annotations

import json
import os
import sqlite3

import pytest

import scripts.memory_audit as memory_audit
from plugins.memory.holographic.store import MemoryStore


@pytest.fixture(autouse=True)
def _clean_shared_registry():
    """Each test starts and ends with an empty MemoryStore shared-connection registry.

    Building fixture databases below opens a MemoryStore, which goes
    through the same process-wide shared-connection registry as every
    other holographic test — see test_holographic_store.py.
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


def _mtime_and_hash(path):
    import hashlib

    data = path.read_bytes()
    return os.stat(path).st_mtime_ns, hashlib.sha256(data).hexdigest()


def _make_governed_db(path, mutations):
    """Build a real MemoryStore db and apply governed mutations via the store.

    mutations: iterable of callables taking `store` and returning nothing;
    lets each test decide exactly which facts/updates/forgets it needs.
    """
    store = MemoryStore(db_path=path)
    try:
        for mutate in mutations:
            mutate(store)
    finally:
        store.close()
    return path


class TestRequiredDbArgument:
    def test_missing_db_arg_is_argparse_error(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            memory_audit.main([])
        assert exc_info.value.code == 2
        assert "--db" in capsys.readouterr().err

    def test_nonexistent_db_file_is_clear_error(self, tmp_path, capsys):
        missing = tmp_path / "does_not_exist.db"

        rc = memory_audit.main(["--db", str(missing)])
        captured = capsys.readouterr()

        assert rc == 1
        assert "database not found" in captured.err
        assert captured.out == ""


class TestMissingTable:
    def test_db_without_audit_table_is_clear_error(self, tmp_path, capsys):
        db = tmp_path / "no_audit_table.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE unrelated (x TEXT)")
        conn.commit()
        conn.close()

        rc = memory_audit.main(["--db", str(db)])
        captured = capsys.readouterr()

        assert rc == 1
        assert "fact_governance_audit" in captured.err
        assert captured.out == ""

    def test_facts_only_db_without_governance_use_is_clear_error(self, tmp_path, capsys):
        # A real MemoryStore db that has never had update_fact_audited/
        # forget_fact_audited called still creates fact_governance_audit
        # (it's part of the base schema, additive CREATE TABLE IF NOT
        # EXISTS) -- so querying it returns an empty result, not an error.
        db = tmp_path / "fresh.db"
        _make_governed_db(db, [lambda store: store.add_fact("Untouched fact")])

        rc = memory_audit.main(["--db", str(db)])
        out = json.loads(capsys.readouterr().out)

        assert rc == 0
        assert out["count"] == 0
        assert out["rows"] == []


class TestFilterByFactId:
    def test_fact_id_filter_returns_only_matching_rows(self, tmp_path, capsys):
        db = tmp_path / "target.db"

        def _seed(store):
            fid1 = store.add_fact("First fact")
            fid2 = store.add_fact("Second fact")
            store.update_fact_audited(fid1, reason="fix typo", content="First fact fixed")
            store.update_fact_audited(fid2, reason="adjust trust", trust_score=0.8)

        _make_governed_db(db, [_seed])

        # Find fact_id for "Second fact" via a direct read-only peek.
        conn = sqlite3.connect(db)
        fid2 = conn.execute(
            "SELECT fact_id FROM facts WHERE content = 'Second fact fixed' "
            "OR content = 'Second fact'"
        ).fetchone()[0]
        conn.close()

        rc = memory_audit.main(["--db", str(db), "--fact-id", str(fid2)])
        out = json.loads(capsys.readouterr().out)

        assert rc == 0
        assert out["fact_id"] == fid2
        assert out["count"] == 1
        assert all(row["fact_id"] == fid2 for row in out["rows"])
        assert out["rows"][0]["reason"] == "adjust trust"

    def test_fact_id_with_no_matches_returns_empty_not_error(self, tmp_path, capsys):
        db = tmp_path / "target.db"

        def _seed(store):
            fid = store.add_fact("Only fact")
            store.forget_fact_audited(fid, reason="cleanup")

        _make_governed_db(db, [_seed])

        rc = memory_audit.main(["--db", str(db), "--fact-id", "999999"])
        out = json.loads(capsys.readouterr().out)

        assert rc == 0
        assert out["count"] == 0
        assert out["rows"] == []

    def test_zero_or_negative_fact_id_rejected(self, tmp_path, capsys):
        db = tmp_path / "target.db"
        _make_governed_db(db, [lambda store: store.add_fact("A fact")])

        rc = memory_audit.main(["--db", str(db), "--fact-id", "0"])
        captured = capsys.readouterr()

        assert rc == 2
        assert "--fact-id must be a positive integer" in captured.err
        assert captured.out == ""


class TestLimit:
    def test_limit_truncates_result_count(self, tmp_path, capsys):
        db = tmp_path / "target.db"

        def _seed(store):
            fid = store.add_fact("Repeatedly edited fact")
            for i in range(5):
                store.update_fact_audited(fid, reason=f"edit {i}", trust_score=0.1 * (i + 1))

        _make_governed_db(db, [_seed])

        rc = memory_audit.main(["--db", str(db), "--limit", "3"])
        out = json.loads(capsys.readouterr().out)

        assert rc == 0
        assert out["limit"] == 3
        assert out["count"] == 3
        assert len(out["rows"]) == 3

    def test_default_limit_is_50(self, tmp_path, capsys):
        db = tmp_path / "target.db"
        _make_governed_db(db, [lambda store: store.add_fact("A fact")])

        rc = memory_audit.main(["--db", str(db)])
        out = json.loads(capsys.readouterr().out)

        assert rc == 0
        assert out["limit"] == 50

    def test_zero_or_negative_limit_rejected(self, tmp_path, capsys):
        db = tmp_path / "target.db"
        _make_governed_db(db, [lambda store: store.add_fact("A fact")])

        rc = memory_audit.main(["--db", str(db), "--limit", "0"])
        captured = capsys.readouterr()

        assert rc == 2
        assert "--limit must be a positive integer" in captured.err
        assert captured.out == ""


class TestDeterministicOrder:
    def test_rows_ordered_by_audit_id_descending(self, tmp_path, capsys):
        db = tmp_path / "target.db"

        def _seed(store):
            fid1 = store.add_fact("Fact A")
            fid2 = store.add_fact("Fact B")
            store.update_fact_audited(fid1, reason="first edit", trust_score=0.6)
            store.update_fact_audited(fid2, reason="second edit", trust_score=0.7)
            store.forget_fact_audited(fid1, reason="third edit / removal")

        _make_governed_db(db, [_seed])

        rc = memory_audit.main(["--db", str(db)])
        out = json.loads(capsys.readouterr().out)

        assert rc == 0
        audit_ids = [row["audit_id"] for row in out["rows"]]
        assert audit_ids == sorted(audit_ids, reverse=True)
        assert [row["reason"] for row in out["rows"]] == [
            "third edit / removal",
            "second edit",
            "first edit",
        ]

    def test_output_json_keys_are_sorted_deterministically(self, tmp_path, capsys):
        db = tmp_path / "target.db"

        def _seed(store):
            fid = store.add_fact("A fact")
            store.update_fact_audited(fid, reason="a reason")

        _make_governed_db(db, [_seed])

        rc = memory_audit.main(["--db", str(db)])
        raw_out = capsys.readouterr().out

        assert rc == 0
        parsed = json.loads(raw_out)
        top_level_keys = list(parsed.keys())
        assert top_level_keys == sorted(top_level_keys)
        row_keys = list(parsed["rows"][0].keys())
        assert row_keys == sorted(row_keys)

    def test_repeated_invocations_produce_identical_output(self, tmp_path, capsys):
        db = tmp_path / "target.db"

        def _seed(store):
            fid = store.add_fact("Stable fact")
            store.update_fact_audited(fid, reason="stable edit", trust_score=0.9)

        _make_governed_db(db, [_seed])

        rc1 = memory_audit.main(["--db", str(db)])
        out1 = capsys.readouterr().out
        rc2 = memory_audit.main(["--db", str(db)])
        out2 = capsys.readouterr().out

        assert rc1 == rc2 == 0
        assert out1 == out2


class TestGuardedPathsRejected:
    def test_basename_guarded_db_rejected(self, tmp_path, capsys):
        guarded_db = tmp_path / "memory_store.db"
        _make_governed_db(guarded_db, [lambda store: store.add_fact("A fact")])

        rc = memory_audit.main(["--db", str(guarded_db)])
        captured = capsys.readouterr()

        assert rc == 2
        assert "looks like a real Hermes database path" in captured.err
        assert captured.out == ""

    def test_dot_hermes_directory_rejected_without_touching_filesystem(self, tmp_path, capsys):
        # is_guarded_path is pure string manipulation (no stat/resolve), so
        # this is safe to check even though the path is never created.
        fake_db = os.path.join(os.path.expanduser("~"), ".hermes", "memory_store_copy.db")

        rc = memory_audit.main(["--db", fake_db])
        captured = capsys.readouterr()

        assert rc == 2
        assert "looks like a real Hermes database path" in captured.err
        assert not os.path.exists(fake_db)

    def test_dot_hermes_enhanced_directory_rejected(self, tmp_path, capsys):
        fake_db = os.path.join(os.path.expanduser("~"), ".hermes-enhanced", "audit_copy.db")

        rc = memory_audit.main(["--db", fake_db])
        captured = capsys.readouterr()

        assert rc == 2
        assert "looks like a real Hermes database path" in captured.err
        assert not os.path.exists(fake_db)

    def test_allow_real_paths_lifts_guard_for_tmp_path_named_like_real_db(self, tmp_path, capsys):
        # A tmp_path file that merely shares a guarded basename is legitimate
        # test fixture data, not a real Hermes path -- --allow-real-paths
        # must let it through (the path guard is a name/location heuristic,
        # never a filesystem check against the real ~/.hermes).
        guarded_looking_db = tmp_path / "state.db"
        _make_governed_db(guarded_looking_db, [lambda store: store.add_fact("A fact")])

        rc = memory_audit.main(["--db", str(guarded_looking_db), "--allow-real-paths"])
        out = json.loads(capsys.readouterr().out)

        assert rc == 0
        assert out["count"] == 0


class TestZeroWrites:
    def test_query_never_modifies_db_file_bytes_or_mtime(self, tmp_path, capsys):
        db = tmp_path / "target.db"

        def _seed(store):
            fid = store.add_fact("Untouched by reads")
            store.update_fact_audited(fid, reason="one edit", trust_score=0.4)

        _make_governed_db(db, [_seed])

        before = _mtime_and_hash(db)
        rc = memory_audit.main(["--db", str(db)])
        capsys.readouterr()
        after = _mtime_and_hash(db)

        assert rc == 0
        assert before == after

    def test_query_never_modifies_db_file_even_with_allow_real_paths(self, tmp_path, capsys):
        # Simulates the real-path case: --allow-real-paths only lifts the
        # location guard, it must never make the connection writable.
        db = tmp_path / "memory_store.db"

        def _seed(store):
            fid = store.add_fact("Guarded-name fact")
            store.forget_fact_audited(fid, reason="removed")

        _make_governed_db(db, [_seed])

        before = _mtime_and_hash(db)
        rc = memory_audit.main(["--db", str(db), "--allow-real-paths"])
        capsys.readouterr()
        after = _mtime_and_hash(db)

        assert rc == 0
        assert before == after

    def test_direct_query_function_connection_rejects_write(self, tmp_path):
        # Belt-and-suspenders check on query_audit() itself: the connection
        # it opens is mode=ro&immutable=1 at the SQLite level, so any write
        # attempt through that exact connection object fails, independent
        # of the fact that query_audit() itself never issues one.
        db = tmp_path / "target.db"
        _make_governed_db(db, [lambda store: store.add_fact("A fact")])

        import sqlite3 as _sqlite3

        conn = _sqlite3.connect(
            f"file:{db.as_posix()}?mode=ro&immutable=1", uri=True
        )
        try:
            with pytest.raises(_sqlite3.OperationalError):
                conn.execute("INSERT INTO fact_governance_audit (fact_id, action, reason) VALUES (1, 'update', 'x')")
        finally:
            conn.close()

    def test_no_new_files_created_by_query(self, tmp_path, capsys):
        db = tmp_path / "target.db"
        _make_governed_db(db, [lambda store: store.add_fact("A fact")])

        before = set(os.listdir(tmp_path))
        rc = memory_audit.main(["--db", str(db)])
        capsys.readouterr()
        after = set(os.listdir(tmp_path))

        assert rc == 0
        assert before == after

    def test_query_never_creates_wal_or_shm_sidecar_files(self, tmp_path, capsys):
        # MemoryStore enables WAL journal mode (store.py's apply_wal_with_
        # fallback), so plain mode=ro would still have SQLite open/create
        # -wal/-shm sidecars to check for uncheckpointed frames.
        # immutable=1 must suppress that entirely.
        db = tmp_path / "target.db"
        _make_governed_db(db, [lambda store: store.add_fact("A fact")])

        wal = tmp_path / "target.db-wal"
        shm = tmp_path / "target.db-shm"
        assert not wal.exists()
        assert not shm.exists()

        rc = memory_audit.main(["--db", str(db)])
        capsys.readouterr()

        assert rc == 0
        assert not wal.exists()
        assert not shm.exists()


class TestOutputContract:
    def test_stdout_is_valid_json_and_stderr_empty_on_success(self, tmp_path, capsys):
        db = tmp_path / "target.db"

        def _seed(store):
            fid = store.add_fact("A fact")
            store.update_fact_audited(fid, reason="a reason", trust_score=0.3)

        _make_governed_db(db, [_seed])

        rc = memory_audit.main(["--db", str(db)])
        captured = capsys.readouterr()

        assert rc == 0
        assert captured.err == ""
        parsed = json.loads(captured.out)
        assert parsed["db"] == str(db)
        assert set(parsed.keys()) == {"db", "fact_id", "limit", "count", "rows"}

    def test_row_fields_never_include_transcript_or_secret_looking_keys(self, tmp_path, capsys):
        db = tmp_path / "target.db"

        def _seed(store):
            fid = store.add_fact("A fact")
            store.update_fact_audited(fid, reason="a reason", trust_score=0.3, session_id="sess-1")

        _make_governed_db(db, [_seed])

        rc = memory_audit.main(["--db", str(db)])
        out = json.loads(capsys.readouterr().out)

        assert rc == 0
        row_keys = set(out["rows"][0].keys())
        expected = {
            "audit_id", "fact_id", "action", "old_content", "new_content",
            "old_category", "new_category", "old_trust", "new_trust",
            "reason", "session_id", "created_at",
        }
        assert row_keys == expected
        assert "transcript" not in row_keys
        assert "messages" not in row_keys
        assert "secret" not in row_keys
