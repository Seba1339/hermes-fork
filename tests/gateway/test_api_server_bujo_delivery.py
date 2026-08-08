"""Behavior tests for APIServerAdapter.send()'s BuJo delivery path.

This is the "gateway/BuJo delivery" mechanism referenced in
docs/personal-system/ARCHITECTURE.md: cron output is intentionally NOT
written to the personal BuJo journal unless a caller opts in with
metadata={"bujo_write": True}. This separation (BuJo = explicit human
intent, not telemetry) is load-bearing — these tests pin it so a future
change can't silently start mirroring every cron job into the journal.

All tests use a temp HERMES_HOME (via the repo-wide _isolate_hermes_home
autouse fixture) and a hand-built bujo.sqlite, never the real journal.

``TestCleanCronOutputUnit`` below tests ``_clean_cron_output`` directly —
the pure function extracted from ``APIServerAdapter.send()`` (Phase 2 of
docs/personal-system/ROADMAP.md) — rather than only through the E2E
``adapter.send()`` path in ``TestBujoDeliveryOptIn``. Both classes are kept:
the unit tests pin exact edge-case behavior (unicode truncation, all-noise
detection) cheaply; the E2E tests pin the DB-write/opt-in contract around it.
"""
import sqlite3

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter, _clean_cron_output
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


class TestCleanCronOutputUnit:
    """Direct tests of ``_clean_cron_output``, no DB/adapter involved."""

    def test_empty_content_returns_none(self):
        assert _clean_cron_output("", "job") is None

    def test_whitespace_only_returns_none(self):
        assert _clean_cron_output("   \n\n\t\n   ", "job") is None

    def test_all_noise_patterns_returns_none(self):
        noisy = "\n".join(
            [
                "[Cron]",
                "Cronjob Response:",
                "(job_id: abc123)",
                "─────────",
                "-----",
                "=====",
                "⚠️ something failed:",
                "To stop or manage this job, run ...",
                "Script not found",
                "   ",
            ]
        )
        assert _clean_cron_output(noisy, "noisy-job") is None

    def test_unicode_content_is_preserved(self):
        content = "Copia de seguridad completada ✅ — 3 archivos añadidos"
        result = _clean_cron_output(content, "backup-diario")
        assert result == "[backup-diario] Copia de seguridad completada ✅ — 3 archivos añadidos"

    def test_unicode_content_truncates_by_character_not_byte(self):
        # Multi-byte chars (each 'é' is 2 bytes in UTF-8) must be counted as
        # one character each, matching the original inline slicing behavior.
        content = "é" * 700
        result = _clean_cron_output(content, None)
        assert result is not None
        assert len(result) == 600
        assert result.endswith("...")
        assert result[:597] == "é" * 597

    def test_truncates_long_single_line_to_600_chars(self):
        content = "x" * 1000
        result = _clean_cron_output(content, "long-job")
        assert result is not None
        # Prefix isn't counted against the 600-char summary cap — only the
        # summary itself is truncated before the "[job_name] " prefix is added.
        assert result == "[long-job] " + "x" * 597 + "..."

    def test_content_under_limit_is_not_truncated(self):
        content = "short line"
        result = _clean_cron_output(content, "job")
        assert result == "[job] short line"
        assert "..." not in result

    def test_more_than_four_lines_adds_overflow_marker(self):
        content = "\n".join(f"line {i}" for i in range(1, 8))
        result = _clean_cron_output(content, "many-lines")
        assert result == "[many-lines] line 1 | line 2 | line 3 | line 4 | (+3 líneas)"

    def test_four_or_fewer_lines_has_no_overflow_marker(self):
        content = "line 1\nline 2"
        result = _clean_cron_output(content, "job")
        assert result == "[job] line 1 | line 2"
        assert "líneas" not in result

    def test_noise_lines_are_dropped_but_real_content_kept(self):
        content = "[Cron]\nCronjob Response:\n(job_id: xyz)\nBackup finished OK\n---"
        result = _clean_cron_output(content, "backup")
        assert result == "[backup] Backup finished OK"

    def test_job_name_none_omits_prefix(self):
        result = _clean_cron_output("Task done", None)
        assert result == "Task done"

    def test_lines_are_stripped_of_surrounding_whitespace(self):
        content = "  leading and trailing spaces  \n\tsecond line\t"
        result = _clean_cron_output(content, "job")
        assert result == "[job] leading and trailing spaces | second line"
