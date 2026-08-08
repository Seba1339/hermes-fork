"""Behavior tests for APIServerAdapter.send()'s BuJo delivery path.

This is the "gateway/BuJo delivery" mechanism referenced in
docs/personal-system/ARCHITECTURE.md: cron output is intentionally NOT
written to the personal BuJo journal unless a caller opts in with
metadata={"bujo_write": True}. This separation (BuJo = explicit human
intent, not telemetry) is load-bearing — these tests pin it so a future
change can't silently start mirroring every cron job into the journal.

All tests use a temp HERMES_HOME (via the repo-wide _isolate_hermes_home
autouse fixture) and a hand-built bujo.sqlite, never the real journal.
"""
import sqlite3

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
from hermes_cli.config import get_hermes_home


def _make_bujo_db() -> None:
    data_dir = get_hermes_home() / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(data_dir / "bujo.sqlite"))
    try:
        conn.execute(
            "CREATE TABLE bujo_entries (id INTEGER PRIMARY KEY, date TEXT, section TEXT, "
            "item_type TEXT, content TEXT, depth INTEGER, sort_order INTEGER)"
        )
        conn.commit()
    finally:
        conn.close()


def _read_entries(date: str, section: str) -> list[str]:
    data_dir = get_hermes_home() / "data"
    conn = sqlite3.connect(str(data_dir / "bujo.sqlite"))
    try:
        rows = conn.execute(
            "SELECT content FROM bujo_entries WHERE date=? AND section=? ORDER BY sort_order",
            (date, section),
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


@pytest.fixture
def adapter():
    return APIServerAdapter(PlatformConfig(enabled=True))


class TestBujoDeliveryOptIn:
    @pytest.mark.asyncio
    async def test_cron_output_without_bujo_write_flag_is_not_persisted(self, adapter):
        _make_bujo_db()
        result = await adapter.send("ignored-chat-id", "some cron output", metadata={})
        assert result.success is True
        assert _read_entries("2026-01-01", "reportes") == []

    @pytest.mark.asyncio
    async def test_no_metadata_at_all_is_also_a_no_op(self, adapter):
        _make_bujo_db()
        result = await adapter.send("ignored-chat-id", "some cron output")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_explicit_bujo_write_true_persists_one_entry(self, adapter):
        _make_bujo_db()
        result = await adapter.send(
            "ignored-chat-id",
            "Backup completed successfully",
            metadata={"bujo_write": True, "job_name": "backup", "date": "2026-08-08"},
        )
        assert result.success is True
        entries = _read_entries("2026-08-08", "reportes")
        assert len(entries) == 1
        assert "[backup]" in entries[0]
        assert "Backup completed successfully" in entries[0]

    @pytest.mark.asyncio
    async def test_missing_bujo_db_returns_failure_without_raising(self, adapter):
        # No _make_bujo_db() call — HERMES_HOME/data/bujo.sqlite does not exist.
        result = await adapter.send(
            "ignored-chat-id", "content", metadata={"bujo_write": True, "job_name": "x"},
        )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_all_noise_output_is_skipped_even_when_opted_in(self, adapter):
        _make_bujo_db()
        noisy = "[Cron]\nCronjob Response:\n(job_id: abc123)\n---\n"
        result = await adapter.send(
            "ignored-chat-id", noisy,
            metadata={"bujo_write": True, "job_name": "noisy-job", "date": "2026-08-08"},
        )
        assert result.success is True
        assert _read_entries("2026-08-08", "reportes") == []

    @pytest.mark.asyncio
    async def test_section_override_is_respected(self, adapter):
        _make_bujo_db()
        await adapter.send(
            "ignored-chat-id", "Task done",
            metadata={"bujo_write": True, "job_name": "j", "date": "2026-08-08", "section": "agenda"},
        )
        assert _read_entries("2026-08-08", "agenda") == ["[j] Task done"]
        assert _read_entries("2026-08-08", "reportes") == []

    @pytest.mark.asyncio
    async def test_repeated_calls_append_rather_than_overwrite(self, adapter):
        # Two opted-in deliveries on the same date/section must not
        # duplicate-detect or clobber each other's sort_order.
        _make_bujo_db()
        await adapter.send(
            "chat", "first", metadata={"bujo_write": True, "job_name": "a", "date": "2026-08-08"},
        )
        await adapter.send(
            "chat", "second", metadata={"bujo_write": True, "job_name": "b", "date": "2026-08-08"},
        )
        entries = _read_entries("2026-08-08", "reportes")
        assert len(entries) == 2
        assert entries[0] == "[a] first"
        assert entries[1] == "[b] second"
