"""
Hermes-Enhanced: Critic Loop + Planificación

Se conecta a los hooks del sistema sin modificar el core loop.
Actúa después de que run_conversation devuelve la respuesta.
"""

import logging
import json
import os

logger = logging.getLogger(__name__)

ENABLED = os.environ.get("HERMES_ENHANCED", "0") == "1"


def critic_evaluate(user_message: str, response_text: str, agent=None) -> dict:
    """
    Evalua la respuesta del agente usando heurísticas locales (sin LLM secundario).
    Retorna {"passed": bool, "issues": str, "suggestion": str}
    """
    if not ENABLED or not response_text or len(response_text) < 20:
        return {"passed": True, "issues": "", "suggestion": ""}

    issues = []

    # 1. Check for truncation / cut-off sentences
    if response_text.rstrip()[-1] in ",;:yY" if response_text.rstrip() else False:
        pass
    if len(response_text) > 10 and not response_text.rstrip()[-1] in ".!?\n":
        # Count lines - if last line doesn't end with punctuation, might be truncated
        last_line = response_text.strip().split("\n")[-1].strip()
        if last_line and not last_line[-1] in ".!?)\"'":
            issues.append("Respuesta parece truncada (no termina en puntuacion)")

    # 2. Check for error messages leaked to user
    error_patterns = ["traceback", "exception", "error:", "error:",
                      "no such file", "command not found", "permission denied",
                      "connection refused", "timeout", "failed:"]
    resp_lower = response_text.lower()
    for pat in error_patterns:
        if pat in resp_lower:
            issues.append(f"Contiene posible error: '{pat}'")
            break

    # 3. Check for placeholders / incomplete content
    placeholder_patterns = ["[object", "[function", "[promise", "undefined",
                            "null", "none", "fill this in", "todo", "fixme"]
    for pat in placeholder_patterns:
        if pat in resp_lower:
            issues.append(f"Contiene placeholder: '{pat}'")

    if issues:
        logger.warning(f"CRITIC ({len(issues)} issues): {'; '.join(issues[:3])}")
        return {
            "passed": False,
            "issues": "; ".join(issues[:3]),
            "suggestion": "Revisar y corregir antes de entregar al usuario"
        }

    return {"passed": True, "issues": "", "suggestion": ""}


def estimate_task_complexity(user_message: str) -> int:
    """
    Estima si una tarea necesita planificación.
    Retorna número de pasos estimados (0 = simple, 3+ = planificar).
    """
    indicators = [
        "bash",
        "script",
        "build",
        "deploy",
        "implementar",
        "crear",
        "refactor",
        "multi",
        "varios",
        "pasos",
        "secuencia",
        "primero... luego",
        "step",
        "phase",
        "fase",
        "etapa",
    ]
    score = 0
    msg_lower = user_message.lower()
    for word in indicators:
        if word in msg_lower:
            score += 1
    return score
