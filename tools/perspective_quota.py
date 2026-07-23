"""Quota guard for model perspective calls.

Limits are deliberately local and transparent: a small SQLite event ledger
survives gateway restarts and is safe when multiple tool calls race.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home


_DEFAULTS = {
    "enabled": True,
    "max_calls_per_session": 6,
    "max_calls_per_hour": 4,
    "max_turns": 3,
    "retry_empty": 1,
}


def _config() -> dict[str, Any]:
    try:
        from hermes_cli.config import load_config_readonly
        raw = load_config_readonly().get("perspectives", {})
        return {**_DEFAULTS, **(raw if isinstance(raw, dict) else {})}
    except Exception:
        return dict(_DEFAULTS)


def perspective_limits() -> dict[str, int | bool]:
    raw = _config()
    result: dict[str, int | bool] = {"enabled": bool(raw.get("enabled", True))}
    for key in ("max_calls_per_session", "max_calls_per_hour", "max_turns", "retry_empty"):
        try:
            result[key] = max(0, int(raw.get(key, _DEFAULTS[key])))
        except (TypeError, ValueError):
            result[key] = int(_DEFAULTS[key])
    return result


def _db_path() -> Path:
    data = get_hermes_home() / "data"
    data.mkdir(parents=True, exist_ok=True)
    return data / "perspective_usage.sqlite"


def reserve_perspective_call(session_id: str | None, perspective: str) -> dict[str, Any]:
    """Reserve one call, returning ``allowed`` plus usage and reason.

    A zero limit means unlimited for that dimension. Session identity is
    supplied by Hermes' dispatcher; the fallback keeps CLI/plugin calls safe.
    """
    limits = perspective_limits()
    if not limits["enabled"]:
        return {"allowed": False, "reason": "perspectives_disabled", "limits": limits}

    sid = str(session_id or "global")[:300]
    now = int(time.time())
    hour_ago = now - 3600
    db = _db_path()
    with sqlite3.connect(db, timeout=10) as conn:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS perspective_calls "
            "(id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, perspective TEXT NOT NULL, created_at INTEGER NOT NULL)"
        )
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM perspective_calls WHERE created_at < ?", (now - 86400,))
        session_calls = conn.execute(
            "SELECT COUNT(*) FROM perspective_calls WHERE session_id=? AND perspective=?",
            (sid, perspective),
        ).fetchone()[0]
        hour_calls = conn.execute(
            "SELECT COUNT(*) FROM perspective_calls WHERE perspective=? AND created_at>=?",
            (perspective, hour_ago),
        ).fetchone()[0]
        if limits["max_calls_per_session"] and session_calls >= limits["max_calls_per_session"]:
            conn.rollback()
            return {"allowed": False, "reason": "session_quota", "session_calls": session_calls, "hour_calls": hour_calls, "limits": limits}
        if limits["max_calls_per_hour"] and hour_calls >= limits["max_calls_per_hour"]:
            conn.rollback()
            return {"allowed": False, "reason": "hour_quota", "session_calls": session_calls, "hour_calls": hour_calls, "limits": limits}
        conn.execute(
            "INSERT INTO perspective_calls(session_id,perspective,created_at) VALUES(?,?,?)",
            (sid, perspective, now),
        )
        conn.commit()
    return {"allowed": True, "session_calls": session_calls + 1, "hour_calls": hour_calls + 1, "limits": limits}
