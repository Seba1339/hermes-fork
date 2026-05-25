"""
Hermes-Enhanced: Changelog automático por proyecto
Registra cada modificación que hace el agente.
"""

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("hermes_enhanced.changelog")

HERMES_HOME = Path(
    os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes-enhanced"))
)


def _changelog_path(project: str) -> Path:
    """Ruta al archivo de changelog del proyecto."""
    d = HERMES_HOME / "changelogs"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{project}.jsonl"


def log_modification(
    project: str,
    action: str,
    file_path: str,
    description: str = "",
    diff_summary: str = "",
    status: str = "ok",
) -> dict:
    """Registra una modificación en el changelog del proyecto.

    Args:
        project: Nombre del proyecto
        action: created, modified, deleted, refactored, tested
        file_path: Archivo modificado
        description: Qué se hizo
        diff_summary: Resumen del cambio
        status: ok, error, warning
    """
    entry = {
        "ts": time.time(),
        "datetime": datetime.now().isoformat(),
        "project": project,
        "action": action,
        "file": file_path,
        "description": description or action,
        "diff": diff_summary[:500],
        "status": status,
    }

    log_path = _changelog_path(project)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    logger.info("Changelog %s: %s %s", project, action, file_path)
    return entry


def get_changelog(project: str, limit: int = 20) -> List[Dict]:
    """Obtiene las últimas entradas del changelog."""
    log_path = _changelog_path(project)
    if not log_path.exists():
        return []

    entries = []
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    return entries[-limit:][::-1]  # Últimas primero


def get_all_changelogs(limit: int = 5) -> Dict[str, List[Dict]]:
    """Obtiene changelogs de todos los proyectos."""
    logs_dir = HERMES_HOME / "changelogs"
    if not logs_dir.exists():
        return {}

    result = {}
    for f in sorted(logs_dir.glob("*.jsonl"), reverse=True):
        project = f.stem
        result[project] = get_changelog(project, limit)

    return result


def format_changelog(project: str, limit: int = 5) -> str:
    """Formatea changelog como texto legible."""
    entries = get_changelog(project, limit)
    if not entries:
        return f"No hay cambios registrados para '{project}'."

    lines = [f"📋 Changelog: {project}"]
    for e in entries:
        icon = {
            "created": "✨",
            "modified": "📝",
            "deleted": "🗑️",
            "refactored": "♻️",
            "tested": "🧪",
        }.get(e["action"], "🔧")
        action = e["action"].upper()
        status_icon = (
            "✅" if e["status"] == "ok" else "❌" if e["status"] == "error" else "⚠️"
        )

        when = datetime.fromtimestamp(e["ts"]).strftime("%H:%M")
        lines.append(f"  {icon} {status_icon} [{when}] {action}: {e['file']}")
        if e.get("description") and e["description"] != e["action"]:
            lines.append(f"     {e['description'][:100]}")

    return "\n".join(lines)
