#!/usr/bin/env python3
"""Operational tool: run ``scripts/memory_migrate.py`` inside a detached,
transient ``systemd-run --user`` unit, isolated from the live gateway.

This does **not** replace ``memory_migrate.py``'s own safety model (dry-run
default, ``--backup-dir``-gated ``--apply``, guarded real-path refusal) — it
wraps it with an extra, independent layer needed specifically because the
migration runs *detached* (in its own cgroup/unit, outside this process's
direct child tree):

- **Plan-only by default.** Without ``--execute``, this tool only computes
  and prints the ``systemd-run`` command and snapshot plan as JSON. It never
  calls ``subprocess`` and never touches the filesystem beyond reading
  ``--source``/``--target`` existence for the printed plan.
- **Pinned interpreter.** The migration always runs under this checkout's
  ``.venv/bin/python`` — never ``sys.executable``, never a bare ``python``/
  ``python3`` resolved from ``PATH`` inside the transient unit's environment.
  There is no flag to override this.
- **Runner-level snapshot before every ``--execute --apply`` run**, in
  addition to (not instead of) ``memory_migrate.py``'s own internal backup —
  belt-and-suspenders because a detached unit can be killed by systemd
  (OOM, timeout) in ways an in-process subprocess cannot.
- **Post-verification with automatic rollback.** After a ``--execute
  --apply`` run exits 0, this tool runs an independent, non-detached dry-run
  of ``memory_migrate.py`` against the same ``--source``/``--target`` and
  requires ``insertable == 0`` (nothing left un-migrated). If the unit
  exits non-zero, or verification fails, the runner-level snapshot is
  restored over ``--source``/``--target`` and the result is marked
  ``rolled_back``.
- **Never stops, starts, restarts, or reloads any existing systemd unit.**
  ``systemd-run --user`` only creates a brand-new transient unit; this tool
  refuses to reuse any unit name that looks like a known Hermes gateway
  service. Swapping a migrated database into the live gateway's path and
  restarting/reloading that gateway is a deliberate, manual, operator-only
  step this tool never performs — see
  ``docs/personal-system/DETACHED_MIGRATION_RUNNER.md``.
- **Never uses ``sudo``.** ``--user`` scope only.

Usage
-----

.. code-block:: text

    # Plan only (default) — prints the command that would run, writes nothing.
    python3 scripts/memory_migrate_detached.py \\
        --source agent_memory.db --target memory_store.db --backup-dir ./backups

    # Actually launch the transient unit, in memory_migrate.py's own dry-run mode.
    python3 scripts/memory_migrate_detached.py \\
        --source agent_memory.db --target memory_store.db --backup-dir ./backups \\
        --execute

    # Actually launch the transient unit AND apply the migration for real,
    # with runner-level snapshot + post-verification + auto-rollback.
    python3 scripts/memory_migrate_detached.py \\
        --source agent_memory.db --target memory_store.db --backup-dir ./backups \\
        --execute --apply
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.memory_migrate import create_backups, is_guarded_path  # noqa: E402

VENV_PYTHON = _REPO_ROOT / ".venv" / "bin" / "python"
MIGRATE_SCRIPT = _REPO_ROOT / "scripts" / "memory_migrate.py"

# Never let a caller point this tool's own transient unit at a name that
# collides with a real Hermes systemd unit, so a typo can't be mistaken for
# "this tool restarts the gateway" (it never does).
_GATEWAY_UNIT_NAMES = frozenset(
    {
        "hermes-enhanced-gateway.service",
        "hermes-gateway-enhanced.service",
        "hermes-enhanced-bridge.service",
    }
)

_DEFAULT_TIMEOUT_SECONDS = 600.0


class DetachedRunnerError(RuntimeError):
    """Fatal error in planning/executing the detached migration run."""


@dataclass
class RunResult:
    ok: bool
    applied: bool
    executed: bool
    returncode: "Optional[int]" = None
    stdout: "Optional[str]" = None
    stderr: "Optional[str]" = None
    verification: "Optional[dict]" = None
    rolled_back: bool = False
    backups: dict = field(default_factory=dict)
    errors: "list[str]" = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "applied": self.applied,
            "executed": self.executed,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "verification": self.verification,
            "rolled_back": self.rolled_back,
            "backups": self.backups,
            "errors": self.errors,
        }


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def default_unit_name() -> str:
    return f"hermes-memmigrate-{_timestamp()}"


def _normalized_unit_name(name: str) -> str:
    return name if name.endswith(".service") else f"{name}.service"


def check_unit_name(name: str) -> None:
    """Raise DetachedRunnerError if `name` collides with a known gateway unit."""
    if _normalized_unit_name(name) in _GATEWAY_UNIT_NAMES:
        raise DetachedRunnerError(
            f"refusing to use unit name {name!r}: it matches a real Hermes "
            "gateway service. This tool only ever creates a new, unrelated "
            "transient unit."
        )


def build_systemd_run_command(
    *,
    unit_name: str,
    source: Path,
    target: Path,
    backup_dir: Path,
    apply: bool,
    allow_real_paths: bool,
    confirm_real_migration: bool,
) -> "list[str]":
    """Build the argv for the detached migration run. Pure — no subprocess call.

    Always ``--user`` (no sudo), always the pinned ``.venv/bin/python``,
    always ``--collect`` (the transient unit is garbage-collected once it
    exits, nothing lingers), always ``--wait`` so this process blocks and
    receives the child's exit code, and always ``--pipe`` so stdout/stderr
    are connected back to this process instead of only going to the journal.
    """
    check_unit_name(unit_name)
    if not VENV_PYTHON.exists():
        raise DetachedRunnerError(
            f"expected interpreter not found: {VENV_PYTHON} — refusing to "
            "fall back to any other python."
        )

    cmd = [
        "systemd-run",
        "--user",
        "--collect",
        "--pipe",
        "--wait",
        f"--unit={unit_name}",
        f"--working-directory={_REPO_ROOT}",
        "--",
        str(VENV_PYTHON),
        str(MIGRATE_SCRIPT),
        "--source",
        str(source),
        "--target",
        str(target),
        "--backup-dir",
        str(backup_dir),
    ]
    if apply:
        cmd.append("--apply")
    if allow_real_paths:
        cmd.append("--allow-real-paths")
    if confirm_real_migration:
        cmd.append("--confirm-real-migration")
    return cmd


def run_detached(cmd: "list[str]", *, timeout: float) -> "subprocess.CompletedProcess[str]":
    """Thin wrapper around subprocess.run — the sole real-execution seam.

    Kept as a one-line, separately-monkeypatchable function so tests can
    replace it entirely and never invoke a real systemd/dbus call.
    """
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _run_plain_dry_run(source: Path, target: Path, *, timeout: float) -> dict:
    """Non-detached, read-only verification pass: memory_migrate.py preview mode.

    Runs directly (no systemd-run) since this is a read-only preview, not a
    risky write — isolation is not needed for it. Still uses the pinned
    venv python exclusively.
    """
    cmd = [str(VENV_PYTHON), str(MIGRATE_SCRIPT), "--source", str(source), "--target", str(target)]
    completed = run_detached(cmd, timeout=timeout)
    if completed.returncode != 0:
        raise DetachedRunnerError(
            f"post-verification dry-run failed (exit {completed.returncode}): {completed.stderr}"
        )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise DetachedRunnerError(f"post-verification dry-run produced non-JSON output: {exc}") from exc


def rollback_from_snapshot(backups: dict, source: Path, target: Path) -> None:
    """Restore --source/--target from the byte-for-byte backups taken pre-run."""
    import shutil

    if "source_backup" in backups:
        shutil.copyfile(backups["source_backup"], source)
    if "target_backup" in backups:
        shutil.copyfile(backups["target_backup"], target)
    elif target.exists():
        # No pre-existing target was backed up because it didn't exist yet;
        # a run that got far enough to create one must be undone by removing it.
        target.unlink()


def execute_run(
    *,
    unit_name: str,
    source: Path,
    target: Path,
    backup_dir: Path,
    apply: bool,
    allow_real_paths: bool,
    confirm_real_migration: bool,
    timeout: float,
) -> RunResult:
    """Actually launch the transient unit (--execute path) and, if --apply,
    snapshot first and verify+rollback after.
    """
    cmd = build_systemd_run_command(
        unit_name=unit_name,
        source=source,
        target=target,
        backup_dir=backup_dir,
        apply=apply,
        allow_real_paths=allow_real_paths,
        confirm_real_migration=confirm_real_migration,
    )

    backups: dict = {}
    if apply:
        try:
            backups = create_backups(source, target, backup_dir)
        except OSError as exc:
            raise DetachedRunnerError(f"pre-run snapshot failed, refusing to launch: {exc}") from exc

    try:
        completed = run_detached(cmd, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        result = RunResult(
            ok=False, applied=apply, executed=True, returncode=None,
            stdout=None, stderr=str(exc), backups=backups,
            errors=["systemd-run timed out"],
        )
        if apply:
            rollback_from_snapshot(backups, source, target)
            result.rolled_back = True
        return result

    result = RunResult(
        ok=completed.returncode == 0,
        applied=apply,
        executed=True,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        backups=backups,
    )

    if not apply:
        return result

    if completed.returncode != 0:
        result.errors.append(f"detached unit exited {completed.returncode}")
        rollback_from_snapshot(backups, source, target)
        result.rolled_back = True
        return result

    try:
        verification = _run_plain_dry_run(source, target, timeout=timeout)
    except DetachedRunnerError as exc:
        result.ok = False
        result.errors.append(str(exc))
        rollback_from_snapshot(backups, source, target)
        result.rolled_back = True
        return result

    result.verification = verification
    if verification.get("insertable", -1) != 0:
        result.ok = False
        result.errors.append(
            "post-verification found un-migrated rows still insertable "
            f"({verification.get('insertable')}); rolling back"
        )
        rollback_from_snapshot(backups, source, target)
        result.rolled_back = True

    return result


def _write_json_and_log(result: dict, backup_dir: Path, *, prefix: str) -> dict:
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = _timestamp()
    json_path = backup_dir / f"{prefix}-{ts}.json"
    log_path = backup_dir / f"{prefix}-{ts}.log"
    json_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    log_lines = [f"{prefix} result at {ts}", json.dumps(result, indent=2, sort_keys=True)]
    log_path.write_text("\n".join(log_lines) + "\n")
    return {"result_json": str(json_path), "result_log": str(log_path)}


def _parse_args(argv: "list[str] | None") -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run scripts/memory_migrate.py inside a detached, transient "
            "systemd-run --user unit, isolated from the live gateway. "
            "Plan-only (no subprocess call) is the default; pass --execute "
            "to actually launch the unit."
        )
    )
    parser.add_argument("--source", required=True, help="Path to the source SQLite database.")
    parser.add_argument("--target", required=True, help="Path to the target SQLite database.")
    parser.add_argument(
        "--backup-dir", required=True,
        help="Directory for the runner-level pre-run snapshot and the JSON/log result. Required.",
    )
    parser.add_argument("--unit-name", default=None, help="Transient unit name (default: auto-generated, timestamped).")
    parser.add_argument("--apply", action="store_true", help="Passed through to memory_migrate.py; requires --execute.")
    parser.add_argument("--allow-real-paths", action="store_true", help="Passed through to memory_migrate.py.")
    parser.add_argument("--confirm-real-migration", action="store_true", help="Passed through to memory_migrate.py.")
    parser.add_argument(
        "--execute", action="store_true",
        help="Actually invoke systemd-run. Without this flag, only the plan is printed.",
    )
    parser.add_argument("--timeout", type=float, default=_DEFAULT_TIMEOUT_SECONDS, help="Seconds to wait for the detached unit.")
    return parser.parse_args(argv)


def main(argv: "list[str] | None" = None) -> int:
    args = _parse_args(argv)

    source = Path(args.source)
    target = Path(args.target)
    backup_dir = Path(args.backup_dir)

    if not args.allow_real_paths:
        for label, path in (("--source", source), ("--target", target), ("--backup-dir", backup_dir)):
            if is_guarded_path(path):
                print(
                    f"Error: {label} {path} looks like a real Hermes path; "
                    "refusing without --allow-real-paths.",
                    file=sys.stderr,
                )
                return 2

    if args.apply and not args.execute:
        print("Error: --apply requires --execute (there is nothing to apply without launching the unit).", file=sys.stderr)
        return 2

    if args.apply and args.allow_real_paths and not args.confirm_real_migration:
        print(
            "Error: --apply with --allow-real-paths also requires --confirm-real-migration.",
            file=sys.stderr,
        )
        return 2

    unit_name = args.unit_name or default_unit_name()
    try:
        check_unit_name(unit_name)
    except DetachedRunnerError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if not args.execute:
        try:
            cmd = build_systemd_run_command(
                unit_name=unit_name,
                source=source,
                target=target,
                backup_dir=backup_dir,
                apply=args.apply,
                allow_real_paths=args.allow_real_paths,
                confirm_real_migration=args.confirm_real_migration,
            )
        except DetachedRunnerError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        plan = {
            "planned": True,
            "executed": False,
            "unit_name": unit_name,
            "command": cmd,
            "note": "No subprocess was called. Pass --execute to actually launch this unit.",
        }
        print(json.dumps(plan, indent=2, sort_keys=True))
        return 0

    try:
        result = execute_run(
            unit_name=unit_name,
            source=source,
            target=target,
            backup_dir=backup_dir,
            apply=args.apply,
            allow_real_paths=args.allow_real_paths,
            confirm_real_migration=args.confirm_real_migration,
            timeout=args.timeout,
        )
    except DetachedRunnerError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    output = result.to_dict()
    output["unit_name"] = unit_name
    try:
        output["files"] = _write_json_and_log(output, backup_dir, prefix="detached-run")
    except OSError as exc:
        output.setdefault("errors", []).append(f"could not write result files: {exc}")

    print(json.dumps(output, indent=2, sort_keys=True))
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
