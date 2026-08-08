#!/usr/bin/env python3
"""Offline migration: legacy ``agent_memory`` facts -> ``MemoryStore`` facts.

Migrates rows from a legacy ``agent_memory`` SQLite table (columns: ``fact``,
``category``, ``confidence``, ``source``, ``expires_at``) into a
``plugins.memory.holographic.store.MemoryStore``-compatible ``facts`` table,
per ``memoria_activa_architecture.md`` Capa 2 / ``docs/personal-system/ROADMAP.md``
Phase 5A.

Safety model
------------

- **Dry-run is the default.** Unless ``--apply`` is passed, nothing is ever
  written to ``--target`` and no backup is taken. The dry-run path never
  imports ``MemoryStore`` and never resolves ``HERMES_HOME`` — it reads
  ``--source`` and (optionally) ``--target`` with plain read-only ``sqlite3``
  connections only.
- **``--apply`` requires ``--backup-dir``.** Byte-for-byte, timestamped
  copies of ``--source`` and (if it exists) ``--target`` are made before any
  write. If the backup fails, nothing is written.
- **Real Hermes paths are refused by default.** Any ``--source``/``--target``
  under ``~/.hermes`` or ``~/.hermes-enhanced``, or literally named
  ``agent_memory.db``, ``memory_store.db``, ``state.db``, or ``bujo.sqlite``,
  is rejected unless ``--allow-real-paths`` is passed. ``--apply`` on such a
  path additionally requires ``--confirm-real-migration``. This script is
  built and tested to run entirely offline, against throwaway copies — see
  ``docs/personal-system/ROADMAP.md`` Phase 5A. It has never been run against
  a real database.
- **Only the ``agent_memory`` table is ever read from ``--source``.** Even if
  ``user_profile``, ``pattern_log``, or any other table lives in the same
  source file, this script issues no SQL against them. ``--target`` is only
  ever written through ``MemoryStore.add_fact``/``update_fact``, which only
  touch the ``facts``/``entities``/``fact_entities``/``memory_banks`` tables.
  ``state.db`` and ``bujo.sqlite`` are never opened by this script at all.
- **No dates or sessions are invented.** ``expires_at`` is copied verbatim
  (or left ``NULL``); ``session_id`` is always ``NULL`` since legacy
  ``agent_memory`` rows carry no session provenance.

Transform
---------

====================  =======================================================
agent_memory column   facts column
====================  =======================================================
``fact``               ``content`` (stripped; empty/NULL -> invalid row)
``category``           ``category`` (kept as-is; NULL/empty -> ``"general"``)
``confidence``         ``trust_score`` (clamped to ``[0, 1]``; NULL -> ``0.5``)
``expires_at``         ``expires_at`` (kept as-is)
``source``              ``fact_type``: ``"manual"`` -> ``"explicit"``,
                        ``"auto"`` -> ``"extracted"``, anything else
                        (including NULL) -> ``"pattern"``
(none)                 ``session_id`` -> always ``NULL``
====================  =======================================================

Deduplication matches ``MemoryStore.add_fact``: unique by (stripped)
``content``. A row whose content already exists in ``--target``, or that
repeats a content already seen earlier in ``--source``, is reported as a
duplicate and never inserted or modified.

Usage
-----

.. code-block:: text

    # Preview only (default) — prints a JSON summary, writes nothing.
    python3 scripts/memory_migrate.py --source agent_memory.db --target memory_store.db

    # Apply — requires an explicit backup directory.
    python3 scripts/memory_migrate.py --source agent_memory.db --target memory_store.db \\
        --apply --backup-dir ./migration-backups
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Allow importing plugins.memory.holographic when run as a plain script.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_GUARDED_DIR_NAMES = (".hermes", ".hermes-enhanced")
_GUARDED_BASENAMES = frozenset(
    {"agent_memory.db", "memory_store.db", "state.db", "bujo.sqlite"}
)

_TRUST_MIN, _TRUST_MAX = 0.0, 1.0
_DEFAULT_TRUST = 0.5
_DEFAULT_CATEGORY = "general"

_SOURCE_TO_FACT_TYPE = {"manual": "explicit", "auto": "extracted"}
_DEFAULT_FACT_TYPE = "pattern"

_SOURCE_TABLE = "agent_memory"
_SOURCE_OPTIONAL_COLUMNS = ("category", "confidence", "source", "expires_at")


class MigrationError(RuntimeError):
    """Fatal migration error: bad path, missing table, or failed backup."""


@dataclass
class TransformedFact:
    rowid: int
    content: str
    category: str
    trust_score: float
    fact_type: str
    expires_at: "Optional[str]"


@dataclass
class MigrationPlan:
    total: int
    to_insert: "list[TransformedFact]" = field(default_factory=list)
    duplicate_rowids: "list[int]" = field(default_factory=list)
    invalid: "list[dict]" = field(default_factory=list)

    def summary(self, *, source: Path, target: Path, dry_run: bool) -> dict:
        return {
            "source": str(source),
            "target": str(target),
            "dry_run": dry_run,
            "total": self.total,
            "insertable": len(self.to_insert),
            "duplicate": len(self.duplicate_rowids),
            "invalid": len(self.invalid),
            "errors": self.invalid,
        }


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def is_guarded_path(path: "str | Path") -> bool:
    """True if `path` looks like a real Hermes database path.

    Pure string manipulation only (``expanduser``/``normpath`` — no
    ``stat``/``resolve``), so this never touches the filesystem, including
    a real ``~/.hermes`` if one exists on the machine running this check.
    """
    normalized = os.path.normpath(os.path.expanduser(str(path)))
    if os.path.basename(normalized) in _GUARDED_BASENAMES:
        return True
    home = os.path.expanduser("~")
    for name in _GUARDED_DIR_NAMES:
        guarded_dir = os.path.normpath(os.path.join(home, name))
        if normalized == guarded_dir or normalized.startswith(guarded_dir + os.sep):
            return True
    return False


def _transform_row(row: sqlite3.Row) -> "tuple[Optional[TransformedFact], Optional[str]]":
    """Return (TransformedFact, None) or (None, invalid-reason)."""
    raw_fact = row["fact"]
    content = raw_fact.strip() if isinstance(raw_fact, str) else ""
    if not content:
        return None, "empty or missing 'fact'"

    raw_category = row["category"]
    category = raw_category if raw_category else _DEFAULT_CATEGORY

    confidence = row["confidence"]
    if confidence is None:
        trust_score = _DEFAULT_TRUST
    else:
        try:
            trust_score = _clamp(float(confidence), _TRUST_MIN, _TRUST_MAX)
        except (TypeError, ValueError):
            return None, f"non-numeric confidence: {confidence!r}"

    raw_source = row["source"]
    source_key = raw_source.strip().lower() if isinstance(raw_source, str) else ""
    fact_type = _SOURCE_TO_FACT_TYPE.get(source_key, _DEFAULT_FACT_TYPE)

    expires_at = row["expires_at"]

    return (
        TransformedFact(
            rowid=row["_source_rowid"],
            content=content,
            category=category,
            trust_score=trust_score,
            fact_type=fact_type,
            expires_at=expires_at,
        ),
        None,
    )


def _read_source_rows(conn: sqlite3.Connection) -> "list[sqlite3.Row]":
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if _SOURCE_TABLE not in tables:
        raise MigrationError(
            f"source database has no '{_SOURCE_TABLE}' table — nothing to migrate"
        )

    columns = {r[1] for r in conn.execute(f"PRAGMA table_info({_SOURCE_TABLE})")}
    if "fact" not in columns:
        raise MigrationError(f"'{_SOURCE_TABLE}' table has no 'fact' column")

    select_cols = ["rowid AS _source_rowid", "fact"]
    for col in _SOURCE_OPTIONAL_COLUMNS:
        select_cols.append(col if col in columns else f"NULL AS {col}")

    sql = f"SELECT {', '.join(select_cols)} FROM {_SOURCE_TABLE} ORDER BY rowid"
    conn.row_factory = sqlite3.Row
    return conn.execute(sql).fetchall()


def _existing_target_content(target: Path) -> "set[str]":
    """Read-only peek at content already in --target's facts table, if any.

    Never writes, never creates the target file, never imports MemoryStore.
    """
    if not target.exists():
        return set()
    conn = sqlite3.connect(f"file:{target.as_posix()}?mode=ro", uri=True)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "facts" not in tables:
            return set()
        return {r[0] for r in conn.execute("SELECT content FROM facts")}
    finally:
        conn.close()


def build_plan(source: Path, target: Path) -> MigrationPlan:
    """Read --source and --target (read-only) and compute the migration plan.

    Never writes anything. Never imports MemoryStore or resolves
    HERMES_HOME.
    """
    if not source.exists():
        raise MigrationError(f"source database not found: {source}")

    conn = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    try:
        rows = _read_source_rows(conn)
    finally:
        conn.close()

    seen = _existing_target_content(target)
    plan = MigrationPlan(total=len(rows))

    for row in rows:
        fact, error = _transform_row(row)
        if error is not None:
            plan.invalid.append({"rowid": row["_source_rowid"], "reason": error})
            continue
        if fact.content in seen:
            plan.duplicate_rowids.append(fact.rowid)
            continue
        seen.add(fact.content)
        plan.to_insert.append(fact)

    return plan


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def create_backups(source: Path, target: Path, backup_dir: Path) -> dict:
    """Byte-for-byte, timestamped copies of source and (if present) target.

    Raises OSError (unchanged) on any failure — callers must treat that as
    "do not proceed to write --target".
    """
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = _timestamp()

    result: dict = {}
    source_backup = backup_dir / f"{source.name}.{ts}.bak"
    shutil.copyfile(source, source_backup)
    result["source_backup"] = str(source_backup)

    if target.exists():
        target_backup = backup_dir / f"{target.name}.{ts}.bak"
        shutil.copyfile(target, target_backup)
        result["target_backup"] = str(target_backup)

    return result


def apply_migration(plan: MigrationPlan, target: Path) -> dict:
    """Write plan.to_insert into --target via MemoryStore(db_path=target).

    Uses an explicit db_path throughout — never the HERMES_HOME-derived
    default — and never deletes or modifies --source.
    """
    from plugins.memory.holographic.store import MemoryStore

    store = MemoryStore(db_path=target)
    try:
        inserted = 0
        for fact in plan.to_insert:
            fact_id = store.add_fact(
                fact.content,
                category=fact.category,
                tags="",
                session_id=None,
                fact_type=fact.fact_type,
                expires_at=fact.expires_at,
            )
            delta = fact.trust_score - store.default_trust
            if delta:
                store.update_fact(fact_id, trust_delta=delta)
            inserted += 1
        return {"inserted": inserted}
    finally:
        store.close()


def _parse_args(argv: "list[str] | None") -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Migrate legacy agent_memory facts into a MemoryStore-compatible "
            "facts database. Dry-run (preview only, no writes) is the default."
        )
    )
    parser.add_argument("--source", required=True, help="Path to the source SQLite database (agent_memory table).")
    parser.add_argument("--target", required=True, help="Path to the target MemoryStore-compatible SQLite database.")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview only, write nothing (default behavior even without this flag).",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually write to --target. Requires --backup-dir.",
    )
    parser.add_argument("--backup-dir", default=None, help="Directory for pre-write backups. Required with --apply.")
    parser.add_argument(
        "--allow-real-paths", action="store_true",
        help="Permit --source/--target under ~/.hermes, ~/.hermes-enhanced, or named "
             "agent_memory.db/memory_store.db/state.db/bujo.sqlite.",
    )
    parser.add_argument(
        "--confirm-real-migration", action="store_true",
        help="Required together with --apply and --allow-real-paths to write to a guarded real path.",
    )
    return parser.parse_args(argv)


def main(argv: "list[str] | None" = None) -> int:
    args = _parse_args(argv)

    if args.dry_run and args.apply:
        print("Error: pass either --dry-run or --apply, not both.", file=sys.stderr)
        return 2

    source = Path(args.source)
    target = Path(args.target)

    if not args.allow_real_paths:
        for label, path in (("--source", source), ("--target", target)):
            if is_guarded_path(path):
                print(
                    f"Error: {label} {path} looks like a real Hermes database path; "
                    "refusing without --allow-real-paths.",
                    file=sys.stderr,
                )
                return 2

    apply_mode = args.apply

    if apply_mode:
        if not args.backup_dir:
            print("Error: --apply requires --backup-dir.", file=sys.stderr)
            return 2
        if args.allow_real_paths and not args.confirm_real_migration:
            print(
                "Error: --apply with --allow-real-paths also requires "
                "--confirm-real-migration.",
                file=sys.stderr,
            )
            return 2

    try:
        plan = build_plan(source, target)
    except MigrationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not apply_mode:
        print(json.dumps(plan.summary(source=source, target=target, dry_run=True), indent=2, sort_keys=True))
        return 0

    backup_dir = Path(args.backup_dir)
    try:
        backups = create_backups(source, target, backup_dir)
    except OSError as exc:
        print(f"Error: backup failed, aborting apply (nothing written to --target): {exc}", file=sys.stderr)
        return 1

    try:
        result = apply_migration(plan, target)
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller, not swallowed
        print(f"Error: migration failed: {exc}", file=sys.stderr)
        return 1

    output = plan.summary(source=source, target=target, dry_run=False)
    output["applied"] = True
    output["inserted"] = result["inserted"]
    output["backups"] = backups
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
