"""Tests for scripts/memory_governance.py: governed fact mutation CLI.

Covers the safety model documented in the script's module docstring:
dry-run (no ``--apply``) is the default and never imports/constructs
``MemoryStore`` or writes anything to ``--db``; ``--apply`` requires an
explicit ``--backup-dir`` and takes a byte-for-byte, timestamped backup of
``--db`` before any write — if the backup fails, nothing is written;
``--action forget`` requires ``--confirm-forget``; guarded real-Hermes-
looking paths are refused unless ``--allow-real-paths``, and ``--apply``
against such a path additionally requires ``--confirm-real-governance``;
a nonexistent ``--fact-id`` and a nonexistent ``--db`` are both clear,
non-zero-exit errors; invalid arguments (missing ``--fact-id``/``--reason``,
non-positive ``--fact-id``, blank ``--reason``, blank ``--content``,
out-of-range ``--trust-score``, ``--action update`` with no field to
change) are rejected before any database is touched.

All databases live under ``tmp_path``, built via the real
``plugins.memory.holographic.store.MemoryStore`` (so the schema/mutations
are byte-for-byte what production writes). ``HERMES_HOME`` isolation is
inherited from the project conftest's autouse ``_hermetic_environment``
fixture; this suite additionally never points ``--db`` at the real ``HOME``
for any *writable* operation — the tests that reference a real-looking
``~/.hermes``/``memory_store.db`` path only ever assert rejection, never
open or write to it.
"""

from __future__ import annotations

import json
import os
import sqlite3

import pytest

import scripts.memory_governance as memory_governance
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


def _make_governed_db(path, mutations=()):
    """Build a real MemoryStore db and apply any setup mutations via the store."""
    store = MemoryStore(db_path=path)
    try:
        for mutate in mutations:
            mutate(store)
    finally:
        store.close()
    return path


def _fact_row(db_path, fact_id):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT content, category, trust_score FROM facts WHERE fact_id = ?",
            (fact_id,),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def _audit_rows(db_path, fact_id):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM fact_governance_audit WHERE fact_id = ? ORDER BY audit_id",
            (fact_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


class TestDryRunPreviewUpdate:
    def test_update_preview_shows_changed_fields_and_writes_nothing(
        self, tmp_path, capsys
    ):
        db = tmp_path / "target.db"
        holder = {}

        def _seed(store):
            holder["fact_id"] = store.add_fact("Original content", category="general")

        _make_governed_db(db, [_seed])
        fact_id = holder["fact_id"]

        before = _mtime_and_hash(db)

        rc = memory_governance.main(
            [
                "--db", str(db),
                "--action", "update",
                "--fact-id", str(fact_id),
                "--reason", "fix typo",
                "--content", "Corrected content",
            ]
        )
        out = json.loads(capsys.readouterr().out)

        after = _mtime_and_hash(db)

        assert rc == 0
        assert before == after
        assert out["dry_run"] is True
        assert out["applied"] is False
        assert out["changed_fields"] == ["content"]
        assert out["changed"]["content"] == {
            "old": "Original content",
            "new": "Corrected content",
        }
        assert out["noop"] is False
        assert out["current"]["content"] == "Original content"

        # Nothing actually changed on disk, and no audit row was written.
        assert _fact_row(db, fact_id)["content"] == "Original content"
        assert _audit_rows(db, fact_id) == []

    def test_update_preview_noop_when_values_match_current(self, tmp_path, capsys):
        db = tmp_path / "target.db"
        holder = {}

        def _seed(store):
            holder["fact_id"] = store.add_fact("Stable content", category="general")

        _make_governed_db(db, [_seed])
        fact_id = holder["fact_id"]

        rc = memory_governance.main(
            [
                "--db", str(db),
                "--action", "update",
                "--fact-id", str(fact_id),
                "--reason", "confirming still correct",
                "--content", "Stable content",
            ]
        )
        out = json.loads(capsys.readouterr().out)

        assert rc == 0
        assert out["noop"] is True
        assert out["changed_fields"] == []
        assert out["changed"] == {}

    def test_update_preview_trust_score_and_category(self, tmp_path, capsys):
        db = tmp_path / "target.db"
        holder = {}

        def _seed(store):
            holder["fact_id"] = store.add_fact("A fact", category="general")

        _make_governed_db(db, [_seed])
        fact_id = holder["fact_id"]

        rc = memory_governance.main(
            [
                "--db", str(db),
                "--action", "update",
                "--fact-id", str(fact_id),
                "--reason", "recategorize and boost trust",
                "--category", "project",
                "--trust-score", "0.9",
            ]
        )
        out = json.loads(capsys.readouterr().out)

        assert rc == 0
        assert set(out["changed_fields"]) == {"category", "trust_score"}
        assert out["changed"]["category"] == {"old": "general", "new": "project"}
        assert out["changed"]["trust_score"] == {"old": 0.5, "new": 0.9}


class TestDryRunPreviewForget:
    def test_forget_preview_marks_would_remove_and_writes_nothing(
        self, tmp_path, capsys
    ):
        db = tmp_path / "target.db"
        holder = {}

        def _seed(store):
            holder["fact_id"] = store.add_fact("Fact to forget", category="general")

        _make_governed_db(db, [_seed])
        fact_id = holder["fact_id"]

        before = _mtime_and_hash(db)

        rc = memory_governance.main(
            [
                "--db", str(db),
                "--action", "forget",
                "--fact-id", str(fact_id),
                "--reason", "user asked to delete this",
                "--confirm-forget",
            ]
        )
        out = json.loads(capsys.readouterr().out)

        after = _mtime_and_hash(db)

        assert rc == 0
        assert before == after
        assert out["dry_run"] is True
        assert out["applied"] is False
        assert out["would_remove"] is True
        assert out["current"]["content"] == "Fact to forget"

        assert _fact_row(db, fact_id) is not None
        assert _audit_rows(db, fact_id) == []

    def test_forget_preview_requires_confirm_forget(self, tmp_path, capsys):
        db = tmp_path / "target.db"
        holder = {}

        def _seed(store):
            holder["fact_id"] = store.add_fact("A fact")

        _make_governed_db(db, [_seed])
        fact_id = holder["fact_id"]

        rc = memory_governance.main(
            [
                "--db", str(db),
                "--action", "forget",
                "--fact-id", str(fact_id),
                "--reason", "cleanup",
            ]
        )
        captured = capsys.readouterr()

        assert rc == 2
        assert "--confirm-forget" in captured.err
        assert captured.out == ""


class TestDryRunNeverImportsMemoryStore:
    def test_dry_run_does_not_create_missing_db(self, tmp_path, capsys):
        # MemoryStore's constructor would create a missing db file/schema on
        # open -- dry-run must never construct one, so a missing --db is a
        # clear error, not an auto-created empty database.
        missing = tmp_path / "does_not_exist.db"

        rc = memory_governance.main(
            [
                "--db", str(missing),
                "--action", "update",
                "--fact-id", "1",
                "--reason", "test",
                "--content", "New content",
            ]
        )
        captured = capsys.readouterr()

        assert rc == 1
        assert "database not found" in captured.err
        assert not missing.exists()

    def test_dry_run_never_creates_wal_or_shm_sidecar_files(self, tmp_path, capsys):
        db = tmp_path / "target.db"
        holder = {}

        def _seed(store):
            holder["fact_id"] = store.add_fact("A fact")

        _make_governed_db(db, [_seed])
        fact_id = holder["fact_id"]

        wal = tmp_path / "target.db-wal"
        shm = tmp_path / "target.db-shm"
        assert not wal.exists()
        assert not shm.exists()

        rc = memory_governance.main(
            [
                "--db", str(db),
                "--action", "update",
                "--fact-id", str(fact_id),
                "--reason", "test",
                "--content", "New content",
            ]
        )
        capsys.readouterr()

        assert rc == 0
        assert not wal.exists()
        assert not shm.exists()


class TestApplyWritesWithBackupAndAudit:
    def test_apply_update_writes_backup_and_audit_row(self, tmp_path, capsys):
        db = tmp_path / "target.db"
        holder = {}

        def _seed(store):
            holder["fact_id"] = store.add_fact("Original content", category="general")

        _make_governed_db(db, [_seed])
        fact_id = holder["fact_id"]
        backup_dir = tmp_path / "backups"

        rc = memory_governance.main(
            [
                "--db", str(db),
                "--action", "update",
                "--fact-id", str(fact_id),
                "--reason", "fix typo",
                "--content", "Corrected content",
                "--apply",
                "--backup-dir", str(backup_dir),
            ]
        )
        out = json.loads(capsys.readouterr().out)

        assert rc == 0
        assert out["dry_run"] is False
        assert out["applied"] is True
        assert out["result"]["changed_fields"] == ["content"]

        # DB actually mutated.
        assert _fact_row(db, fact_id)["content"] == "Corrected content"

        # Audit row recorded.
        audit = _audit_rows(db, fact_id)
        assert len(audit) == 1
        assert audit[0]["action"] == "update"
        assert audit[0]["old_content"] == "Original content"
        assert audit[0]["new_content"] == "Corrected content"
        assert audit[0]["reason"] == "fix typo"

        # Backup was taken and matches the pre-mutation content byte-for-byte.
        backups = list(backup_dir.glob("target.db.*.bak"))
        assert len(backups) == 1
        assert out["backup"]["db_backup"] == str(backups[0])
        conn = sqlite3.connect(backups[0])
        try:
            row = conn.execute(
                "SELECT content FROM facts WHERE fact_id = ?", (fact_id,)
            ).fetchone()
        finally:
            conn.close()
        assert row[0] == "Original content"

    def test_apply_forget_removes_fact_writes_backup_and_audit_row(
        self, tmp_path, capsys
    ):
        db = tmp_path / "target.db"
        holder = {}

        def _seed(store):
            holder["fact_id"] = store.add_fact("Fact to forget", category="general")

        _make_governed_db(db, [_seed])
        fact_id = holder["fact_id"]
        backup_dir = tmp_path / "backups"

        rc = memory_governance.main(
            [
                "--db", str(db),
                "--action", "forget",
                "--fact-id", str(fact_id),
                "--reason", "user asked to delete this",
                "--confirm-forget",
                "--apply",
                "--backup-dir", str(backup_dir),
            ]
        )
        out = json.loads(capsys.readouterr().out)

        assert rc == 0
        assert out["applied"] is True
        assert out["result"]["removed"] is True

        assert _fact_row(db, fact_id) is None

        audit = _audit_rows(db, fact_id)
        assert len(audit) == 1
        assert audit[0]["action"] == "forget"
        assert audit[0]["old_content"] == "Fact to forget"

        backups = list(backup_dir.glob("target.db.*.bak"))
        assert len(backups) == 1

    def test_apply_update_noop_still_backs_up_and_audits_attempt(
        self, tmp_path, capsys
    ):
        db = tmp_path / "target.db"
        holder = {}

        def _seed(store):
            holder["fact_id"] = store.add_fact("Stable content", category="general")

        _make_governed_db(db, [_seed])
        fact_id = holder["fact_id"]
        backup_dir = tmp_path / "backups"

        rc = memory_governance.main(
            [
                "--db", str(db),
                "--action", "update",
                "--fact-id", str(fact_id),
                "--reason", "confirming still correct",
                "--content", "Stable content",
                "--apply",
                "--backup-dir", str(backup_dir),
            ]
        )
        out = json.loads(capsys.readouterr().out)

        assert rc == 0
        assert out["result"]["noop"] is True
        assert list(backup_dir.glob("target.db.*.bak"))
        audit = _audit_rows(db, fact_id)
        assert len(audit) == 1
        assert audit[0]["reason"] == "confirming still correct"

    def test_apply_creates_backup_dir_if_missing(self, tmp_path, capsys):
        db = tmp_path / "target.db"
        holder = {}

        def _seed(store):
            holder["fact_id"] = store.add_fact("A fact")

        _make_governed_db(db, [_seed])
        fact_id = holder["fact_id"]
        backup_dir = tmp_path / "nested" / "backups"
        assert not backup_dir.exists()

        rc = memory_governance.main(
            [
                "--db", str(db),
                "--action", "forget",
                "--fact-id", str(fact_id),
                "--reason", "cleanup",
                "--confirm-forget",
                "--apply",
                "--backup-dir", str(backup_dir),
            ]
        )
        capsys.readouterr()

        assert rc == 0
        assert backup_dir.exists()
        assert list(backup_dir.glob("target.db.*.bak"))


class TestApplyRequiresConfirmations:
    def test_apply_without_backup_dir_rejected(self, tmp_path, capsys):
        db = tmp_path / "target.db"
        holder = {}

        def _seed(store):
            holder["fact_id"] = store.add_fact("A fact")

        _make_governed_db(db, [_seed])
        fact_id = holder["fact_id"]

        rc = memory_governance.main(
            [
                "--db", str(db),
                "--action", "update",
                "--fact-id", str(fact_id),
                "--reason", "test",
                "--content", "New content",
                "--apply",
            ]
        )
        captured = capsys.readouterr()

        assert rc == 2
        assert "--apply requires --backup-dir" in captured.err
        assert captured.out == ""
        assert _fact_row(db, fact_id)["content"] == "A fact"

    def test_apply_forget_without_confirm_forget_rejected(self, tmp_path, capsys):
        db = tmp_path / "target.db"
        holder = {}

        def _seed(store):
            holder["fact_id"] = store.add_fact("A fact")

        _make_governed_db(db, [_seed])
        fact_id = holder["fact_id"]
        backup_dir = tmp_path / "backups"

        rc = memory_governance.main(
            [
                "--db", str(db),
                "--action", "forget",
                "--fact-id", str(fact_id),
                "--reason", "cleanup",
                "--apply",
                "--backup-dir", str(backup_dir),
            ]
        )
        captured = capsys.readouterr()

        assert rc == 2
        assert "--confirm-forget" in captured.err
        assert not backup_dir.exists()
        assert _fact_row(db, fact_id) is not None


class TestGuardedRealPaths:
    def test_basename_guarded_db_rejected_without_allow_real_paths(
        self, tmp_path, capsys
    ):
        guarded_db = tmp_path / "memory_store.db"
        holder = {}

        def _seed(store):
            holder["fact_id"] = store.add_fact("A fact")

        _make_governed_db(guarded_db, [_seed])
        fact_id = holder["fact_id"]

        rc = memory_governance.main(
            [
                "--db", str(guarded_db),
                "--action", "update",
                "--fact-id", str(fact_id),
                "--reason", "test",
                "--content", "New content",
            ]
        )
        captured = capsys.readouterr()

        assert rc == 2
        assert "looks like a real Hermes database path" in captured.err
        assert captured.out == ""

    def test_dot_hermes_directory_rejected_without_touching_filesystem(
        self, tmp_path, capsys
    ):
        fake_db = os.path.join(os.path.expanduser("~"), ".hermes", "memory_store_copy.db")

        rc = memory_governance.main(
            [
                "--db", fake_db,
                "--action", "update",
                "--fact-id", "1",
                "--reason", "test",
                "--content", "New content",
            ]
        )
        captured = capsys.readouterr()

        assert rc == 2
        assert "looks like a real Hermes database path" in captured.err
        assert not os.path.exists(fake_db)

    def test_allow_real_paths_lifts_guard_for_tmp_path_dry_run(self, tmp_path, capsys):
        guarded_looking_db = tmp_path / "state.db"
        holder = {}

        def _seed(store):
            holder["fact_id"] = store.add_fact("A fact")

        _make_governed_db(guarded_looking_db, [_seed])
        fact_id = holder["fact_id"]

        rc = memory_governance.main(
            [
                "--db", str(guarded_looking_db),
                "--action", "update",
                "--fact-id", str(fact_id),
                "--reason", "test",
                "--content", "New content",
                "--allow-real-paths",
            ]
        )
        out = json.loads(capsys.readouterr().out)

        assert rc == 0
        assert out["dry_run"] is True

    def test_apply_with_allow_real_paths_requires_confirm_real_governance(
        self, tmp_path, capsys
    ):
        guarded_looking_db = tmp_path / "state.db"
        holder = {}

        def _seed(store):
            holder["fact_id"] = store.add_fact("A fact")

        _make_governed_db(guarded_looking_db, [_seed])
        fact_id = holder["fact_id"]
        backup_dir = tmp_path / "backups"

        rc = memory_governance.main(
            [
                "--db", str(guarded_looking_db),
                "--action", "update",
                "--fact-id", str(fact_id),
                "--reason", "test",
                "--content", "New content",
                "--allow-real-paths",
                "--apply",
                "--backup-dir", str(backup_dir),
            ]
        )
        captured = capsys.readouterr()

        assert rc == 2
        assert "--confirm-real-governance" in captured.err
        assert not backup_dir.exists()
        assert _fact_row(guarded_looking_db, fact_id)["content"] == "A fact"

    def test_apply_with_allow_real_paths_and_confirm_real_governance_succeeds(
        self, tmp_path, capsys
    ):
        # Still a tmp_path db that merely shares a guarded basename -- the
        # path guard is a name/location heuristic, not a filesystem check
        # against the real ~/.hermes, so this must be allowed to apply once
        # both confirmation flags are present.
        guarded_looking_db = tmp_path / "state.db"
        holder = {}

        def _seed(store):
            holder["fact_id"] = store.add_fact("A fact")

        _make_governed_db(guarded_looking_db, [_seed])
        fact_id = holder["fact_id"]
        backup_dir = tmp_path / "backups"

        rc = memory_governance.main(
            [
                "--db", str(guarded_looking_db),
                "--action", "update",
                "--fact-id", str(fact_id),
                "--reason", "test",
                "--content", "New content",
                "--allow-real-paths",
                "--confirm-real-governance",
                "--apply",
                "--backup-dir", str(backup_dir),
            ]
        )
        out = json.loads(capsys.readouterr().out)

        assert rc == 0
        assert out["applied"] is True
        assert _fact_row(guarded_looking_db, fact_id)["content"] == "New content"
        assert list(backup_dir.glob("state.db.*.bak"))


class TestInvalidArguments:
    def test_missing_db_arg_is_argparse_error(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            memory_governance.main(
                ["--action", "update", "--fact-id", "1", "--reason", "test"]
            )
        assert exc_info.value.code == 2
        assert "--db" in capsys.readouterr().err

    def test_missing_action_arg_is_argparse_error(self, tmp_path, capsys):
        with pytest.raises(SystemExit) as exc_info:
            memory_governance.main(
                ["--db", str(tmp_path / "x.db"), "--fact-id", "1", "--reason", "test"]
            )
        assert exc_info.value.code == 2
        assert "--action" in capsys.readouterr().err

    def test_missing_fact_id_arg_is_argparse_error(self, tmp_path, capsys):
        with pytest.raises(SystemExit) as exc_info:
            memory_governance.main(
                ["--db", str(tmp_path / "x.db"), "--action", "update", "--reason", "test"]
            )
        assert exc_info.value.code == 2
        assert "--fact-id" in capsys.readouterr().err

    def test_missing_reason_arg_is_argparse_error(self, tmp_path, capsys):
        with pytest.raises(SystemExit) as exc_info:
            memory_governance.main(
                ["--db", str(tmp_path / "x.db"), "--action", "update", "--fact-id", "1"]
            )
        assert exc_info.value.code == 2
        assert "--reason" in capsys.readouterr().err

    def test_invalid_action_choice_is_argparse_error(self, tmp_path, capsys):
        with pytest.raises(SystemExit) as exc_info:
            memory_governance.main(
                [
                    "--db", str(tmp_path / "x.db"),
                    "--action", "delete",
                    "--fact-id", "1",
                    "--reason", "test",
                ]
            )
        assert exc_info.value.code == 2
        assert "--action" in capsys.readouterr().err

    def test_zero_or_negative_fact_id_rejected(self, tmp_path, capsys):
        rc = memory_governance.main(
            [
                "--db", str(tmp_path / "x.db"),
                "--action", "update",
                "--fact-id", "0",
                "--reason", "test",
                "--content", "New content",
            ]
        )
        captured = capsys.readouterr()

        assert rc == 2
        assert "--fact-id must be a positive integer" in captured.err
        assert captured.out == ""

    def test_blank_reason_rejected(self, tmp_path, capsys):
        rc = memory_governance.main(
            [
                "--db", str(tmp_path / "x.db"),
                "--action", "update",
                "--fact-id", "1",
                "--reason", "   ",
                "--content", "New content",
            ]
        )
        captured = capsys.readouterr()

        assert rc == 2
        assert "--reason must not be blank" in captured.err

    def test_update_with_no_fields_rejected(self, tmp_path, capsys):
        rc = memory_governance.main(
            [
                "--db", str(tmp_path / "x.db"),
                "--action", "update",
                "--fact-id", "1",
                "--reason", "test",
            ]
        )
        captured = capsys.readouterr()

        assert rc == 2
        assert "at least one of" in captured.err

    def test_update_blank_content_rejected(self, tmp_path, capsys):
        rc = memory_governance.main(
            [
                "--db", str(tmp_path / "x.db"),
                "--action", "update",
                "--fact-id", "1",
                "--reason", "test",
                "--content", "   ",
            ]
        )
        captured = capsys.readouterr()

        assert rc == 2
        assert "--content must not be empty" in captured.err

    def test_update_out_of_range_trust_score_rejected(self, tmp_path, capsys):
        rc = memory_governance.main(
            [
                "--db", str(tmp_path / "x.db"),
                "--action", "update",
                "--fact-id", "1",
                "--reason", "test",
                "--trust-score", "1.5",
            ]
        )
        captured = capsys.readouterr()

        assert rc == 2
        assert "--trust-score must be between" in captured.err

    def test_update_negative_trust_score_rejected(self, tmp_path, capsys):
        rc = memory_governance.main(
            [
                "--db", str(tmp_path / "x.db"),
                "--action", "update",
                "--fact-id", "1",
                "--reason", "test",
                "--trust-score", "-0.1",
            ]
        )
        captured = capsys.readouterr()

        assert rc == 2
        assert "--trust-score must be between" in captured.err

    def test_invalid_arguments_checked_before_any_db_access(self, tmp_path, capsys):
        # A --db that does not exist would normally raise "database not
        # found", but argument validation must happen first so the error
        # is about the bad argument, not the missing db.
        rc = memory_governance.main(
            [
                "--db", str(tmp_path / "does_not_exist.db"),
                "--action", "update",
                "--fact-id", "-5",
                "--reason", "test",
                "--content", "New content",
            ]
        )
        captured = capsys.readouterr()

        assert rc == 2
        assert "--fact-id must be a positive integer" in captured.err


class TestNonexistentFact:
    def test_update_nonexistent_fact_id_dry_run_is_clear_error(self, tmp_path, capsys):
        db = tmp_path / "target.db"
        _make_governed_db(db, [lambda store: store.add_fact("A fact")])

        rc = memory_governance.main(
            [
                "--db", str(db),
                "--action", "update",
                "--fact-id", "999999",
                "--reason", "test",
                "--content", "New content",
            ]
        )
        captured = capsys.readouterr()

        assert rc == 1
        assert "fact_id 999999 not found" in captured.err
        assert captured.out == ""

    def test_forget_nonexistent_fact_id_apply_is_clear_error_and_no_backup_taken(
        self, tmp_path, capsys
    ):
        db = tmp_path / "target.db"
        _make_governed_db(db, [lambda store: store.add_fact("A fact")])
        backup_dir = tmp_path / "backups"

        rc = memory_governance.main(
            [
                "--db", str(db),
                "--action", "forget",
                "--fact-id", "999999",
                "--reason", "test",
                "--confirm-forget",
                "--apply",
                "--backup-dir", str(backup_dir),
            ]
        )
        captured = capsys.readouterr()

        assert rc == 1
        assert "fact_id 999999 not found" in captured.err
        assert captured.out == ""
        # The fact-existence check happens before the backup is taken.
        assert not backup_dir.exists()


class TestBackupFailure:
    def test_apply_aborts_and_writes_nothing_when_backup_fails(
        self, tmp_path, capsys, monkeypatch
    ):
        db = tmp_path / "target.db"
        holder = {}

        def _seed(store):
            holder["fact_id"] = store.add_fact("Original content", category="general")

        _make_governed_db(db, [_seed])
        fact_id = holder["fact_id"]
        backup_dir = tmp_path / "backups"

        def _boom(*args, **kwargs):
            raise OSError("simulated disk failure")

        monkeypatch.setattr(memory_governance.shutil, "copyfile", _boom)

        before = _mtime_and_hash(db)

        rc = memory_governance.main(
            [
                "--db", str(db),
                "--action", "update",
                "--fact-id", str(fact_id),
                "--reason", "fix typo",
                "--content", "Corrected content",
                "--apply",
                "--backup-dir", str(backup_dir),
            ]
        )
        captured = capsys.readouterr()

        after = _mtime_and_hash(db)

        assert rc == 1
        assert "backup failed" in captured.err
        assert "nothing written to --db" in captured.err
        assert captured.out == ""

        # DB untouched: same bytes/mtime, and no audit row.
        assert before == after
        assert _fact_row(db, fact_id)["content"] == "Original content"
        assert _audit_rows(db, fact_id) == []

    def test_apply_forget_aborts_when_backup_fails(self, tmp_path, capsys, monkeypatch):
        db = tmp_path / "target.db"
        holder = {}

        def _seed(store):
            holder["fact_id"] = store.add_fact("Fact to forget", category="general")

        _make_governed_db(db, [_seed])
        fact_id = holder["fact_id"]
        backup_dir = tmp_path / "backups"

        def _boom(*args, **kwargs):
            raise OSError("simulated disk failure")

        monkeypatch.setattr(memory_governance.shutil, "copyfile", _boom)

        rc = memory_governance.main(
            [
                "--db", str(db),
                "--action", "forget",
                "--fact-id", str(fact_id),
                "--reason", "cleanup",
                "--confirm-forget",
                "--apply",
                "--backup-dir", str(backup_dir),
            ]
        )
        captured = capsys.readouterr()

        assert rc == 1
        assert "backup failed" in captured.err

        assert _fact_row(db, fact_id) is not None
        assert _audit_rows(db, fact_id) == []


class TestOutputContract:
    def test_dry_run_stdout_is_valid_json_and_stderr_empty_on_success(
        self, tmp_path, capsys
    ):
        db = tmp_path / "target.db"
        holder = {}

        def _seed(store):
            holder["fact_id"] = store.add_fact("A fact")

        _make_governed_db(db, [_seed])
        fact_id = holder["fact_id"]

        rc = memory_governance.main(
            [
                "--db", str(db),
                "--action", "update",
                "--fact-id", str(fact_id),
                "--reason", "test",
                "--content", "New content",
            ]
        )
        captured = capsys.readouterr()

        assert rc == 0
        assert captured.err == ""
        parsed = json.loads(captured.out)
        top_level_keys = list(parsed.keys())
        assert top_level_keys == sorted(top_level_keys)

    def test_apply_stdout_is_valid_json_and_stderr_empty_on_success(
        self, tmp_path, capsys
    ):
        db = tmp_path / "target.db"
        holder = {}

        def _seed(store):
            holder["fact_id"] = store.add_fact("A fact")

        _make_governed_db(db, [_seed])
        fact_id = holder["fact_id"]
        backup_dir = tmp_path / "backups"

        rc = memory_governance.main(
            [
                "--db", str(db),
                "--action", "update",
                "--fact-id", str(fact_id),
                "--reason", "test",
                "--content", "New content",
                "--apply",
                "--backup-dir", str(backup_dir),
            ]
        )
        captured = capsys.readouterr()

        assert rc == 0
        assert captured.err == ""
        parsed = json.loads(captured.out)
        top_level_keys = list(parsed.keys())
        assert top_level_keys == sorted(top_level_keys)
