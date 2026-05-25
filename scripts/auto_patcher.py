#!/usr/bin/env python3
"""
Hermes-Enhanced Auto-Patcher
Parchea skills automáticamente cuando detecta errores recurrentes.
Ejecutado por el agente cuando identifica un patrón de corrección.
"""
import json
import os
import re
import sys
from pathlib import Path

FORK_DIR = os.path.expanduser("~/hermes-fork")
if FORK_DIR not in sys.path:
    sys.path.insert(0, FORK_DIR)

os.environ.setdefault("HERMES_HOME", os.path.expanduser("~/.hermes-enhanced"))
HERMES_HOME = Path(os.environ["HERMES_HOME"])


def auto_patch_from_correction(skill_name: str, user_message: str, agent_response: str):
    """
    Dado un skill, el mensaje del usuario que corrigió y la respuesta del agente,
    genera un parche automático.
    
    Actualmente hace un dry-run (sugiere el cambio). 
    En futuras versiones aplicará el parche directamente.
    """
    skill_path = HERMES_HOME / "skills" / skill_name / "SKILL.md"
    if not skill_path.exists():
        return {"success": False, "error": f"Skill {skill_name} no encontrado"}

    content = skill_path.read_text(encoding="utf-8")

    # Extraer el problema del mensaje del usuario
    problem = user_message[:200]
    correction = agent_response[:200] if agent_response else ""

    # Buscar secciones relevantes en el skill
    sections = re.split(r"\n## ", content)

    report = {
        "skill": skill_name,
        "problem": problem,
        "suggestion": f"Revisar secciones relacionadas con: {problem[:80]}",
        "needs_review": True,  # Siempre requiere revisión humana
        "estimated_fix": "patch en SKILL.md",
    }

    return report


def list_stale_skills(days_threshold: int = 60) -> list:
    """Lista skills que no se han usado recientemente."""
    stale = []
    skills_dir = HERMES_HOME / "skills"
    if not skills_dir.exists():
        return stale

    now = __import__("time").time()
    for skill_dir in skills_dir.iterdir():
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        mtime = skill_md.stat().st_mtime
        age_days = (now - mtime) / 86400
        if age_days > days_threshold:
            stale.append({
                "name": skill_dir.name,
                "last_modified": __import__("datetime").datetime.fromtimestamp(mtime).isoformat(),
                "age_days": round(age_days, 1),
            })

    return sorted(stale, key=lambda x: x["age_days"], reverse=True)


if __name__ == "__main__":
    # Modo CLI: reportar skills antiguos
    stale = list_stale_skills()
    if stale:
        print(f"Skills sin modificar en >60 días ({len(stale)}):")
        for s in stale[:10]:
            print(f"  📦 {s['name']} - {s['age_days']} días sin cambios ({s['last_modified']})")
    else:
        print("No hay skills antiguos. Todo actualizado.")
