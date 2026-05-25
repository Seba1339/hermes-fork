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
    Evalua la respuesta del agente usando un modelo ligero.
    Retorna {"passed": bool, "issues": str, "suggestion": str}
    """
    if not ENABLED or not response_text or len(response_text) < 20:
        return {"passed": True, "issues": "", "suggestion": ""}

    try:
        # Usar el provider actual pero con max_tokens reducido
        critic_messages = [
            {
                "role": "system",
                "content": (
                    "Eres un crítico de calidad. Evalúa si la respuesta del agente: "
                    "1) Responde directamente a la pregunta del usuario "
                    "2) Es factualmente correcta (no alucina) "
                    "3) Está completa y no cortada "
                    "4) No contiene errores, advertencias o placeholders "
                    "Responde SOLO con un JSON: "
                    '{"passed": true/false, "issues": "descripción breve si hay problemas", '
                    '"suggestion": "qué mejorar"}'
                ),
            },
            {
                "role": "user",
                "content": f"Pregunta del usuario: {user_message[:500]}\n\nRespuesta del agente:\n{response_text[:2000]}",
            },
        ]

        # Usar el cliente OpenAI del agente para la crítica
        if agent and hasattr(agent, "_get_api_client"):
            client = agent._get_api_client()
            critic_resp = client.chat.completions.create(
                model=agent.model,
                messages=critic_messages,
                max_tokens=300,
                temperature=0.1,
            )
            text = critic_resp.choices[0].message.content
            # Intentar parsear como JSON
            if text:
                # Buscar JSON en la respuesta
                import re

                json_match = re.search(r"\{.*\}", text, re.DOTALL)
                if json_match:
                    result = json.loads(json_match.group())
                    if not result.get("passed", True):
                        logger.warning(
                            f"CRITIC: {result.get('issues', 'sin detalle')[:200]}"
                        )
                    return result
    except Exception as e:
        logger.debug(f"Critic loop skipped: {e}")

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
