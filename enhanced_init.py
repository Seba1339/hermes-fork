#!/usr/bin/env python3
"""
Hermes-Enhanced Init Wrapper (Clean Version)
Entry point for the enhanced gateway. Aplica parches del skill router
y arranca el gateway en el puerto configurado.
"""
import logging
import os
import sys
import runpy

# Configure paths
FORK_DIR = os.path.expanduser("~/hermes-fork")
sys.path.insert(0, FORK_DIR)

# Environment variables
os.environ.setdefault("HERMES_HOME", os.path.expanduser("~/.hermes-enhanced"))
os.environ["HERMES_ENHANCED"] = "1"
os.environ["HERMES_SESSION_SOURCE"] = "enhanced"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("enhanced_init")

def apply_patches():
    """Aplica parches en caliente al AIAgent (solo skill router, critic purgado)."""
    try:
        from run_agent import AIAgent
        from hermes_enhanced.skill_router import auto_load
        import json

        original_run = AIAgent.run_conversation

        def with_skills(self, user_message, **kwargs):
            try:
                skills = auto_load(str(user_message)[:500], max_skills=3)
                if skills:
                    logger.info(f"Skill Router: {skills}")
                    kwargs['system_message'] = kwargs.get('system_message', '') + \
                        f"\n\nLOAD THESE SKILLS: {json.dumps(skills)}"
            except Exception as e:
                logger.debug(f"Skill Router skip: {e}")
            return original_run(self, user_message, **kwargs)

        AIAgent.run_conversation = with_skills
        logger.info("Parche del Skill Router aplicado correctamente")
        return True
    except Exception as e:
        logger.warning(f"No se pudo aplicar el parche del Skill Router: {e}")
        return False

def main():
    logger.info("=" * 50)
    logger.info("Hermes-Enhanced Gateway Starting (Clean v3)")
    logger.info(f"  HERMES_HOME={os.environ.get('HERMES_HOME', '')}")
    logger.info(f"  FORK_DIR={FORK_DIR}")
    logger.info(f"  API_SERVER_PORT={os.environ.get('API_SERVER_PORT', '')}")
    logger.info("=" * 50)

    # 1. Aplicar parche de skill router
    apply_patches()

    # 2. Arrancar el gateway
    sys.argv = ["hermes", "gateway", "run", "--replace"]
    runpy.run_module("hermes_cli.main", run_name="__main__", alter_sys=True)

if __name__ == "__main__":
    main()
