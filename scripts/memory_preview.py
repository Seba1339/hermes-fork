#!/usr/bin/env python3
"""Preview which facts holographic memory auto-extraction would produce.

Reads a conversation transcript from a file the caller supplies explicitly
(``--input``) and prints the candidate facts
``HolographicMemoryProvider.preview_extracted_facts()`` detects — the same
pure, no-SQLite detection `_auto_extract_facts` uses to persist, minus the
persistence. Nothing is written to disk and no fact is ever stored.

This script deliberately never resolves ``HERMES_HOME`` or opens any Hermes
database (``state.db``, ``agent_memory.db``, ``memory_store.db``,
``bujo.sqlite``): it instantiates ``HolographicMemoryProvider`` with an
explicit config and skips ``initialize()`` entirely, so the only data it
ever touches is the ``--input`` file the caller names on the command line.

Input format (``--input PATH``):
  - JSON: a single JSON array of message objects, each with ``role`` and
    ``content`` keys, e.g. ``[{"role": "user", "content": "..."}]``.
  - JSONL: one JSON object per line, same per-message shape.

Usage:
    python3 scripts/memory_preview.py --input transcript.json
    python3 scripts/memory_preview.py --input transcript.jsonl --session-id sess-42
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List

# Allow importing plugins.memory.holographic when run as a plain script.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def _parse_messages(text: str) -> List[dict]:
    """Parse `text` as a JSON array or as JSONL, one object per line.

    Tries whole-file JSON first (a single array). If that fails, falls back
    to JSONL (one JSON object per non-blank line). Raises ValueError with a
    caller-facing message on any other shape or malformed line.
    """
    stripped = text.strip()
    if not stripped:
        return []

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None

    if parsed is not None:
        if not isinstance(parsed, list):
            raise ValueError(
                "Input JSON must be a list of messages, got "
                f"{type(parsed).__name__}"
            )
        messages = parsed
    else:
        messages = []
        for lineno, line in enumerate(stripped.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {lineno}: {exc}") from exc
            messages.append(obj)

    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            raise ValueError(
                f"Message {i} must be a JSON object, got {type(msg).__name__}"
            )

    return messages


def _build_provider():
    """Instantiate HolographicMemoryProvider with no config-file lookup.

    A non-empty explicit `config` dict short-circuits the constructor's
    `config or _load_plugin_config()` fallback, so no attempt is ever made
    to resolve HERMES_HOME or read config.yaml. `initialize()` is never
    called, so no store, retriever, or database connection is created.
    """
    from plugins.memory.holographic import HolographicMemoryProvider

    return HolographicMemoryProvider(config={"auto_extract": False})


def run(input_path: str, session_id: str | None) -> dict:
    path = Path(input_path)
    text = path.read_text(encoding="utf-8")
    messages = _parse_messages(text)

    provider = _build_provider()
    provider._session_id = session_id

    candidates = provider.preview_extracted_facts(messages)
    return {
        "candidates": candidates,
        "count": len(candidates),
        "session_id": session_id,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Preview candidate facts holographic memory auto-extraction "
            "would produce from a conversation transcript, without storing "
            "anything."
        )
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to a JSON (array of messages) or JSONL transcript file.",
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="Session id to stamp candidate facts with (default: none).",
    )
    args = parser.parse_args(argv)

    try:
        result = run(args.input, args.session_id)
    except FileNotFoundError:
        print(f"Error: input file not found: {args.input}", file=sys.stderr)
        return 1
    except IsADirectoryError:
        print(f"Error: input path is a directory: {args.input}", file=sys.stderr)
        return 1
    except (ValueError, UnicodeDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
