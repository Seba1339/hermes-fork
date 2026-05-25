"""
Hermes-Enhanced: Project Knowledge Base

Mantiene contexto persistente por proyecto entre sesiones.
Cada proyecto tiene: arquitectura, convenciones, decisiones, errores conocidos.

Esto evita que el agente empiece de cero cada vez que toca un proyecto.

Uso:
    from hermes_enhanced.coding import project_knowledge

    # Al empezar a trabajar en un proyecto
    ctx = project_knowledge.load("hermes_app")

    # Después de descubrir algo importante
    project_knowledge.record_decision("hermes_app", "Usar Flutter 3.24.0 arm64-only")

    # Después de un error
    project_knowledge.record_error("hermes_app", "Pantalla negra con Flutter 3.27+",
                                     "Usar --target-platform android-arm64")
"""

import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("hermes_enhanced.project_kb")

HERMES_HOME = Path(
    os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes-enhanced"))
)
KB_PATH = HERMES_HOME / "data" / "project_knowledge.db"


def _get_db() -> sqlite3.Connection:
    """Obtiene conexión a la base de conocimiento de proyectos."""
    os.makedirs(KB_PATH.parent, exist_ok=True)
    conn = sqlite3.connect(str(KB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            name TEXT PRIMARY KEY,
            language TEXT,
            root_dir TEXT,
            description TEXT,
            created_at REAL,
            updated_at REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project TEXT,
            decision TEXT,
            reason TEXT,
            alternatives TEXT,
            made_at REAL,
            FOREIGN KEY (project) REFERENCES projects(name)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS errors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project TEXT,
            error TEXT,
            solution TEXT,
            occurred_at REAL,
            frequency INTEGER DEFAULT 1,
            FOREIGN KEY (project) REFERENCES projects(name)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conventions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project TEXT,
            convention TEXT,
            pattern TEXT,
            added_at REAL,
            FOREIGN KEY (project) REFERENCES projects(name)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project TEXT,
            file_glob TEXT,
            description TEXT,
            template TEXT,
            added_at REAL,
            FOREIGN KEY (project) REFERENCES projects(name)
        )
    """)
    conn.commit()
    return conn


# ── API pública ────────────────────────────────────────────────────────


def register_project(name: str, language: str, root_dir: str, description: str = ""):
    """Registra un proyecto en la base de conocimiento."""
    conn = _get_db()
    now = time.time()
    conn.execute(
        """
        INSERT OR REPLACE INTO projects (name, language, root_dir, description, created_at, updated_at)
        VALUES (?, ?, ?, ?, COALESCE((SELECT created_at FROM projects WHERE name=?), ?), ?)
    """,
        (name, language, root_dir, description, name, now, now),
    )
    conn.commit()
    conn.close()
    logger.info("Proyecto registrado: %s (%s)", name, language)


def record_decision(
    project: str, decision: str, reason: str = "", alternatives: str = ""
):
    """Registra una decisión de arquitectura o diseño."""
    conn = _get_db()
    conn.execute(
        "INSERT INTO decisions (project, decision, reason, alternatives, made_at) VALUES (?, ?, ?, ?, ?)",
        (project, decision, reason, alternatives, time.time()),
    )
    conn.commit()
    conn.close()


def record_error(project: str, error: str, solution: str = ""):
    """Registra un error conocido y su solución."""
    conn = _get_db()
    # Verificar si ya existe un error similar
    cur = conn.execute(
        "SELECT id, frequency FROM errors WHERE project=? AND error LIKE ?",
        (project, f"%{error[:50]}%"),
    )
    existing = cur.fetchone()
    if existing:
        conn.execute(
            "UPDATE errors SET frequency = frequency + 1, solution = ?, occurred_at = ? WHERE id = ?",
            (solution, time.time(), existing["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO errors (project, error, solution, occurred_at) VALUES (?, ?, ?, ?)",
            (project, error, solution, time.time()),
        )
    conn.commit()
    conn.close()


def record_convention(project: str, convention: str, pattern: str = ""):
    """Registra una convención de código."""
    conn = _get_db()
    conn.execute(
        "INSERT INTO conventions (project, convention, pattern, added_at) VALUES (?, ?, ?, ?)",
        (project, convention, pattern, time.time()),
    )
    conn.commit()
    conn.close()


def record_pattern(project: str, file_glob: str, description: str, template: str = ""):
    """Registra un patrón de código recurrente."""
    conn = _get_db()
    conn.execute(
        "INSERT INTO patterns (project, file_glob, description, template, added_at) VALUES (?, ?, ?, ?, ?)",
        (project, file_glob, description, template, time.time()),
    )
    conn.commit()
    conn.close()


def load(project: str) -> Dict:
    """Carga todo el contexto conocido de un proyecto."""
    conn = _get_db()
    context = {
        "project": None,
        "decisions": [],
        "known_errors": [],
        "conventions": [],
        "patterns": [],
    }

    # Proyecto
    cur = conn.execute("SELECT * FROM projects WHERE name = ?", (project,))
    row = cur.fetchone()
    if row:
        context["project"] = dict(row)
    else:
        conn.close()
        return context

    # Decisiones
    cur = conn.execute(
        "SELECT * FROM decisions WHERE project = ? ORDER BY made_at DESC LIMIT 20",
        (project,),
    )
    context["decisions"] = [dict(r) for r in cur.fetchall()]

    # Errores
    cur = conn.execute(
        "SELECT * FROM errors WHERE project = ? ORDER BY frequency DESC, occurred_at DESC LIMIT 20",
        (project,),
    )
    context["known_errors"] = [dict(r) for r in cur.fetchall()]

    # Convenciones
    cur = conn.execute(
        "SELECT * FROM conventions WHERE project = ? ORDER BY added_at DESC", (project,)
    )
    context["conventions"] = [dict(r) for r in cur.fetchall()]

    # Patrones
    cur = conn.execute(
        "SELECT * FROM patterns WHERE project = ? ORDER BY added_at DESC", (project,)
    )
    context["patterns"] = [dict(r) for r in cur.fetchall()]

    conn.close()
    return context


def get_project_list() -> List[Dict]:
    """Lista todos los proyectos registrados con sus stats."""
    conn = _get_db()
    cur = conn.execute("""
        SELECT p.*,
               (SELECT COUNT(*) FROM decisions WHERE project=p.name) as decisions_count,
               (SELECT COUNT(*) FROM errors WHERE project=p.name) as errors_count,
               (SELECT COUNT(*) FROM conventions WHERE project=p.name) as conventions_count
        FROM projects p
        ORDER BY p.updated_at DESC
    """)
    projects = [dict(r) for r in cur.fetchall()]
    conn.close()
    return projects


def format_context(project: str) -> str:
    """Formatea el contexto de un proyecto como texto legible para inyectar."""
    ctx = load(project)
    if not ctx["project"]:
        return f"Proyecto '{project}' no encontrado en la base de conocimiento."

    lines = []
    p = ctx["project"]
    lines.append(f"📁 {p['name']} ({p['language']})")
    lines.append(f"   {p['description']}")
    lines.append(f"   Dir: {p['root_dir']}")
    lines.append("")

    if ctx["decisions"]:
        lines.append("📐 Decisiones de arquitectura:")
        for d in ctx["decisions"]:
            lines.append(f"  • {d['decision']}")
            if d.get("reason"):
                lines.append(f"    Razón: {d['reason']}")

    if ctx["known_errors"]:
        lines.append("")
        lines.append("⚠️  Errores conocidos:")
        for e in ctx["known_errors"]:
            lines.append(f"  • {e['error'][:100]} (x{e['frequency']})")
            if e.get("solution"):
                lines.append(f"    Solución: {e['solution'][:100]}")

    if ctx["conventions"]:
        lines.append("")
        lines.append("📏 Convenciones:")
        for c in ctx["conventions"]:
            lines.append(f"  • {c['convention']}")

    if ctx["patterns"]:
        lines.append("")
        lines.append("🔁 Patrones recurrentes:")
        for p in ctx["patterns"]:
            lines.append(f"  • {p['file_glob']}: {p['description']}")

    return "\n".join(lines)
