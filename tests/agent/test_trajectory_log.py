import json
import logging
from pathlib import Path

import pytest

from agent.trajectory_log import TrajectoryLogger, trajectory_event
from agent.file_safety import get_read_block_error, is_write_denied
from tools.terminal_tool import _trajectory_command_block_error


def _events(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_append_only_events_have_contiguous_seq_and_survive_restart(tmp_path):
    first = TrajectoryLogger("session-1", root=tmp_path)
    first.append("turn.start", {"turn_id": "turn-1"})
    first.append("llm.request", {"request_id": "request-1"})

    second = TrajectoryLogger("session-1", root=tmp_path)
    second.append("turn.end", {"status": "completed"})

    events = _events(tmp_path / "session-1.jsonl")
    assert [event["seq"] for event in events] == [1, 2, 3]
    assert all("integrity" not in event for event in events)


def test_compression_records_discarded_messages_with_original_seq(tmp_path):
    logger = TrajectoryLogger("session-2", root=tmp_path)
    before = [
        {"role": "user", "content": "keep this"},
        {"role": "assistant", "content": "discard this answer"},
        {"role": "tool", "content": "discard this result", "tool_call_id": "x"},
    ]
    after = [
        {"role": "user", "content": "keep this"},
        {"role": "user", "content": "[CONTEXT SUMMARY] replacement"},
    ]

    logger.log_compression(before, after, before_tokens=900, after_tokens=120)

    event = _events(tmp_path / "session-2.jsonl")[0]
    assert event["type"] == "context.compression"
    assert event["data"]["before_count"] == 3
    assert event["data"]["after_count"] == 2
    assert event["data"]["discarded_messages"] == [
        {"role": "assistant", "seq": 2, "content": "discard this answer"},
        {"role": "tool", "seq": 3, "content": "discard this result", "tool_call_id": "x"},
    ]
    assert event["data"]["replacement_messages"] == [
        {"role": "user", "seq": None, "content": "[CONTEXT SUMMARY] replacement"}
    ]


def test_logging_failure_is_swallowed_and_logged(tmp_path, caplog):
    logger = TrajectoryLogger("session-3", root=tmp_path)
    blocked_parent = tmp_path / "not-a-directory"
    blocked_parent.write_text("not a directory", encoding="utf-8")
    logger.root = blocked_parent
    logger.path = blocked_parent / "trajectory.jsonl"
    with caplog.at_level(logging.WARNING):
        assert logger.append("turn.start", {"x": 1}) is False
    assert "trajectory logging failed" in caplog.text


def test_trajectory_event_helper_never_raises(monkeypatch):
    class BrokenLogger:
        def append(self, *_args, **_kwargs):
            raise RuntimeError("boom")

    assert trajectory_event(BrokenLogger(), "turn.start", {}) is False


def test_trajectory_directory_is_blocked_for_file_and_write_tools(tmp_path):
    trajectory = tmp_path / ".hermes" / "data" / "trajectory" / "secret.jsonl"
    trajectory.parent.mkdir(parents=True)
    trajectory.write_text("secret", encoding="utf-8")

    # The safety helper is tested against an explicit Hermes home so this test
    # does not depend on the host user's real profile.
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    try:
        assert get_read_block_error(str(trajectory))
        assert is_write_denied(str(trajectory))
    finally:
        monkeypatch.undo()


def test_trajectory_directory_is_blocked_for_shell_commands():
    assert _trajectory_command_block_error("cat ~/.hermes/data/trajectory/session.jsonl")
    assert _trajectory_command_block_error("python -c 'open(\"/home/u/.hermes/data/trajectory/x\")'")
    assert _trajectory_command_block_error("pwd") is None
