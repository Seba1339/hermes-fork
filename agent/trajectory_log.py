"""Best-effort append-only trajectory logging.

The trajectory log is deliberately independent from ``state.db``.  It records
what the active runtime prepared for the model and what came back, but logging
is observability only: every public helper catches its own failures so a broken
or unavailable log can never abort an agent turn.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")
_LOCKS: Dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _trajectory_root(root: Optional[Path] = None) -> Path:
    if root is not None:
        return Path(root)
    try:
        from hermes_constants import get_hermes_home

        home = Path(get_hermes_home())
    except Exception:
        home = Path(os.path.expanduser("~/.hermes"))
    return home / "data" / "trajectory"


def _safe_filename(session_id: str) -> str:
    value = _FILENAME_SAFE_RE.sub("_", str(session_id or "session"))
    value = value.strip("._") or "session"
    return f"{value}.jsonl"


def _lock_for(path: Path) -> threading.Lock:
    key = str(path)
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def _jsonable(value: Any) -> Any:
    """Return a JSON-safe value without allowing serialization to break logging."""
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except Exception:
        if isinstance(value, dict):
            return {str(k): _jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_jsonable(item) for item in value]
        return str(value)


def _read_next_seq(path: Path) -> int:
    last_seq = 0
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    value = json.loads(line).get("seq")
                    if isinstance(value, int) and value > last_seq:
                        last_seq = value
                except Exception:
                    continue
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning("trajectory sequence scan failed: %s", exc)
    return last_seq + 1


def _message_snapshot(message: Any, seq: Optional[int]) -> Dict[str, Any]:
    if not isinstance(message, dict):
        return {"role": None, "seq": seq, "content": _jsonable(message)}
    snapshot: Dict[str, Any] = {
        "role": message.get("role"),
        "seq": seq,
        "content": _jsonable(message.get("content")),
    }
    for key in ("tool_call_id", "name"):
        if key in message:
            snapshot[key] = _jsonable(message[key])
    return snapshot


def _message_key(message: Any) -> str:
    try:
        return json.dumps(_jsonable(message), ensure_ascii=False, sort_keys=True)
    except Exception:
        return repr(message)


class TrajectoryLogger:
    """Append JSONL events for one session, failing open on every error."""

    def __init__(self, session_id: str, root: Optional[Path] = None):
        self.session_id = str(session_id or "session")
        self.root = _trajectory_root(root)
        self.path = self.root / _safe_filename(self.session_id)
        self._next_seq: Optional[int] = None

    def append(self, event_type: str, data: Optional[Dict[str, Any]] = None) -> bool:
        """Append one event; return False and log normally if anything fails."""
        try:
            lock = _lock_for(self.path)
            with lock:
                if self._next_seq is None:
                    self._next_seq = _read_next_seq(self.path)
                event = {
                    "schema": "hermes.trajectory.v1",
                    "event_id": str(uuid.uuid4()),
                    "session_id": self.session_id,
                    "seq": self._next_seq,
                    "time": datetime.now(timezone.utc).isoformat(),
                    "type": str(event_type),
                    "data": _jsonable(data or {}),
                }
                line = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
                self.root.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line)
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    os.chmod(self.path, 0o600)
                except OSError:
                    pass
                self._next_seq += 1
            return True
        except Exception as exc:
            logger.warning("trajectory logging failed: %s", exc)
            return False

    def log_compression(
        self,
        before: Iterable[Any],
        after: Iterable[Any],
        *,
        before_tokens: Optional[int] = None,
        after_tokens: Optional[int] = None,
        reason: Optional[str] = None,
    ) -> bool:
        """Record compression and the exact messages removed/replaced.

        ``seq`` in message snapshots is the 1-based position in the original
        pre-compression message list.  Replacement messages have ``seq: null``
        because they did not exist in that original list.
        """
        before_list = list(before)
        after_list = list(after)
        remaining = [_message_key(item) for item in after_list]
        kept = set(remaining)
        discarded = []
        for index, message in enumerate(before_list, start=1):
            key = _message_key(message)
            if key in kept:
                kept.remove(key)
            else:
                discarded.append(_message_snapshot(message, index))
        before_keys = {_message_key(item) for item in before_list}
        replacement = [
            _message_snapshot(message, None)
            for message in after_list
            if _message_key(message) not in before_keys
        ]
        return self.append(
            "context.compression",
            {
                "reason": reason,
                "before_count": len(before_list),
                "after_count": len(after_list),
                "before_tokens": before_tokens,
                "after_tokens": after_tokens,
                "discarded_messages": discarded,
                "replacement_messages": replacement,
            },
        )


def trajectory_event(logger_obj: Any, event_type: str, data: Optional[Dict[str, Any]] = None) -> bool:
    """Call a trajectory logger defensively from hot-path code."""
    try:
        if logger_obj is None:
            return False
        return bool(logger_obj.append(event_type, data or {}))
    except Exception as exc:
        logger.warning("trajectory logging failed: %s", exc)
        return False


def get_trajectory_logger(agent: Any) -> Optional[TrajectoryLogger]:
    """Return/create the logger attached to an agent, without raising."""
    try:
        existing = getattr(agent, "_trajectory_logger", None)
        session_id = str(getattr(agent, "session_id", "") or "")
        if existing is not None and getattr(existing, "session_id", None) == session_id:
            return existing
        if not session_id:
            return None
        existing = TrajectoryLogger(session_id)
        agent._trajectory_logger = existing
        return existing
    except Exception as exc:
        logger.warning("trajectory logger initialization failed: %s", exc)
        return None
