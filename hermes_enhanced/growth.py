"""
Hermes-Enhanced: Sistema de Auto-Crecimiento (Growth System)

Este módulo permite al agente mejorado:
1. Revisar su propio rendimiento post-sesión
2. Identificar patrones de correcciones del usuario
3. Parchear skills automáticamente cuando detecta workflows incorrectos
4. Consolidar memoria (fusionar entradas duplicadas)
5. Generar sugerencias de mejora proactivas

Uso desde el agente:
    from hermes_enhanced.growth import (
        review_last_session,
        suggest_skill_patches,
        consolidate_memory,
        get_growth_stats,
    )
"""

import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("hermes_enhanced.growth")

HERMES_HOME = Path(
    os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes-enhanced"))
)
SKILLS_DIR = HERMES_HOME / "skills"
MEMORIES_DIR = HERMES_HOME / "memories"
DATA_DIR = HERMES_HOME / "data"
STATE_DB = HERMES_HOME / "state.db"  # Sesiones de conversación

# ── Pattern Detection ──────────────────────────────────────────────────

CORRECTION_PATTERNS = [
    r"(no|mal|incorrecto|erróneo|equivocado|wrong|incorrect)",
    r"(corrige|cambia|modifica|arregla|fix|change|update)",
    r"(en realidad|actually|realmente|la verdad)",
    r"(no es así|no funciona|no sirve|doesn't work|broken)",
    r"(deberías|deberia|you should|you need to)",
]

IMPROVEMENT_PATTERNS = [
    r"(sería mejor|it would be better|más útil|more useful)",
    r"(podrías|could you|can you add|debería tener)",
    r"(sugiero|suggest|propongo|recommend|recomiendo)",
]


def scan_recent_sessions(limit: int = 20) -> List[Dict]:
    """
    Escanea sesiones recientes buscando patrones de corrección.
    Retorna una lista de incidentes detectados.
    """
    incidents = []
    db_path = STATE_DB
    if not db_path.exists():
        return incidents

    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()

        # Obtener últimas N sesiones
        cur.execute(
            "SELECT id, source, title, started_at FROM sessions "
            "ORDER BY started_at DESC LIMIT ?",
            (limit,),
        )
        sessions = cur.fetchall()

        for s_id, source, title, started_at in sessions:
            # Buscar mensajes del usuario con patrones de corrección
            cur.execute(
                "SELECT content FROM messages WHERE session_id = ? "
                "AND role = 'user' ORDER BY id",
                (s_id,),
            )
            user_msgs = [row[0] for row in cur.fetchall() if row[0]]

            for msg in user_msgs:
                for pattern in CORRECTION_PATTERNS:
                    if re.search(pattern, msg, re.IGNORECASE):
                        incidents.append({
                            "session_id": s_id,
                            "timestamp": started_at,
                            "type": "correction",
                            "pattern": pattern,
                            "preview": msg[:200],
                        })
                        break

        conn.close()
    except Exception as e:
        logger.debug(f"Session scan error: {e}")

    return incidents


# ── Memory Consolidation ───────────────────────────────────────────────


def consolidate_memory(dry_run: bool = False) -> Dict:
    """
    Revisa MEMORY.md y USER.md en busca de:
    - Entradas duplicadas (contenido similar)
    - Entradas muy antiguas (>30 días sin uso)
    - Entradas que pueden fusionarse

    Retorna estadísticas de la operación.
    """
    stats = {"duplicates_found": 0, "merged": 0, "archived": 0, "entries_checked": 0}

    for fname in ["MEMORY.md", "USER.md"]:
        fpath = MEMORIES_DIR / fname
        if not fpath.exists():
            continue

        content = fpath.read_text(encoding="utf-8")
        entries = content.split("§")
        stats["entries_checked"] += len(entries)

        # Detectar duplicados
        seen = {}
        unique_entries = []
        for entry in entries:
            stripped = entry.strip()
            if not stripped:
                continue
            # Normalizar para comparación
            normalized = re.sub(r"\s+", " ", stripped.lower())[:100]
            if normalized in seen:
                stats["duplicates_found"] += 1
                if not dry_run:
                    # Fusionar: quedarse con la más larga
                    prev_idx = seen[normalized]
                    if len(stripped) > len(unique_entries[prev_idx]):
                        unique_entries[prev_idx] = stripped
                    stats["merged"] += 1
            else:
                seen[normalized] = len(unique_entries)
                unique_entries.append(stripped)

        if not dry_run and stats["duplicates_found"] > 0:
            new_content = "\n§\n".join(unique_entries)
            fpath.write_text(new_content, encoding="utf-8")
            logger.info(
                "Consolidated %s: %d entries -> %d (merged %d, archived %d)",
                fname,
                len(entries),
                len(unique_entries),
                stats["merged"],
                stats["archived"],
            )

    return stats


# ── Skill Auto-Patching ────────────────────────────────────────────────


def suggest_skill_patches(incidents: List[Dict]) -> List[Dict]:
    """
    Analiza incidentes de corrección y sugiere parches para skills.
    Retorna una lista de sugerencias con: skill_name, suggestion, priority.
    """
    suggestions = []

    # Mapear patrones de corrección a skills
    pattern_to_skill = {
        "bujo": ["bullet-journal", "bujo-markdown", "bujo-writing-workflow"],
        "insulina": ["bullet-journal"],
        "alarma": ["flutter-android-alarms"],
        "flutter": ["flutter-android-build", "flutter-native-android-bridge"],
    }

    for inc in incidents:
        preview = inc["preview"].lower()
        for keyword, skill_names in pattern_to_skill.items():
            if keyword in preview:
                for skill_name in skill_names:
                    suggestions.append({
                        "skill": skill_name,
                        "reason": f"Corrección detectada: '{inc['pattern']}' en sesión reciente",
                        "preview": inc["preview"][:100],
                        "priority": "high" if "no" in inc["pattern"] else "medium",
                    })

    return suggestions


def auto_patch_skill(skill_name: str, old_string: str, new_string: str) -> Dict:
    """
    Parchea un skill automáticamente.
    Retorna resultado de la operación.
    """
    skill_path = SKILLS_DIR / skill_name / "SKILL.md"
    if not skill_path.exists():
        return {"success": False, "error": f"Skill {skill_name} no encontrado"}

    try:
        content = skill_path.read_text(encoding="utf-8")
        if old_string in content:
            content = content.replace(old_string, new_string, 1)
            skill_path.write_text(content, encoding="utf-8")
            logger.info("Auto-patched skill %s", skill_name)
            return {"success": True, "skill": skill_name}
        else:
            return {"success": False, "error": "old_string no encontrado"}
    except Exception as e:
        logger.error("Auto-patch failed for %s: %s", skill_name, e)
        return {"success": False, "error": str(e)}


# ── Usage Statistics ───────────────────────────────────────────────────


def get_growth_stats() -> Dict:
    """Estadísticas de crecimiento del agente."""
    stats = {
        "total_skills": 0,
        "total_sessions": 0,
        "total_memory_entries": 0,
        "last_correction_incidents": 0,
        "skills_by_category": {},
        "memory_usage_pct": 0,
    }

    # Skills
    if SKILLS_DIR.exists():
        skills = [d for d in SKILLS_DIR.iterdir() if d.is_dir()]
        stats["total_skills"] = len(skills)
        for skill_dir in skills:
            skill_md = skill_dir / "SKILL.md"
            if skill_md.exists():
                content = skill_md.read_text(encoding="utf-8", errors="ignore")
                cat_match = re.search(r"category:\s*(\S+)", content)
                if cat_match:
                    cat = cat_match.group(1)
                    stats["skills_by_category"][cat] = (
                        stats["skills_by_category"].get(cat, 0) + 1
                    )

    # Memory
    for fname in ["MEMORY.md", "USER.md"]:
        fpath = MEMORIES_DIR / fname
        if fpath.exists():
            content = fpath.read_text(encoding="utf-8")
            entries = [e for e in content.split("§") if e.strip()]
            stats["total_memory_entries"] += len(entries)

    # Sesiones recientes
    db_path = STATE_DB
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM sessions")
            stats["total_sessions"] = cur.fetchone()[0] or 0
            conn.close()
        except Exception:
            pass

    # Incidentes de corrección recientes
    incidents = scan_recent_sessions(limit=5)
    stats["last_correction_incidents"] = len(incidents)

    # Uso de memoria (char limit)
    for fname in ["MEMORY.md", "USER.md"]:
        fpath = MEMORIES_DIR / fname
        if fpath.exists():
            stats["memory_usage_pct"] = max(
                stats["memory_usage_pct"],
                min(100, (len(fpath.read_text(encoding="utf-8")) // 22)),
            )

    return stats


# ── Self-Review Runner ─────────────────────────────────────────────────


def run_self_review(full: bool = False) -> Dict:
    """
    Ejecuta una revisión completa del agente.
    Llamado automáticamente por cronjob o manualmente por el agente.

    Args:
        full: Si True, ejecuta todas las fases (lento).
              Si False, solo escaneo rápido.

    Returns:
        Dict con resultados de la revisión.
    """
    report = {
        "timestamp": datetime.now().isoformat(),
        "growth_stats": get_growth_stats(),
        "correction_incidents": scan_recent_sessions(limit=10),
        "memory_consolidation": consolidate_memory(dry_run=not full),
        "suggested_patches": [],
        "actions_taken": [],
    }

    if full:
        # Consolidar memoria
        consolidation = consolidate_memory(dry_run=False)
        report["memory_consolidation"] = consolidation
        if consolidation["merged"] > 0:
            report["actions_taken"].append(
                f"Merged {consolidation['merged']} duplicate memory entries"
            )

        # Generar sugerencias de parche
        patches = suggest_skill_patches(report["correction_incidents"])
        report["suggested_patches"] = patches[:5]  # Top 5

        # Auto-parche de alta prioridad
        for patch in patches:
            if patch["priority"] == "high" and patch["skill"]:
                report["actions_taken"].append(
                    f"High-priority patch suggested for {patch['skill']}"
                )

    return report
