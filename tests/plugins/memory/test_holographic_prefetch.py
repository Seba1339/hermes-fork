"""Verification tests for HolographicMemoryProvider.prefetch() (Phase 4-verificación).

Fase 4 of docs/personal-system/ROADMAP.md calls for verifying the existing
``prefetch()`` implementation in ``plugins/memory/holographic/__init__.py``
against the proposal's contract — it does NOT redesign prefetch, since the
method already ships. These tests exercise the real ``MemoryStore`` /
``FactRetriever`` stack against a temporary SQLite database (small
``hrr_dim`` so HRR vector math stays cheap), following the same
``HolographicMemoryProvider(config=...)`` + ``provider.initialize(session_id)``
pattern already used by
``tests/plugins/memory/test_holographic_store.py::TestProviderShutdown``.
"""

from __future__ import annotations

import sqlite3

import pytest

from plugins.memory.holographic import HolographicMemoryProvider
from plugins.memory.holographic.store import MemoryStore


@pytest.fixture(autouse=True)
def _clean_shared_registry():
    """Each test starts and ends with an empty shared-connection registry.

    Mirrors the fixture in test_holographic_store.py — HolographicMemoryProvider
    goes through the same process-wide shared-connection MemoryStore registry,
    so leakage between tests would silently reuse another test's connection.
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


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "memory_store.db"


@pytest.fixture
def provider(db_path):
    """A HolographicMemoryProvider initialized against a temp SQLite DB.

    Small hrr_dim keeps HRR vector math cheap in tests. Yields the
    initialized provider and shuts it down (releasing its shared connection
    reference) afterward.
    """
    p = HolographicMemoryProvider(config={"db_path": str(db_path), "hrr_dim": 64})
    p.initialize("session-prefetch-verify")
    try:
        yield p
    finally:
        p.shutdown()


class TestPrefetchReturnsRelevantFacts:
    def test_prefetch_returns_header_and_relevant_fact(self, provider):
        provider._store.add_fact("The deploy pipeline uses GitHub Actions", category="project")

        result = provider.prefetch("deploy pipeline")

        assert result.startswith("## Holographic Memory")
        assert "GitHub Actions" in result

    def test_prefetch_only_includes_matching_facts(self, provider):
        provider._store.add_fact("The deploy pipeline uses GitHub Actions", category="project")
        provider._store.add_fact("Sebastian prefers dark mode editors", category="user_pref")

        result = provider.prefetch("deploy pipeline")

        assert "GitHub Actions" in result
        assert "dark mode" not in result


class TestPrefetchEmptyQuery:
    def test_empty_query_returns_empty_string(self, provider):
        provider._store.add_fact("The deploy pipeline uses GitHub Actions", category="project")

        assert provider.prefetch("") == ""


class TestPrefetchMinTrustFilter:
    def test_min_trust_excludes_low_trust_facts(self, db_path):
        # default_trust below the provider's configured min_trust_threshold
        # so the fact is stored but must never surface via prefetch().
        p = HolographicMemoryProvider(
            config={
                "db_path": str(db_path),
                "hrr_dim": 64,
                "default_trust": 0.1,
                "min_trust_threshold": 0.5,
            }
        )
        p.initialize("session-min-trust")
        try:
            p._store.add_fact("Low trust deploy note", category="project")
            result = p.prefetch("deploy note")
            assert result == ""
        finally:
            p.shutdown()

    def test_high_trust_fact_passes_min_trust(self, db_path):
        p = HolographicMemoryProvider(
            config={
                "db_path": str(db_path),
                "hrr_dim": 64,
                "default_trust": 0.9,
                "min_trust_threshold": 0.5,
            }
        )
        p.initialize("session-min-trust-pass")
        try:
            p._store.add_fact("High trust deploy note", category="project")
            result = p.prefetch("deploy note")
            assert "High trust deploy note" in result
        finally:
            p.shutdown()


class TestPrefetchWithoutRetriever:
    def test_uninitialized_provider_returns_empty_string(self):
        """A provider that was never initialize()'d has no _retriever."""
        p = HolographicMemoryProvider(config={"hrr_dim": 64})
        assert p._retriever is None
        assert p.prefetch("anything") == ""


class TestPrefetchRetrieverFailure:
    def test_retriever_exception_returns_empty_string_without_raising(self, provider, monkeypatch):
        provider._store.add_fact("The deploy pipeline uses GitHub Actions", category="project")

        def _boom(*args, **kwargs):
            raise RuntimeError("retriever exploded")

        monkeypatch.setattr(provider._retriever, "search", _boom)

        result = provider.prefetch("deploy pipeline")

        assert result == ""
