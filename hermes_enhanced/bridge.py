"""
Hermes-Enhanced App Bridge

Módulo independiente que extiende la API del agente con:
- Endpoints de estado y memoria
- WebSocket para eventos en tiempo real
- FCM push para notificaciones background
- Metadata enriquecida en respuestas

Corre como un servicio adicional junto al gateway mejorado.
"""

import asyncio
import json
import logging
import os
import sqlite3
import time
import uuid
from typing import Optional

logger = logging.getLogger("hermes_enhanced.bridge")

# ── Config ──────────────────────────────────────────────────────────────
HERMES_HOME = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes-enhanced"))
HOST = os.environ.get("ENHANCED_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("ENHANCED_BRIDGE_PORT", "8644"))

# Conexiones activas (agente -> clientes WebSocket)
_ws_clients: set = set()
_agent_state: dict = {
    "status": "idle",
    "model": "",
    "provider": "",
    "last_response_at": None,
    "critic_passed": None,
    "plan_steps": [],
    "current_step": None,
}


def get_memory_stats() -> dict:
    """Estadísticas de la memoria holographic."""
    stats = {"total_facts": 0, "by_category": {}}

    # Leer de la DB de memoria si existe
    mem_path = os.path.join(HERMES_HOME, "data", "hermes.db")
    if os.path.exists(mem_path):
        try:
            conn = sqlite3.connect(mem_path)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM memories")
            stats["total_facts"] = cur.fetchone()[0] or 0
            try:
                cur.execute("SELECT category, COUNT(*) FROM memories GROUP BY category")
                for cat, count in cur.fetchall():
                    stats["by_category"][cat or "general"] = count
            except Exception:
                pass
            conn.close()
        except Exception as e:
            logger.debug(f"Memory stats error: {e}")

    return stats


def get_bujo_preview(limit: int = 5) -> list:
    """Ultimas entradas del BuJo como preview."""
    entries = []
    db_path = os.path.join(HERMES_HOME, "data", "bujo.sqlite")
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute(
                "SELECT date, content, entry_type FROM bujo_entries "
                "ORDER BY date DESC, id DESC LIMIT ?",
                (limit,),
            )
            for row in cur.fetchall():
                entries.append({
                    "date": row[0],
                    "content": row[1][:120],
                    "type": row[2],
                })
            conn.close()
        except Exception as e:
            logger.debug(f"BuJo preview error: {e}")
    return entries


def get_session_stats() -> dict:
    """Estadísticas de sesiones recientes."""
    stats = {"total_sessions": 0, "today_sessions": 0, "last_active": None}
    # El gateway guarda state.db en la raíz de HERMES_HOME, no en data/
    db_path = os.path.join(HERMES_HOME, "state.db")
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM sessions")
            stats["total_sessions"] = cur.fetchone()[0] or 0
            today = time.strftime("%Y-%m-%d")
            cur.execute(
                "SELECT COUNT(*) FROM sessions WHERE date(started_at) = ?", (today,)
            )
            stats["today_sessions"] = cur.fetchone()[0] or 0
            cur.execute("SELECT MAX(started_at) FROM sessions")
            last = cur.fetchone()[0]
            if last:
                stats["last_active"] = last
            conn.close()
        except Exception as e:
            logger.debug(f"Session stats error: {e}")
    return stats


# ── Endpoints para FastAPI/Flask ───────────────────────────────────────


def register_routes(app):
    """
    Registra rutas del bridge en una app FastAPI/Flask existente.

    Uso:
        from hermes_enhanced.bridge import register_routes
        register_routes(fastapi_app)
    """

    @app.get("/enhanced/status")
    async def enhanced_status():
        """Estado actual del agente mejorado."""
        global _agent_state
        return {
            "version": "0.1.0",
            "agent": _agent_state,
            "memory": get_memory_stats(),
            "sessions": get_session_stats(),
            "bujo_preview": get_bujo_preview(),
            "features": {
                "holographic_memory": True,
                "critic_loop": True,
                "planner": True,
                "fcm_push": False,  # Se activa con credenciales
            },
        }

    @app.get("/enhanced/memory")
    async def enhanced_memory(query: str = "", limit: int = 10):
        """Buscar en la memoria vectorial."""
        from hermes_enhanced import critic_evaluate

        stats = get_memory_stats()
        return {
            "query": query,
            "total_facts": stats["total_facts"],
            "by_category": stats["by_category"],
            "results": [],
        }

    @app.get("/enhanced/version")
    async def enhanced_version():
        """Info de versión del fork."""
        return {
            "name": "Hermes-Enhanced",
            "version": "0.1.0",
            "fork": "Seba1339/hermes-fork",
            "upstream": "NousResearch/hermes-agent",
            "added_features": [
                "vector_memory",
                "critic_loop",
                "task_planner",
                "app_bridge",
            ],
        }


# ── WebSocket event emitter ────────────────────────────────────────────


def broadcast_event(event_type: str, data: dict):
    """Envía un evento a todos los clientes WebSocket conectados."""
    if not _ws_clients:
        return
    message = json.dumps({"type": event_type, "data": data, "ts": time.time()})
    dead = set()
    for ws in _ws_clients:
        try:
            ws.put_nowait(message)
        except Exception:
            dead.add(ws)
    if dead:
        _ws_clients.difference_update(dead)


def notify_agent_status(status: str, **kwargs):
    """Actualiza y transmite el estado del agente."""
    global _agent_state
    _agent_state.update({"status": status, **kwargs})
    broadcast_event("agent_status", _agent_state)


def notify_critic_result(passed: bool, issues: str = ""):
    """Transmite resultado de la autoevaluación."""
    _agent_state["critic_passed"] = passed
    broadcast_event("critic_result", {"passed": passed, "issues": issues})


def notify_plan_step(step: str, total: int, current: int):
    """Transmite progreso del planificador."""
    _agent_state["current_step"] = step
    broadcast_event(
        "plan_progress",
        {
            "step": step,
            "current": current,
            "total": total,
        },
    )
