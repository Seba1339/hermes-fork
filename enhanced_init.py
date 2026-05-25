#!/usr/bin/env python3
"""
Hermes-Enhanced Init Wrapper
Entry point for the enhanced gateway. Aplica parches en caliente
al AIAgent y arranca el gateway con todas las mejoras activas.

Uso (systemd):
    ExecStart=/path/to/.venv/bin/python enhanced_init.py gateway run --replace
"""
import logging
import os
import sys
import runpy

# ── Config ─────────────────────────────────────────────────────────────
FORK_DIR = os.path.expanduser("~/hermes-fork")
sys.path.insert(0, FORK_DIR)

# Set HERMES_HOME to enhanced profile if not already set
os.environ.setdefault("HERMES_HOME", os.path.expanduser("~/.hermes-enhanced"))
os.environ["HERMES_ENHANCED"] = "1"
os.environ["HERMES_SESSION_SOURCE"] = "enhanced"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("enhanced_init")


# ── Parches del AIAgent ───────────────────────────────────────────────

def apply_patches():
    """Aplica parches en caliente al AIAgent (critic loop)."""
    from run_agent import AIAgent
    from hermes_enhanced import critic_evaluate
    from hermes_enhanced.changelog import log_modification

    original_run = AIAgent.run_conversation

    def critiqued_run(self, user_message, **kwargs):
        result = original_run(self, user_message, **kwargs)
        if (result.get("final_response")
            and not result.get("interrupted")
            and os.environ.get("HERMES_ENHANCED") == "1"):
            try:
                evaluation = critic_evaluate(
                    str(user_message),
                    str(result["final_response"]),
                    agent=self
                )
                if not evaluation.get("passed", True):
                    logger.warning(
                        "CRITIC: %s - %s",
                        evaluation.get("issues", "")[:100],
                        evaluation.get("suggestion", "")[:100]
                    )
                    log_modification(
                        "agent_performance", "critic_feedback", "response",
                        evaluation.get("issues", "Calidad subóptima")[:200],
                        status="warning"
                    )
            except Exception as e:
                logger.debug(f"Critic loop skipped: {e}")
        return result

    # Skill router patch: envuelve el critic patch
    def with_skills(self, user_message, **kwargs):
        try:
            from hermes_enhanced.skill_router import auto_load
            import json
            skills = auto_load(str(user_message)[:500], max_skills=3)
            if skills:
                logger.info(f"Skill Router: {skills}")
                kwargs['system_message'] = kwargs.get('system_message', '') + \
                    f"\n\nLOAD THESE SKILLS: {json.dumps(skills)}"
        except Exception as e:
            logger.debug(f"Skill Router skip: {e}")
        return critiqued_run(self, user_message, **kwargs)

    AIAgent.run_conversation = with_skills
    logger.info("Parches aplicados: critic loop + skill router")
    return True


# ── Main ───────────────────────────────────────────────────────────────

def main():
    logger.info("=" * 50)
    logger.info("Hermes-Enhanced Gateway Starting")
    logger.info(f"  HERMES_HOME={os.environ.get('HERMES_HOME', '')}")
    logger.info(f"  FORK_DIR={FORK_DIR}")
    logger.info("=" * 50)
    # 1. Aplicar parches
    apply_patches()

    # 2. Verificar modulos enhanced
    try:
        from hermes_enhanced import critic_evaluate
        from hermes_enhanced.coding import code_quality_gate
        from hermes_enhanced.sandbox import test_code
        from hermes_enhanced.changelog import log_modification
        from hermes_enhanced.project_kb import load
        from hermes_enhanced.growth import get_growth_stats
        logger.info("6 modulos enhanced cargados correctamente")
    except Exception as e:
        logger.warning(f"Modulos enhanced parciales: {e}")

    # 3. Ejecutar el gateway pasando los argumentos de systemd
    # systemd pasa: python enhanced_init.py -> sys.argv = ['enhanced_init.py']
    # Pero necesitamos que hermes_cli.main reciba: ['hermes', 'gateway', 'run', '--replace']
    logger.info("Arrancando gateway...")

    # Pasar los argumentos correctos: el primer arg es el programa,
    # el resto son 'gateway run --replace'
    sys.argv = ["hermes", "gateway", "run", "--replace"]

    # Usar runpy.run_module para ejecutar hermes_cli.main
    runpy.run_module("hermes_cli.main", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
