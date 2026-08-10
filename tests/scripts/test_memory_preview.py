"""Tests for scripts/memory_preview.py: safe extraction preview CLI.

Covers the ``--input`` parsing (JSON array vs. JSONL), role filtering (only
``role="user"`` messages become candidates, matching
``HolographicMemoryProvider.preview_extracted_facts`` — see
tests/plugins/memory/test_holographic_extraction_dry_run.py), ``--session-id``
stamping, and every error path (missing file, directory path, malformed
JSON, non-list JSON shape, non-dict message). Every test also asserts no
database or extra file is ever created, since this script must never touch
``HERMES_HOME`` or any Hermes SQLite store — see the script's module
docstring.

All input files live under ``tmp_path``; ``main()`` is invoked directly
with explicit argv. ``HERMES_HOME`` isolation is inherited from the
project conftest's autouse ``_hermetic_environment`` fixture, so no real
HOME or database is ever reachable from this process.
"""

import json

import scripts.memory_preview as memory_preview


def _write(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


class TestJsonArrayInput:
    def test_json_array_produces_candidates(self, tmp_path, capsys):
        path = _write(
            tmp_path,
            "transcript.json",
            json.dumps(
                [
                    {"role": "user", "content": "I prefer dark mode editors for long sessions"},
                    {"role": "user", "content": "We decided to use SQLite for the memory store"},
                ]
            ),
        )
        rc = memory_preview.main(["--input", str(path)])
        out = json.loads(capsys.readouterr().out)

        assert rc == 0
        assert out["count"] == 2
        assert len(out["candidates"]) == 2
        assert out["session_id"] is None

    def test_empty_json_array_returns_no_candidates(self, tmp_path, capsys):
        path = _write(tmp_path, "empty.json", "[]")
        rc = memory_preview.main(["--input", str(path)])
        out = json.loads(capsys.readouterr().out)

        assert rc == 0
        assert out == {"candidates": [], "count": 0, "session_id": None}


class TestJsonlInput:
    def test_jsonl_produces_candidates(self, tmp_path, capsys):
        lines = "\n".join(
            [
                json.dumps({"role": "user", "content": "I prefer dark mode editors for long sessions"}),
                json.dumps({"role": "assistant", "content": "Noted"}),
            ]
        )
        path = _write(tmp_path, "transcript.jsonl", lines)
        rc = memory_preview.main(["--input", str(path)])
        out = json.loads(capsys.readouterr().out)

        assert rc == 0
        assert out["count"] == 1
        assert out["candidates"][0]["category"] == "user_pref"

    def test_jsonl_blank_lines_are_skipped(self, tmp_path, capsys):
        content = "\n".join(
            [
                json.dumps({"role": "user", "content": "I prefer dark mode editors for long sessions"}),
                "",
                json.dumps({"role": "user", "content": "We decided to use SQLite for the memory store"}),
            ]
        )
        path = _write(tmp_path, "transcript.jsonl", content)
        rc = memory_preview.main(["--input", str(path)])
        out = json.loads(capsys.readouterr().out)

        assert rc == 0
        assert out["count"] == 2


class TestInvalidJson:
    def test_malformed_jsonl_line_reports_error(self, tmp_path, capsys):
        path = _write(tmp_path, "bad.jsonl", "not valid json\nalso not valid")
        rc = memory_preview.main(["--input", str(path)])
        captured = capsys.readouterr()

        assert rc == 1
        assert "Invalid JSON on line 1" in captured.err
        assert captured.out == ""

    def test_json_object_instead_of_list_is_rejected(self, tmp_path, capsys):
        path = _write(tmp_path, "object.json", json.dumps({"role": "user", "content": "hi"}))
        rc = memory_preview.main(["--input", str(path)])
        captured = capsys.readouterr()

        assert rc == 1
        assert "must be a list of messages" in captured.err

    def test_non_dict_message_is_rejected(self, tmp_path, capsys):
        path = _write(tmp_path, "badmsg.json", json.dumps(["not-a-dict"]))
        rc = memory_preview.main(["--input", str(path)])
        captured = capsys.readouterr()

        assert rc == 1
        assert "must be a JSON object" in captured.err


class TestRoleFiltering:
    def test_only_user_role_messages_are_detected(self, tmp_path, capsys):
        path = _write(
            tmp_path,
            "roles.json",
            json.dumps(
                [
                    {"role": "assistant", "content": "I prefer dark mode editors too"},
                    {"role": "system", "content": "We decided to use SQLite"},
                    {"role": "user", "content": "I prefer dark mode editors for long sessions"},
                ]
            ),
        )
        rc = memory_preview.main(["--input", str(path)])
        out = json.loads(capsys.readouterr().out)

        assert rc == 0
        assert out["count"] == 1
        assert out["candidates"][0]["content"] == "I prefer dark mode editors for long sessions"


class TestSessionId:
    def test_session_id_stamps_candidates_and_output(self, tmp_path, capsys):
        path = _write(
            tmp_path,
            "transcript.json",
            json.dumps([{"role": "user", "content": "I prefer dark mode editors for long sessions"}]),
        )
        rc = memory_preview.main(["--input", str(path), "--session-id", "sess-42"])
        out = json.loads(capsys.readouterr().out)

        assert rc == 0
        assert out["session_id"] == "sess-42"
        assert out["candidates"][0]["session_id"] == "sess-42"

    def test_missing_session_id_defaults_to_none(self, tmp_path, capsys):
        path = _write(
            tmp_path,
            "transcript.json",
            json.dumps([{"role": "user", "content": "I prefer dark mode editors for long sessions"}]),
        )
        rc = memory_preview.main(["--input", str(path)])
        out = json.loads(capsys.readouterr().out)

        assert out["session_id"] is None
        assert out["candidates"][0]["session_id"] is None


class TestFileErrors:
    def test_missing_file_reports_error(self, tmp_path, capsys):
        missing = tmp_path / "does-not-exist.json"
        rc = memory_preview.main(["--input", str(missing)])
        captured = capsys.readouterr()

        assert rc == 1
        assert "input file not found" in captured.err
        assert captured.out == ""

    def test_directory_path_reports_error(self, tmp_path, capsys):
        directory = tmp_path / "a-directory"
        directory.mkdir()
        rc = memory_preview.main(["--input", str(directory)])
        captured = capsys.readouterr()

        assert rc == 1
        assert "input path is a directory" in captured.err


class TestNoDatabaseTouched:
    def test_no_files_created_besides_input(self, tmp_path, capsys):
        path = _write(
            tmp_path,
            "transcript.json",
            json.dumps([{"role": "user", "content": "We decided to use SQLite for the memory store"}]),
        )
        before = set(tmp_path.iterdir())

        rc = memory_preview.main(["--input", str(path)])
        capsys.readouterr()

        assert rc == 0
        after = set(tmp_path.iterdir())
        assert after == before
        assert not (tmp_path / "memory_store.db").exists()
