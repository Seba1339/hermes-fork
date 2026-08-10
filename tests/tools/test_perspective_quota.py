"""Behavior tests for the perspective quota guard (tools/perspective_quota.py).

This module gates how often the agent may consult an external "perspective"
(claude/gemini/deepseek) per session and per hour, backed by a small SQLite
ledger under HERMES_HOME/data/perspective_usage.sqlite. The `_isolate_hermes_home`
autouse fixture (tests/conftest.py) already redirects HERMES_HOME to a temp
dir for every test in this repo, so these tests never touch real quota data.
"""
import time

import pytest

from tools import perspective_quota as pq


@pytest.fixture(autouse=True)
def _reset_config_cache(monkeypatch):
    # _config() reads config.yaml via load_config_readonly(); force the
    # built-in defaults so tests are independent of any local config.yaml.
    monkeypatch.setattr(pq, "_config", lambda: dict(pq._DEFAULTS))


class TestPerspectiveLimits:
    def test_defaults_are_returned_when_config_is_empty(self):
        limits = pq.perspective_limits()
        assert limits == pq._DEFAULTS

    def test_negative_config_values_are_clamped_to_zero(self, monkeypatch):
        monkeypatch.setattr(pq, "_config", lambda: {**pq._DEFAULTS, "max_calls_per_session": -5})
        limits = pq.perspective_limits()
        assert limits["max_calls_per_session"] == 0


class TestReservePerspectiveCall:
    def test_disabled_perspectives_are_never_allowed(self, monkeypatch):
        monkeypatch.setattr(pq, "_config", lambda: {**pq._DEFAULTS, "enabled": False})
        result = pq.reserve_perspective_call("session-1", "claude")
        assert result["allowed"] is False
        assert result["reason"] == "perspectives_disabled"

    def test_first_call_in_a_session_is_allowed(self):
        result = pq.reserve_perspective_call("session-1", "claude")
        assert result["allowed"] is True
        assert result["session_calls"] == 1

    def test_session_quota_blocks_after_max_calls_per_session(self, monkeypatch):
        monkeypatch.setattr(pq, "_config", lambda: {**pq._DEFAULTS, "max_calls_per_session": 2})
        sid = "session-quota-test"
        assert pq.reserve_perspective_call(sid, "claude")["allowed"] is True
        assert pq.reserve_perspective_call(sid, "claude")["allowed"] is True
        blocked = pq.reserve_perspective_call(sid, "claude")
        assert blocked["allowed"] is False
        assert blocked["reason"] == "session_quota"

    def test_session_quota_is_per_perspective_not_shared(self, monkeypatch):
        # A session that has exhausted its "claude" quota must still be able
        # to reserve a "gemini" call — the ledger keys on (session, perspective).
        monkeypatch.setattr(pq, "_config", lambda: {**pq._DEFAULTS, "max_calls_per_session": 1})
        sid = "session-per-perspective"
        assert pq.reserve_perspective_call(sid, "claude")["allowed"] is True
        assert pq.reserve_perspective_call(sid, "claude")["allowed"] is False
        assert pq.reserve_perspective_call(sid, "gemini")["allowed"] is True

    def test_hour_quota_blocks_across_sessions(self, monkeypatch):
        monkeypatch.setattr(pq, "_config", lambda: {**pq._DEFAULTS, "max_calls_per_hour": 1, "max_calls_per_session": 0})
        assert pq.reserve_perspective_call("session-a", "deepseek")["allowed"] is True
        blocked = pq.reserve_perspective_call("session-b", "deepseek")
        assert blocked["allowed"] is False
        assert blocked["reason"] == "hour_quota"

    def test_zero_limit_means_unlimited_for_that_dimension(self, monkeypatch):
        monkeypatch.setattr(
            pq, "_config",
            lambda: {**pq._DEFAULTS, "max_calls_per_session": 0, "max_calls_per_hour": 0},
        )
        sid = "session-unlimited"
        for _ in range(10):
            assert pq.reserve_perspective_call(sid, "claude")["allowed"] is True

    def test_missing_session_id_falls_back_to_global(self):
        result = pq.reserve_perspective_call(None, "claude")
        assert result["allowed"] is True

    def test_ledger_persists_across_calls_via_sqlite(self, monkeypatch):
        # Two separate calls must accumulate in the same on-disk ledger
        # rather than resetting — this is the durability guarantee the
        # module's docstring promises ("survives gateway restarts").
        monkeypatch.setattr(pq, "_config", lambda: {**pq._DEFAULTS, "max_calls_per_session": 5})
        sid = "session-persist"
        first = pq.reserve_perspective_call(sid, "claude")
        second = pq.reserve_perspective_call(sid, "claude")
        assert first["session_calls"] == 1
        assert second["session_calls"] == 2
        assert pq._db_path().exists()
