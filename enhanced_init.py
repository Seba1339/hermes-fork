#!/usr/bin/env python3
"""
Hermes-Enhanced Init Wrapper (Clean Version)
Entry point for the enhanced gateway. Aplica parches del skill router
y arranca el gateway en el puerto configurado.
"""
import asyncio
import logging
import os
import sys
import runpy

# Configure paths
FORK_DIR = os.path.expanduser("~/hermes-fork")
PROJECT_DIR = os.path.expanduser("~/projects/bujo-2.0")
sys.path.insert(0, FORK_DIR)
sys.path.insert(0, PROJECT_DIR)

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


def apply_bujo_agenda_ingress_patch():
    """Attach the isolated agenda ingress at the gateway edge, never in the agent loop."""
    try:
        from gateway.platforms.base import BasePlatformAdapter
        from hermes_cli.config import load_config
        from bujo_core.hermes_gateway_ingress import ingest_gateway_event

        gateway_logger = logging.getLogger("gateway.run")
        original_handle = BasePlatformAdapter.handle_message

        async def with_bujo_agenda_ingress(adapter, event):
            try:
                settings = await asyncio.to_thread(
                    lambda: load_config().get("bujo_2", {}).get("ingress", {})
                )
                if not settings.get("enabled", False):
                    return await original_handle(adapter, event)
                mode = settings.get("mode", "shadow")
                database_path = os.path.expanduser(
                    settings.get("database_path", "~/projects/bujo-2.0/runtime/agenda.sqlite")
                )
                outcome = await asyncio.to_thread(
                    ingest_gateway_event,
                    event,
                    adapter_name=getattr(adapter, "name", ""),
                    database_path=database_path,
                    enabled=True,
                    mode=mode,
                )
                gateway_logger.info(
                    "BuJo 2 ingress received: adapter=%s mode=%s status=%s",
                    getattr(adapter, "name", ""), mode, outcome.status,
                )
            except Exception as exc:
                # The assistant conversation must still run if the isolated feature has a fault.
                gateway_logger.error("BuJo 2 ingress skipped: %s", exc)
            return await original_handle(adapter, event)

        BasePlatformAdapter.handle_message = with_bujo_agenda_ingress
        logger.info("BuJo 2 agenda ingress patch installed; configuration is read per message")
        return True

    except Exception as exc:
        logger.warning("Could not enable BuJo 2 agenda ingress: %s", exc)
        return False


def main():

    logger.info("Hermes-Enhanced Gateway Starting (Clean v3)")
    logger.info(f"  HERMES_HOME={os.environ.get('HERMES_HOME', '')}")
    logger.info(f"  FORK_DIR={FORK_DIR}")
    logger.info(f"  API_SERVER_PORT={os.environ.get('API_SERVER_PORT', '')}")
    logger.info("=" * 50)

    # 1. Aplicar parches en el borde del gateway
    apply_patches()
    apply_bujo_agenda_ingress_patch()

    # 2. Arrancar el gateway
    sys.argv = ["hermes", "gateway", "run", "--replace"]
    runpy.run_module("hermes_cli.main", run_name="__main__", alter_sys=True)

if __name__ == "__main__":
    main()
