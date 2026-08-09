import json
from pathlib import Path

import pytest

from scripts import memory_migrate_detached as runner


def test_plan_only_does_not_call_subprocess(tmp_path, monkeypatch, capsys):
    called = []
    monkeypatch.setattr(runner, "run_detached", lambda *a, **k: called.append(a))
    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    backup = tmp_path / "backup"
    source.touch()
    target.touch()

    assert runner.main(["--source", str(source), "--target", str(target), "--backup-dir", str(backup)]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["executed"] is False
    assert called == []
    assert str(runner.VENV_PYTHON) in plan["command"]
    assert "--user" in plan["command"]


def test_apply_requires_execute(tmp_path, capsys):
    rc = runner.main([
        "--source", str(tmp_path / "source.db"),
        "--target", str(tmp_path / "target.db"),
        "--backup-dir", str(tmp_path / "backup"),
        "--apply",
    ])
    assert rc == 2
    assert "requires --execute" in capsys.readouterr().err


def test_gateway_unit_name_is_rejected():
    with pytest.raises(runner.DetachedRunnerError):
        runner.check_unit_name("hermes-enhanced-gateway")


def test_real_hermes_paths_are_rejected(tmp_path, capsys):
    rc = runner.main([
        "--source", "/home/ubuntu/.hermes/data/agent_memory.db",
        "--target", str(tmp_path / "target.db"),
        "--backup-dir", str(tmp_path / "backup"),
    ])
    assert rc == 2
    assert "refusing" in capsys.readouterr().err


def test_rollback_restores_source_and_target(tmp_path):
    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    source_backup = tmp_path / "source.bak"
    target_backup = tmp_path / "target.bak"
    source.write_text("new-source")
    target.write_text("new-target")
    source_backup.write_text("old-source")
    target_backup.write_text("old-target")

    runner.rollback_from_snapshot(
        {"source_backup": str(source_backup), "target_backup": str(target_backup)},
        source,
        target,
    )
    assert source.read_text() == "old-source"
    assert target.read_text() == "old-target"


def test_result_json_and_log_are_written(tmp_path):
    files = runner._write_json_and_log({"ok": True, "rolled_back": False}, tmp_path, prefix="test")
    assert Path(files["result_json"]).exists()
    assert Path(files["result_log"]).exists()
    assert json.loads(Path(files["result_json"]).read_text())["ok"] is True
