#!/usr/bin/env python3
"""CLI wrapper over ``MemoryStore.update_fact_audited``/``forget_fact_audited``
(``plugins/memory/holographic/store.py``, Entry 10 in
``docs/personal-system/IMPLEMENTATION_LOG.md``).

This is the governance *write* path — companion to the read-only
``scripts/memory_audit.py``. It never invents a mutation shape of its own:
every write ultimately goes through ``update_fact_audited``/
``forget_fact_audited``, so the mandatory ``reason``, the audited before/
after row in ``fact_governance_audit``, and the atomic mutation+audit
transaction are exactly what the store already guarantees.

Safety model
------------

- **Dry-run is the default.** Unless ``--apply`` is passed, nothing is ever
  written to ``--db`` and no backup is taken. The dry-run path never
  imports or constructs ``MemoryStore`` (whose constructor creates a
  missing database file and schema on open — a write) and never resolves
  ``HERMES_HOME``. It reads the current ``facts`` row for ``--fact-id``
  with a plain read-only ``sqlite3`` connection (``mode=ro&immutable=1``,
  same flags ``memory_audit.py`` uses) and prints a JSON preview of what
  ``--apply`` would do.
- **``--apply`` requires ``--backup-dir``.** A byte-for-byte, timestamped
  copy of ``--db`` is made before any write. If the backup fails, nothing
  is written — fail closed.
- **Real Hermes paths are refused by default.** Any ``--db`` under
  ``~/.hermes`` or ``~/.hermes-enhanced``, or literally named
  ``agent_memory.db``, ``memory_store.db``, ``state.db``, or
  ``bujo.sqlite``, is rejected unless ``--allow-real-paths`` (the same
  ``is_guarded_path`` guard ``memory_migrate.py``/``memory_audit.py``
  use). ``--apply`` against such a path additionally requires
  ``--confirm-real-governance``.
- **``--db`` must already exist.** Neither dry-run nor ``--apply`` ever
  creates a database: if ``--db`` does not exist, this script errors out
  before taking a backup or opening ``MemoryStore`` at all — there is no
  fact to govern in a database that was never populated.
- **Only ``facts``/``fact_governance_audit`` (plus the ancillary
  ``entities``/``fact_entities``/``memory_banks`` tables
  ``update_fact_audited`` already touches) are ever read or written.**
  This script issues no SQL of its own beyond the read-only ``facts`` peek
  used for the dry-run preview.

Usage
-----

.. code-block:: text

    # Preview only (default) — prints a JSON diff, writes nothing.
    python3 scripts/memory_governance.py --db memory_store.db \\
        --action update --fact-id 42 --reason "fix typo" --content "Corrected fact"

    # Apply — requires an explicit backup directory.
    python3 scripts/memory_governance.py --db memory_store.db \\
        --action forget --fact-id 42 --reason "no longer true" --confirm-forget \\
        --apply --backup-dir ./governance-backups
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Allow importing scripts.memory_migrate (for is_guarded_path) and
# plugins.memory.holographic (for MemoryStore) when run as a plain script.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from scripts.memory_migrate import is_guarded_path  # noqa: E402

_TRUST_MIN, _TRUST_MAX = 0.0, 1.0
_FACTS_COLUMNS = ("content", "category", "trust_score")


class GovernanceError(RuntimeError):
    """Fatal error resolving --db or --fact-id: bad path, missing table/row."""


def read_current_fact(db_path: Path, fact_id: int) -> dict:
    """Read-only peek at one ``facts`` row. Never writes, never creates ``db_path``.

    Opens `db_path` via the SQLite `mode=ro&immutable=1` URI flags (same as
    `memory_audit.py`) so no `-wal`/`-shm` sidecar files are opened or
    created against a WAL-mode `MemoryStore` database, and any accidental
    write attempt raises `sqlite3.OperationalError` rather than succeeding.
    """
    if not db_path.exists():
        raise GovernanceError(f"database not found: {db_path}")

    try:
        conn = sqlite3.connect(
            f"file:{db_path.as_posix()}?mode=ro&immutable=1", uri=True
        )
    except sqlite3.OperationalError as exc:
        raise GovernanceError(f"cannot open database: {db_path} ({exc})") from exc

    conn.row_factory = sqlite3.Row
    try:
        try:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        except sqlite3.DatabaseError as exc:
            raise GovernanceError(
                f"not a valid SQLite database: {db_path} ({exc})"
            ) from exc

        if "facts" not in tables:
            raise GovernanceError(
                f"database has no 'facts' table: {db_path}"
            )

        row = conn.execute(
            f"SELECT {', '.join(_FACTS_COLUMNS)} FROM facts WHERE fact_id = ?",
            (fact_id,),
        ).fetchone()
        if row is None:
            raise GovernanceError(f"fact_id {fact_id} not found")

        return dict(row)
    finally:
        conn.close()


def build_update_preview(
    current: dict,
    *,
    content: "Optional[str]",
    category: "Optional[str]",
    trust_score: "Optional[float]",
) -> dict:
    """Pure computation of what `update_fact_audited` would change. No I/O."""
    changed: dict = {}
    if content is not None and content != current["content"]:
        changed["content"] = {"old": current["content"], "new": content}
    if category is not None and category != current["category"]:
        changed["category"] = {"old": current["category"], "new": category}
    if trust_score is not None and trust_score != current["trust_score"]:
        changed["trust_score"] = {"old": current["trust_score"], "new": trust_score}
    return {"changed_fields": sorted(changed), "changed": changed, "noop": not changed}


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def create_backup(db_path: Path, backup_dir: Path) -> dict:
    """Byte-for-byte, timestamped copy of `db_path` into `backup_dir`.

    Raises OSError (unchanged) on any failure — callers must treat that as
    "do not proceed to write --db".
    """
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{db_path.name}.{_timestamp()}.bak"
    shutil.copyfile(db_path, backup_path)
    return {"db_backup": str(backup_path)}


def _parse_args(argv: "list[str] | None") -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Safe CLI over MemoryStore.update_fact_audited/forget_fact_audited. "
            "Dry-run (JSON preview, no writes) is the default. Never infers "
            "HERMES_HOME."
        )
    )
    parser.add_argument(
        "--db", required=True,
        help="Path to the MemoryStore-compatible SQLite database to govern.",
    )
    parser.add_argument(
        "--action", required=True, choices=("update", "forget"),
        help="update: change content/category/trust_score. forget: delete the fact.",
    )
    parser.add_argument(
        "--fact-id", required=True, type=int,
        help="fact_id to update or forget.",
    )
    parser.add_argument(
        "--reason", required=True,
        help="Mandatory human-readable reason, recorded in fact_governance_audit.",
    )
    parser.add_argument("--content", default=None, help="[update] New content.")
    parser.add_argument("--category", default=None, help="[update] New category.")
    parser.add_argument(
        "--trust-score", dest="trust_score", type=float, default=None,
        help="[update] New trust_score, must be within [0, 1].",
    )
    parser.add_argument(
        "--confirm-forget", action="store_true",
        help="[forget] Required to confirm a forget action (preview or apply).",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually write to --db. Requires --backup-dir. Default is dry-run.",
    )
    parser.add_argument(
        "--backup-dir", default=None,
        help="Directory for the pre-write backup. Required with --apply.",
    )
    parser.add_argument(
        "--allow-real-paths", action="store_true",
        help="Permit --db under ~/.hermes, ~/.hermes-enhanced, or named "
             "agent_memory.db/memory_store.db/state.db/bujo.sqlite.",
    )
    parser.add_argument(
        "--confirm-real-governance", action="store_true",
        help="Required together with --apply and --allow-real-paths to write "
             "to a guarded real path.",
    )
    return parser.parse_args(argv)


def main(argv: "list[str] | None" = None) -> int:
    args = _parse_args(argv)

    if args.fact_id <= 0:
        print("Error: --fact-id must be a positive integer.", file=sys.stderr)
        return 2

    reason = (args.reason or "").strip()
    if not reason:
        print("Error: --reason must not be blank.", file=sys.stderr)
        return 2

    if args.action == "update":
        if args.content is None and args.category is None and args.trust_score is None:
            print(
                "Error: --action update requires at least one of "
                "--content/--category/--trust-score.",
                file=sys.stderr,
            )
            return 2
        content = args.content
        if content is not None:
            content = content.strip()
            if not content:
                print("Error: --content must not be empty.", file=sys.stderr)
                return 2
        if args.trust_score is not None and not (_TRUST_MIN <= args.trust_score <= _TRUST_MAX):
            print(
                f"Error: --trust-score must be between {_TRUST_MIN} and {_TRUST_MAX}, "
                f"got {args.trust_score!r}.",
                file=sys.stderr,
            )
            return 2
    else:  # forget
        content = None
        if not args.confirm_forget:
            print(
                "Error: --action forget requires --confirm-forget.",
                file=sys.stderr,
            )
            return 2

    db_path = Path(args.db)

    if not args.allow_real_paths and is_guarded_path(db_path):
        print(
            f"Error: --db {db_path} looks like a real Hermes database path; "
            "refusing without --allow-real-paths.",
            file=sys.stderr,
        )
        return 2

    apply_mode = args.apply
    if apply_mode:
        if not args.backup_dir:
            print("Error: --apply requires --backup-dir.", file=sys.stderr)
            return 2
        if args.allow_real_paths and not args.confirm_real_governance:
            print(
                "Error: --apply with --allow-real-paths also requires "
                "--confirm-real-governance.",
                file=sys.stderr,
            )
            return 2

    try:
        current = read_current_fact(db_path, args.fact_id)
    except GovernanceError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not apply_mode:
        output = {
            "db": str(db_path),
            "action": args.action,
            "fact_id": args.fact_id,
            "reason": reason,
            "dry_run": True,
            "applied": False,
            "current": current,
        }
        if args.action == "update":
            output.update(
                build_update_preview(
                    current,
                    content=content,
                    category=args.category,
                    trust_score=args.trust_score,
                )
            )
        else:
            output["would_remove"] = True
        print(json.dumps(output, sort_keys=True, indent=2, ensure_ascii=False))
        return 0

    backup_dir = Path(args.backup_dir)
    try:
        backup = create_backup(db_path, backup_dir)
    except OSError as exc:
        print(
            f"Error: backup failed, aborting apply (nothing written to --db): {exc}",
            file=sys.stderr,
        )
        return 1

    from plugins.memory.holographic.store import MemoryStore

    store = MemoryStore(db_path=db_path)
    try:
        if args.action == "update":
            result = store.update_fact_audited(
                args.fact_id,
                reason=reason,
                content=content,
                category=args.category,
                trust_score=args.trust_score,
            )
        else:
            result = store.forget_fact_audited(args.fact_id, reason=reason)
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller, not swallowed
        print(f"Error: governance mutation failed: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()

    output = {
        "db": str(db_path),
        "action": args.action,
        "fact_id": args.fact_id,
        "reason": reason,
        "dry_run": False,
        "applied": True,
        "backup": backup,
        "result": result,
    }
    print(json.dumps(output, sort_keys=True, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
