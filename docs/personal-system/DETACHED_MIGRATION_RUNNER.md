# Detached migration runner

`scripts/memory_migrate_detached.py` is a safety wrapper around
`scripts/memory_migrate.py` for an **offline, operator-controlled** migration.

## Safety contract

- Plan-only is the default; without `--execute` it does not call subprocesses.
- It uses the repository virtualenv interpreter explicitly:
  `/home/ubuntu/hermes-fork/.venv/bin/python`.
- It launches only a new transient `systemd-run --user` unit with `--collect`,
  `--pipe` and `--wait`; it never uses `sudo`.
- It refuses guarded Hermes paths unless the operator explicitly supplies both
  `--allow-real-paths` and, for `--apply`, `--confirm-real-migration`.
- Apply mode takes a runner-level snapshot, runs a post-apply dry-run, and
  restores the snapshot if the detached unit or verification fails.
- It does **not** stop, start, reload, or restart the gateway, and it does not
  swap a database into the live gateway path. Those are separate, manual
  administrative operations requiring a maintenance window.

## Plan a temporary/offline run

```bash
/home/ubuntu/hermes-fork/.venv/bin/python \
  scripts/memory_migrate_detached.py \
  --source /tmp/source.db \
  --target /tmp/target.db \
  --backup-dir /tmp/memory-migration-backup
```

The command prints JSON describing the transient unit and performs no write.

## Execute a non-applying preview

Add `--execute`. The transient unit runs `memory_migrate.py` in its default
read-only dry-run mode and writes the result JSON/log under `--backup-dir`.

## Apply to an explicitly supplied offline target

```bash
/home/ubuntu/hermes-fork/.venv/bin/python \
  scripts/memory_migrate_detached.py \
  --source /tmp/source.db \
  --target /tmp/target.db \
  --backup-dir /tmp/memory-migration-backup \
  --execute --apply
```

Do not point this example at active Hermes databases. A live migration still
requires a separately reviewed maintenance procedure, fresh backups, an
administrative stop/start performed outside this runner, and post-start health
checks. This tool alone does not constitute or claim a production migration.
