"""Claude Pro perspective tool via the official Claude Code CLI.

This deliberately wraps Claude Code instead of treating a Claude.ai OAuth
session as an Anthropic API key.  It is review-only by default and is exposed
only when the CLI is installed and authenticated.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from tools.registry import registry, tool_error, tool_result
from tools.perspective_quota import perspective_limits, reserve_perspective_call


_CLAUDE_CANDIDATES = (
    "claude",
    "/home/ubuntu/.local/bin/claude",
    "/home/ubuntu/.npm-global/bin/claude",
)
_MAX_PROMPT_CHARS = 60_000
_MAX_RESULT_CHARS = 40_000


def _claude_command() -> str | None:
    """Find the Claude Code executable without depending on service PATH."""
    found = shutil.which("claude")
    if found:
        return found
    for candidate in _CLAUDE_CANDIDATES[1:]:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _claude_env() -> dict[str, str]:
    env = os.environ.copy()
    extra = ["/home/ubuntu/.local/bin", "/home/ubuntu/.npm-global/bin"]
    env["PATH"] = ":".join(extra + [env.get("PATH", "")])
    return env


def _check_claude_auth() -> bool:
    command = _claude_command()
    if not command:
        return False
    try:
        completed = subprocess.run(
            [command, "auth", "status"],
            capture_output=True,
            text=True,
            timeout=10,
            env=_claude_env(),
            check=False,
        )
        data = json.loads(completed.stdout)
        return bool(data.get("loggedIn")) and data.get("authMethod") == "claude.ai"
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return False


def _extract_json_line(stdout: str) -> dict[str, Any] | None:
    """Claude may print diagnostics before its final JSON envelope."""
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _handle_claude_perspective(args: dict, **_kwargs) -> str:
    if not _check_claude_auth():
        return tool_error(
            "Claude Code no está instalado o no tiene una sesión Claude.ai OAuth activa.",
            code="claude_oauth_unavailable",
        )
    quota = reserve_perspective_call(_kwargs.get("session_id"), "claude")
    if not quota["allowed"]:
        return tool_error(
            "Cuota de Claude alcanzada; usa otra perspectiva o continúa más tarde.",
            code=quota["reason"],
            usage={k: quota.get(k) for k in ("session_calls", "hour_calls", "limits")},
        )

    prompt = str(args.get("prompt", "")).strip()
    if not prompt:
        return tool_error("prompt es obligatorio", code="invalid_prompt")
    if len(prompt) > _MAX_PROMPT_CHARS:
        return tool_error(
            f"prompt supera el límite de {_MAX_PROMPT_CHARS} caracteres",
            code="prompt_too_large",
        )

    role = str(args.get("role", "critical_reviewer")).strip() or "critical_reviewer"
    context = str(args.get("context", "")).strip()
    workdir = str(args.get("workdir", os.getcwd())).strip() or os.getcwd()
    workdir_path = Path(workdir).expanduser()
    if not workdir_path.is_absolute() or not workdir_path.is_dir():
        return tool_error(
            "workdir debe ser un directorio absoluto existente",
            code="invalid_workdir",
        )

    limits = perspective_limits()
    try:
        max_turns = max(1, min(int(args.get("max_turns", limits["max_turns"])), int(limits["max_turns"])))
    except (TypeError, ValueError):
        max_turns = int(limits["max_turns"])

    role_prompt = (
        "Eres una perspectiva independiente dentro del panel de Hermes. "
        f"Tu rol es: {role}. Analiza con honestidad, señala incertidumbres y "
        "no inventes resultados de pruebas. No edites archivos ni ejecutes comandos. "
        "Devuelve una respuesta estructurada con: conclusión, supuestos, riesgos, "
        "confianza y qué evidencia cambiaría tu conclusión."
    )
    if context:
        role_prompt += f"\n\nContexto adicional:\n{context}"

    command = _claude_command()
    assert command is not None
    cmd = [
        command,
        "-p",
        prompt,
        "--output-format",
        "json",
        "--no-session-persistence",
        "--tools",
        "",
        "--permission-mode",
        "plan",
        "--max-turns",
        str(max_turns),
        "--append-system-prompt",
        role_prompt,
    ]
    envelope = None
    completed = None
    attempts = 1 + int(limits["retry_empty"])
    for attempt in range(attempts):
        try:
            completed = subprocess.run(
                cmd,
                cwd=str(workdir_path),
                capture_output=True,
                text=True,
                timeout=180,
                env=_claude_env(),
                check=False,
            )
        except subprocess.TimeoutExpired:
            return tool_error("Claude agotó el límite de 180 segundos", code="timeout")
        except OSError as exc:
            return tool_error(f"No se pudo ejecutar Claude Code: {exc}", code="exec_error")
        envelope = _extract_json_line(completed.stdout)
        if envelope and str(envelope.get("result", "")).strip():
            break
        if attempt + 1 < attempts:
            cmd[2] = prompt + "\n\nSi tu respuesta anterior quedó vacía, devuelve ahora el análisis solicitado de forma breve y estructurada."

    if not envelope:
        return tool_error(
            "Claude Code no devolvió una respuesta JSON válida",
            code="invalid_claude_output",
            exit_code=completed.returncode if completed else None,
            stderr=completed.stderr[-2_000:] if completed else "",
        )

    assert completed is not None
    result = str(envelope.get("result", "")).strip()
    if not result:
        return tool_error(
            "Claude Code devolvió una respuesta vacía después del reintento",
            code="empty_claude_output",
            exit_code=completed.returncode if completed else None,
        )
    if len(result) > _MAX_RESULT_CHARS:
        result = result[:_MAX_RESULT_CHARS] + "\n[resultado truncado]"
    if envelope.get("is_error") or completed.returncode != 0:
        return tool_error(
            result or "Claude Code devolvió un error",
            code="claude_error",
            exit_code=completed.returncode,
        )

    return tool_result(
        success=True,
        perspective="claude",
        role=role,
        result=result,
        model_usage=envelope.get("modelUsage", {}),
        session_id=envelope.get("session_id"),
    )


CLAUDE_PERSPECTIVE_SCHEMA = {
    "name": "claude_perspective",
    "description": (
        "Consulta Claude Pro mediante Claude Code OAuth como perspectiva independiente. "
        "Modo revisión, sin edición ni comandos. Úsalo para crítica, arquitectura, "
        "riesgos y segunda opinión; no lo invoques para cada tarea trivial."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "Problema o propuesta a analizar."},
            "role": {
                "type": "string",
                "description": "Rol de Claude, por ejemplo critical_reviewer, architect o code_reviewer.",
                "default": "critical_reviewer",
            },
            "context": {"type": "string", "description": "Contexto adicional relevante, sin secretos."},
            "workdir": {"type": "string", "description": "Directorio absoluto del proyecto, si aplica."},
            "max_turns": {"type": "integer", "minimum": 1, "maximum": 8, "default": 3},
        },
        "required": ["prompt"],
        "additionalProperties": False,
    },
}


registry.register(
    name="claude_perspective",
    toolset="perspectives",
    schema=CLAUDE_PERSPECTIVE_SCHEMA,
    handler=_handle_claude_perspective,
    check_fn=_check_claude_auth,
    emoji="🧭",
    max_result_size_chars=_MAX_RESULT_CHARS,
)
