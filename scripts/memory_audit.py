#!/usr/bin/env python3
"""Read-only CLI: query ``fact_governance_audit`` rows written by
``MemoryStore.update_fact_audited``/``forget_fact_audited``
(``plugins/memory/holographic/store.py``).

This script never infers ``HERMES_HOME``: ``--db PATH`` is required and is
the only database this process ever opens. The connection is opened with
SQLite's ``mode=ro&immutable=1`` URI flags, so SQLite itself rejects any
write at the connection level regardless of what the code above it does —
belt and suspenders on top of the fact that this module issues nothing but
``SELECT`` statements. There is no code path in this script, with or
without ``--allow-real-paths``, that can write to ``--db``.

``immutable=1`` (not just ``mode=ro``) matters because ``MemoryStore``
(``plugins/memory/holographic/store.py``) enables WAL journal mode.
Plain ``mode=ro`` against a WAL database still makes SQLite open (and, on a
fresh database, create) the ``-wal``/``-shm`` sidecar files to check for
frames not yet checkpointed into the main file. ``immutable=1`` tells
SQLite the database file will not change for the life of the connection, so
it skips that WAL/locking machinery entirely and reads only the main
database file — no sidecar files are opened or created. The tradeoff: if
the target database has a pending, un-checkpointed ``-wal`` file, this CLI
will not see those not-yet-checkpointed rows. Point ``--db`` at a stable
snapshot/backup, or run ``PRAGMA wal_checkpoint(TRUNCATE);`` against the
live database first (e.g. via ``sqlite3 memory_store.db 'PRAGMA
wal_checkpoint(TRUNCATE);'``) if you need the very latest audit rows.

Real Hermes paths are refused by default, reusing the same guard
``scripts/memory_migrate.py`` uses: anything under ``~/.hermes`` or
``~/.hermes-enhanced``, or literally named ``agent_memory.db``,
``memory_store.db``, ``state.db``, or ``bujo.sqlite``, is rejected unless
``--allow-real-paths`` is passed. That flag only lifts the path check — it
never changes the connection from read-only.

Output is a single deterministic JSON object on stdout (``sort_keys=True``,
rows ordered by ``audit_id DESC`` — most recent mutation first — then
truncated to ``--limit``). All errors (missing ``--db`` file, missing
``fact_governance_audit`` table, invalid ``--fact-id``/``--limit``, guarded
path without ``--allow-real-paths``) go to stderr with a non-zero exit
code and nothing on stdout.

``fact_governance_audit`` itself never stores secrets or conversation
transcripts (see the table's definition in ``store.py``): only a fact's own
before/after content, category, and trust score plus the caller-supplied
reason and optional session_id. This CLI reads those columns verbatim and
adds nothing else.

Usage:
    python3 scripts/memory_audit.py --db memory_store.db
    python3 scripts/memory_audit.py --db memory_store.db --fact-id 42
    python3 scripts/memory_audit.py --db memory_store.db --limit 10
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Optional

# Allow importing scripts.memory_migrate (for is_guarded_path) when run as a
# plain script rather than via `python -m`.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from scripts.memory_migrate import is_guarded_path  # noqa: E402

_AUDIT_TABLE = "fact_governance_audit"

# Explicit column list (rather than SELECT *) so output shape is stable even
# if the table gains columns later, and so nothing beyond these documented
# fields is ever echoed back.
_COLUMNS = (
    "audit_id",
    "fact_id",
    "action",
    "old_content",
    "new_content",
    "old_category",
    "new_category",
    "old_trust",
    "new_trust",
    "reason",
    "session_id",
    "created_at",
)

_DEFAULT_LIMIT = 50


class AuditQueryError(RuntimeError):
    """Fatal query error: bad path, missing table, or invalid database."""


def query_audit(
    db_path: Path,
    *,
    fact_id: "Optional[int]" = None,
    limit: int = _DEFAULT_LIMIT,
) -> "list[dict]":
    """Read-only query against ``fact_governance_audit``. Never writes.

    Opens `db_path` via the SQLite `mode=ro&immutable=1` URI flags, so any
    accidental write attempt raises `sqlite3.OperationalError` rather than
    succeeding, and no `-wal`/`-shm` sidecar files are opened or created
    even against a WAL-mode database. See the module docstring for why a
    pending, un-checkpointed WAL means this call may not see the latest
    rows — point `db_path` at a stable snapshot/backup, or checkpoint the
    live database first, if that matters for the query.
    """
    if not db_path.exists():
        raise AuditQueryError(f"database not found: {db_path}")

    try:
        conn = sqlite3.connect(
            f"file:{db_path.as_posix()}?mode=ro&immutable=1", uri=True
        )
    except sqlite3.OperationalError as exc:
        raise AuditQueryError(f"cannot open database: {db_path} ({exc})") from exc

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
            raise AuditQueryError(
                f"not a valid SQLite database: {db_path} ({exc})"
            ) from exc

        if _AUDIT_TABLE not in tables:
            raise AuditQueryError(
                f"database has no '{_AUDIT_TABLE}' table — nothing to audit"
            )

        sql = f"SELECT {', '.join(_COLUMNS)} FROM {_AUDIT_TABLE}"
        params: list = []
        if fact_id is not None:
            sql += " WHERE fact_id = ?"
            params.append(fact_id)
        sql += " ORDER BY audit_id DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _parse_args(argv: "list[str] | None") -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only query of the fact_governance_audit table written by "
            "MemoryStore.update_fact_audited/forget_fact_audited. Never "
            "writes, never infers HERMES_HOME."
        )
    )
    parser.add_argument(
        "--db", required=True,
        help="Path to the MemoryStore-compatible SQLite database to query.",
    )
    parser.add_argument(
        "--fact-id", type=int, default=None,
        help="Restrict results to this fact_id (default: all facts).",
    )
    parser.add_argument(
        "--limit", type=int, default=_DEFAULT_LIMIT,
        help=f"Maximum number of rows to return (default: {_DEFAULT_LIMIT}).",
    )
    parser.add_argument(
        "--allow-real-paths", action="store_true",
        help="Permit --db under ~/.hermes, ~/.hermes-enhanced, or named "
             "agent_memory.db/memory_store.db/state.db/bujo.sqlite. The "
             "connection is always read-only regardless of this flag.",
    )
    return parser.parse_args(argv)


def main(argv: "list[str] | None" = None) -> int:
    args = _parse_args(argv)

    if args.limit <= 0:
        print("Error: --limit must be a positive integer.", file=sys.stderr)
        return 2
    if args.fact_id is not None and args.fact_id <= 0:
        print("Error: --fact-id must be a positive integer.", file=sys.stderr)
        return 2

    db_path = Path(args.db)

    if not args.allow_real_paths and is_guarded_path(db_path):
        print(
            f"Error: --db {db_path} looks like a real Hermes database path; "
            "refusing without --allow-real-paths.",
            file=sys.stderr,
        )
        return 2

    try:
        rows = query_audit(db_path, fact_id=args.fact_id, limit=args.limit)
    except AuditQueryError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    output = {
        "db": str(db_path),
        "fact_id": args.fact_id,
        "limit": args.limit,
        "count": len(rows),
        "rows": rows,
    }
    print(json.dumps(output, sort_keys=True, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
