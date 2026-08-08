"""Tests for scripts/memory_migrate.py: offline agent_memory -> facts migration.

Covers the safety model documented in the script's module docstring: dry-run
is the default and never writes; ``--apply`` requires ``--backup-dir`` and
takes byte-for-byte backups before any write; guarded real-Hermes-looking
paths are refused unless ``--allow-real-paths``, and ``--allow-real-paths``
without ``--confirm-real-migration`` is refused under ``--apply``; a source
database with no ``agent_memory`` table is a hard error; and ``user_profile``/
``pattern_log`` tables that happen to live in the same source file are never
queried. Also covers the manual/auto/other -> explicit/extracted/pattern
``fact_type`` mapping, confidence clamping to ``[0, 1]``, ``expires_at``
passthrough, ``session_id`` always ``NULL``, and dedup/idempotency across two
``--apply`` runs.

All source/target/backup paths live under ``tmp_path``, built with plain
``sqlite3`` connections. ``HERMES_HOME`` isolation is inherited from the
project conftest's autouse ``_hermetic_environment`` fixture; this suite
additionally never points ``--source``/``--target`` at the real ``HOME``, so
nothing here can touch a real ``~/.hermes`` even though ``HOME`` itself is
not redirected by conftest.
"""

from __future__ import annotations

import json
import os
import sqlite3

import pytest

import scripts.memory_migrate as memory_migrate
from plugins.memory.holographic.store import MemoryStore


@pytest.fixture(autouse=True)
def _clean_shared_registry():
    """Each test starts and ends with an empty MemoryStore shared-connection registry.

    ``apply_migration`` opens a ``MemoryStore`` against ``--target``, which
    goes through the same process-wide shared-connection registry as every
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


def _make_source_db(path, rows, extra_tables=False):
    """Create a throwaway sqlite db with an agent_memory table and given rows.

    rows: iterable of (fact, category, confidence, source, expires_at) tuples.
    """
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE agent_memory (
                fact       TEXT,
                category   TEXT,
                confidence REAL,
                source     TEXT,
                expires_at TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO agent_memory (fact, category, confidence, source, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        if extra_tables:
            conn.execute("CREATE TABLE user_profile (id INTEGER PRIMARY KEY, name TEXT)")
            conn.execute("INSERT INTO user_profile (name) VALUES ('Sebastian')")
            conn.execute("CREATE TABLE pattern_log (id INTEGER PRIMARY KEY, pattern TEXT)")
            conn.execute("INSERT INTO pattern_log (pattern) VALUES ('daily-standup')")
        conn.commit()
    finally:
        conn.close()
    return path


def _read_target_facts(target):
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM facts ORDER BY fact_id")]
    finally:
        conn.close()


class TestDryRunNeverWrites:
    def test_dry_run_default_creates_no_target(self, tmp_path, capsys):
        source = _make_source_db(tmp_path / "source.db", [("I like coffee", "habits", 0.9, "manual", None)])
        target = tmp_path / "target.db"

        rc = memory_migrate.main(["--source", str(source), "--target", str(target)])
        out = json.loads(capsys.readouterr().out)

        assert rc == 0
        assert not target.exists()
        assert out["dry_run"] is True
        assert out["insertable"] == 1
        assert out["duplicate"] == 0
        assert out["invalid"] == 0

    def test_explicit_dry_run_flag_also_creates_no_target(self, tmp_path, capsys):
        source = _make_source_db(tmp_path / "source.db", [("I like coffee", "habits", 0.9, "manual", None)])
        target = tmp_path / "target.db"

        rc = memory_migrate.main(["--source", str(source), "--target", str(target), "--dry-run"])
        capsys.readouterr()

        assert rc == 0
        assert not target.exists()

    def test_dry_run_and_apply_together_rejected(self, tmp_path, capsys):
        source = _make_source_db(tmp_path / "source.db", [("I like coffee", "habits", 0.9, "manual", None)])
        target = tmp_path / "target.db"

        rc = memory_migrate.main(
            ["--source", str(source), "--target", str(target), "--dry-run", "--apply"]
        )
        captured = capsys.readouterr()

        assert rc == 2
        assert not target.exists()
        assert "either --dry-run or --apply" in captured.err


class TestApplyRequiresBackupDir:
    def test_apply_without_backup_dir_rejected(self, tmp_path, capsys):
        source = _make_source_db(tmp_path / "source.db", [("I like coffee", "habits", 0.9, "manual", None)])
        target = tmp_path / "target.db"

        rc = memory_migrate.main(["--source", str(source), "--target", str(target), "--apply"])
        captured = capsys.readouterr()

        assert rc == 2
        assert "--apply requires --backup-dir" in captured.err
        assert not target.exists()


class TestApplyMigratesRows:
    def test_apply_creates_backups_and_migrates_with_mapping_and_clamping(self, tmp_path, capsys):
        source_path = tmp_path / "source.db"
        _make_source_db(
            source_path,
            [
                ("I like coffee", "habits", 0.9, "manual", None),
                ("Uses Vim", "tools", 0.3, "auto", "2027-01-01"),
                ("Some pattern noticed", "misc", 1.5, "heuristic", None),  # confidence > 1 clamps to 1.0
                ("Low confidence fact", None, -0.5, None, None),  # confidence < 0 clamps to 0.0
                ("   ", "habits", 0.5, "manual", None),  # empty after strip -> invalid
                ("Bad confidence", "habits", "bogus", "manual", None),  # non-numeric -> invalid
            ],
        )
        target = tmp_path / "target.db"
        backup_dir = tmp_path / "backups"

        rc = memory_migrate.main(
            [
                "--source", str(source_path),
                "--target", str(target),
                "--apply",
                "--backup-dir", str(backup_dir),
            ]
        )
        out = json.loads(capsys.readouterr().out)

        assert rc == 0
        assert out["applied"] is True
        assert out["insertable"] == 4
        assert out["inserted"] == 4
        assert out["duplicate"] == 0
        assert out["invalid"] == 2

        # Backups: source backup always made; no target backup since target
        # did not exist prior to this run.
        assert "source_backup" in out["backups"]
        assert "target_backup" not in out["backups"]
        source_backups = list(backup_dir.glob("source.db.*.bak"))
        assert len(source_backups) == 1
        assert source_backups[0].read_bytes() == source_path.read_bytes()

        assert target.exists()
        facts = {f["content"]: f for f in _read_target_facts(target)}

        assert facts["I like coffee"]["fact_type"] == "explicit"
        assert facts["I like coffee"]["trust_score"] == pytest.approx(0.9)
        assert facts["I like coffee"]["session_id"] is None
        assert facts["I like coffee"]["expires_at"] is None

        assert facts["Uses Vim"]["fact_type"] == "extracted"
        assert facts["Uses Vim"]["trust_score"] == pytest.approx(0.3)
        assert facts["Uses Vim"]["expires_at"] == "2027-01-01"
        assert facts["Uses Vim"]["session_id"] is None

        assert facts["Some pattern noticed"]["fact_type"] == "pattern"
        assert facts["Some pattern noticed"]["trust_score"] == pytest.approx(1.0)

        assert facts["Low confidence fact"]["fact_type"] == "pattern"
        assert facts["Low confidence fact"]["trust_score"] == pytest.approx(0.0)
        assert facts["Low confidence fact"]["category"] == "general"

        assert "   " not in facts
        assert "Bad confidence" not in facts

    def test_apply_takes_target_backup_when_target_preexists(self, tmp_path, capsys):
        source_path = tmp_path / "source.db"
        _make_source_db(source_path, [("First fact", "habits", 0.5, "manual", None)])
        target = tmp_path / "target.db"
        backup_dir = tmp_path / "backups"

        # Prime the target with an existing MemoryStore-compatible db.
        store = MemoryStore(db_path=target)
        store.add_fact("Pre-existing fact", category="general")
        store.close()

        rc = memory_migrate.main(
            [
                "--source", str(source_path),
                "--target", str(target),
                "--apply",
                "--backup-dir", str(backup_dir),
            ]
        )
        out = json.loads(capsys.readouterr().out)

        assert rc == 0
        assert "target_backup" in out["backups"]
        target_backups = list(backup_dir.glob("target.db.*.bak"))
        assert len(target_backups) == 1


class TestDedupAndIdempotency:
    def test_second_apply_run_is_idempotent(self, tmp_path, capsys):
        source_path = tmp_path / "source.db"
        _make_source_db(
            source_path,
            [
                ("I like coffee", "habits", 0.9, "manual", None),
                ("Uses Vim", "tools", 0.3, "auto", None),
            ],
        )
        target = tmp_path / "target.db"
        backup_dir = tmp_path / "backups"
        argv = [
            "--source", str(source_path),
            "--target", str(target),
            "--apply",
            "--backup-dir", str(backup_dir),
        ]

        rc1 = memory_migrate.main(argv)
        out1 = json.loads(capsys.readouterr().out)
        assert rc1 == 0
        assert out1["inserted"] == 2
        assert out1["duplicate"] == 0

        rc2 = memory_migrate.main(argv)
        out2 = json.loads(capsys.readouterr().out)
        assert rc2 == 0
        assert out2["inserted"] == 0
        assert out2["duplicate"] == 2

        facts = _read_target_facts(target)
        assert len(facts) == 2
        assert {f["content"] for f in facts} == {"I like coffee", "Uses Vim"}

    def test_duplicate_content_within_source_deduped(self, tmp_path, capsys):
        source_path = tmp_path / "source.db"
        _make_source_db(
            source_path,
            [
                ("Repeated fact", "habits", 0.9, "manual", None),
                ("Repeated fact", "habits", 0.1, "auto", None),
            ],
        )
        target = tmp_path / "target.db"
        backup_dir = tmp_path / "backups"

        rc = memory_migrate.main(
            [
                "--source", str(source_path),
                "--target", str(target),
                "--apply",
                "--backup-dir", str(backup_dir),
            ]
        )
        out = json.loads(capsys.readouterr().out)

        assert rc == 0
        assert out["inserted"] == 1
        assert out["duplicate"] == 1
        facts = _read_target_facts(target)
        assert len(facts) == 1
        # First-seen row wins (higher trust from the manual row).
        assert facts[0]["trust_score"] == pytest.approx(0.9)


class TestGuardedPathsRejected:
    def test_basename_guarded_target_rejected(self, tmp_path, capsys):
        source = _make_source_db(tmp_path / "source.db", [("fact", "cat", 0.5, "manual", None)])
        guarded_target = tmp_path / "state.db"

        rc = memory_migrate.main(["--source", str(source), "--target", str(guarded_target)])
        captured = capsys.readouterr()

        assert rc == 2
        assert "looks like a real Hermes database path" in captured.err
        assert not guarded_target.exists()

    def test_basename_guarded_source_rejected(self, tmp_path, capsys):
        # The legacy real-world name is itself guarded, even under tmp_path.
        guarded_source = tmp_path / "agent_memory.db"
        _make_source_db(guarded_source, [("fact", "cat", 0.5, "manual", None)])
        target = tmp_path / "target.db"

        rc = memory_migrate.main(["--source", str(guarded_source), "--target", str(target)])
        captured = capsys.readouterr()

        assert rc == 2
        assert "looks like a real Hermes database path" in captured.err
        assert not target.exists()

    def test_dot_hermes_directory_rejected_without_touching_filesystem(self, tmp_path, capsys):
        # is_guarded_path is pure string manipulation (no stat/resolve), so
        # this is safe to check even though the path is never created.
        fake_target = os.path.join(os.path.expanduser("~"), ".hermes", "some_migrated.db")
        source = _make_source_db(tmp_path / "source.db", [("fact", "cat", 0.5, "manual", None)])

        rc = memory_migrate.main(["--source", str(source), "--target", fake_target])
        captured = capsys.readouterr()

        assert rc == 2
        assert "looks like a real Hermes database path" in captured.err
        assert not os.path.exists(fake_target)

    def test_dot_hermes_enhanced_directory_rejected(self, tmp_path, capsys):
        fake_source = os.path.join(os.path.expanduser("~"), ".hermes-enhanced", "agent_memory_copy.db")
        target = tmp_path / "target.db"

        rc = memory_migrate.main(["--source", fake_source, "--target", str(target)])
        captured = capsys.readouterr()

        assert rc == 2
        assert "looks like a real Hermes database path" in captured.err
        assert not target.exists()


class TestAllowRealPathsWithoutConfirmRejected:
    def test_allow_real_paths_without_confirm_rejected_under_apply(self, tmp_path, capsys):
        source = _make_source_db(tmp_path / "source.db", [("fact", "cat", 0.5, "manual", None)])
        target = tmp_path / "target.db"
        backup_dir = tmp_path / "backups"

        rc = memory_migrate.main(
            [
                "--source", str(source),
                "--target", str(target),
                "--apply",
                "--backup-dir", str(backup_dir),
                "--allow-real-paths",
            ]
        )
        captured = capsys.readouterr()

        assert rc == 2
        assert "--confirm-real-migration" in captured.err
        assert not target.exists()
        assert not backup_dir.exists()

    def test_allow_real_paths_with_confirm_and_dry_run_flag_combo_still_requires_backup_dir(
        self, tmp_path, capsys
    ):
        # --allow-real-paths + --confirm-real-migration without --apply is a
        # no-op flag combo; dry-run behavior (no backup-dir required) still
        # applies since --apply was never passed.
        source = _make_source_db(tmp_path / "source.db", [("fact", "cat", 0.5, "manual", None)])
        target = tmp_path / "target.db"

        rc = memory_migrate.main(
            [
                "--source", str(source),
                "--target", str(target),
                "--allow-real-paths",
                "--confirm-real-migration",
            ]
        )
        capsys.readouterr()

        assert rc == 0
        assert not target.exists()


class TestMissingAgentMemoryTable:
    def test_missing_table_reported_in_dry_run(self, tmp_path, capsys):
        source = tmp_path / "source.db"
        conn = sqlite3.connect(source)
        conn.execute("CREATE TABLE unrelated (x TEXT)")
        conn.commit()
        conn.close()
        target = tmp_path / "target.db"

        rc = memory_migrate.main(["--source", str(source), "--target", str(target)])
        captured = capsys.readouterr()

        assert rc == 1
        assert "no 'agent_memory' table" in captured.err
        assert captured.out == ""
        assert not target.exists()

    def test_missing_table_reported_before_any_backup_under_apply(self, tmp_path, capsys):
        source = tmp_path / "source.db"
        conn = sqlite3.connect(source)
        conn.execute("CREATE TABLE unrelated (x TEXT)")
        conn.commit()
        conn.close()
        target = tmp_path / "target.db"
        backup_dir = tmp_path / "backups"

        rc = memory_migrate.main(
            [
                "--source", str(source),
                "--target", str(target),
                "--apply",
                "--backup-dir", str(backup_dir),
            ]
        )
        captured = capsys.readouterr()

        assert rc == 1
        assert "no 'agent_memory' table" in captured.err
        assert not target.exists()
        assert not backup_dir.exists()


class TestUserProfileAndPatternLogNotRead:
    def test_extra_tables_never_queried_in_dry_run(self, tmp_path, capsys, monkeypatch):
        source = _make_source_db(
            tmp_path / "source.db",
            [("I like coffee", "habits", 0.9, "manual", None)],
            extra_tables=True,
        )
        target = tmp_path / "target.db"

        # sqlite3.Connection is a C-level type: instances don't allow
        # arbitrary attribute assignment, so `conn.execute = ...` raises
        # AttributeError. Spy via a Connection subclass installed as the
        # `factory` instead, which sqlite3.connect() honors regardless of
        # the `uri=True` mode=ro connection the script opens.
        executed_sql: list[str] = []
        orig_connect = sqlite3.connect

        class _SpyConnection(sqlite3.Connection):
            def execute(self, sql, *a, **kw):
                executed_sql.append(sql)
                return super().execute(sql, *a, **kw)

        def _spy_connect(*args, **kwargs):
            kwargs.setdefault("factory", _SpyConnection)
            return orig_connect(*args, **kwargs)

        monkeypatch.setattr(sqlite3, "connect", _spy_connect)

        rc = memory_migrate.main(["--source", str(source), "--target", str(target)])
        out = json.loads(capsys.readouterr().out)

        assert rc == 0
        assert out["insertable"] == 1
        assert not any("user_profile" in sql for sql in executed_sql)
        assert not any("pattern_log" in sql for sql in executed_sql)

    def test_extra_table_content_never_migrated(self, tmp_path, capsys):
        source_path = tmp_path / "source.db"
        _make_source_db(
            source_path,
            [("I like coffee", "habits", 0.9, "manual", None)],
            extra_tables=True,
        )
        target = tmp_path / "target.db"
        backup_dir = tmp_path / "backups"

        rc = memory_migrate.main(
            [
                "--source", str(source_path),
                "--target", str(target),
                "--apply",
                "--backup-dir", str(backup_dir),
            ]
        )
        capsys.readouterr()

        assert rc == 0
        facts = _read_target_facts(target)
        assert len(facts) == 1
        contents = {f["content"] for f in facts}
        assert "Sebastian" not in contents
        assert "daily-standup" not in contents
