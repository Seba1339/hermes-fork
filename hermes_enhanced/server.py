"""
Hermes-Enhanced App Bridge Server

Servidor independiente que corre junto al gateway mejorado.
Expone endpoints para que la App (Flutter) consulte estado,
memoria y reciba eventos en tiempo real.

Uso:
    HERMES_HOME=~/.hermes-enhanced python -m hermes_enhanced.server
"""

import json
import logging
import os
import sys

# Asegurar que el path del fork está disponible
FORK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if FORK_DIR not in sys.path:
    sys.path.insert(0, FORK_DIR)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("hermes_enhanced.server")

try:
    from aiohttp import web
except ImportError:
    logger.error("aiohttp requerido. pip install aiohttp")
    sys.exit(1)

from hermes_enhanced.bridge import (
    get_memory_stats,
    get_session_stats,
    get_bujo_preview,
    _ws_clients,
    broadcast_event,
    HOST,
    PORT,
    notify_agent_status,
)

# ── Almacén de notificaciones pendientes ──
_pending_notifications: list = []
_notif_id_counter: int = 0

# ── Handlers ───────────────────────────────────────────────────────────


async def handle_status(request):
    """GET /status - Estado completo del agente mejorado."""
    return web.json_response({
        "version": "0.1.0",
        "fork": "Seba1339/hermes-fork",
        "name": "Hermes-Enhanced",
        "agent": {
            "status": "idle",
            "model": os.environ.get("HERMES_MODEL", "deepseek-v4-flash"),
            "provider": os.environ.get("HERMES_PROVIDER", "deepseek"),
            "last_response_at": None,
        },
        "memory": get_memory_stats(),
        "sessions": get_session_stats(),
        "bujo_preview": get_bujo_preview(3),
        "features": {
            "holographic_memory": True,
            "critic_loop": True,
            "planner": True,
            "websocket_events": True,
            "app_bridge": True,
        },
    })


async def handle_memory(request):
    """GET /memory - Estadísticas de memoria."""
    return web.json_response(get_memory_stats())


async def handle_sessions(request):
    """GET /sessions - Estadísticas de sesiones."""
    return web.json_response(get_session_stats())


async def handle_bujo(request):
    """GET /bujo - Preview de BuJo."""
    return web.json_response({"entries": get_bujo_preview(10)})


async def handle_version(request):
    """GET /version - Información de versión."""
    return web.json_response({
        "name": "Hermes-Enhanced",
        "version": "0.1.0",
        "fork": "Seba1339/hermes-fork",
        "upstream": "NousResearch/hermes-agent",
        "added_features": [
            "vector_memory (holographic)",
            "critic_loop (autoevaluacion)",
            "task_planner (planificador)",
            "app_bridge (WebSocket + API)",
            "bujo_preview",
            "session_stats",
        ],
        "config_home": os.environ.get(
            "HERMES_HOME", os.path.expanduser("~/.hermes-enhanced")
        ),
    })


async def handle_health(request):
    """GET /health - Health check simple."""
    return web.json_response({"status": "ok", "service": "hermes-enhanced-bridge"})


async def handle_ws(request):
    """GET /ws - WebSocket para eventos en tiempo real."""
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    _ws_clients.add(ws)
    logger.info("WebSocket client connected (%d total)", len(_ws_clients))

    try:
        # Enviar estado inicial
        await ws.send_json({
            "type": "connected",
            "data": {
                "version": "0.1.0",
                "features": ["status", "critic", "plan", "memory"],
            },
            "ts": __import__("time").time(),
        })

        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    if data.get("type") == "ping":
                        await ws.send_json({"type": "pong"})
                except json.JSONDecodeError:
                    pass
            elif msg.type == web.WSMsgType.ERROR:
                logger.error("WS error: %s", ws.exception())

    finally:
        _ws_clients.discard(ws)
        logger.info("WebSocket client disconnected (%d remaining)", len(_ws_clients))

    return ws


# ── Notificaciones Proactivas ──────────────────────────────────────────


async def handle_notify(request):
    """POST /notify - Crea una notificación para la App.

    Body JSON: {"title": str, "body": str, "type": str, "action": str}
    """
    global _notif_id_counter, _pending_notifications
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "JSON inválido"}, status=400)

    _notif_id_counter += 1
    notif = {
        "id": _notif_id_counter,
        "title": data.get("title", "Hermes-Enhanced"),
        "body": data.get("body", ""),
        "type": data.get("type", "info"),
        "action": data.get("action", ""),
        "created_at": __import__("time").time(),
        "read": False,
    }
    _pending_notifications.append(notif)
    # Mantener máx 50 notificaciones
    _pending_notifications[:] = _pending_notifications[-50:]

    # Broadcast por WebSocket
    broadcast_event("notification", notif)

    logger.info("Notificación creada: %s - %s", notif["title"], notif["body"][:60])
    return web.json_response({"success": True, "notification": notif})


async def handle_get_notifications(request):
    """GET /notifications - Obtiene notificaciones pendientes."""
    return web.json_response({
        "notifications": _pending_notifications[-20:],  # Últimas 20
        "unread": sum(1 for n in _pending_notifications if not n["read"]),
    })


async def handle_mark_read(request):
    """POST /notifications/{notif_id}/read - Marca como leída."""
    notif_id = int(request.match_info.get("notif_id", 0))
    for n in _pending_notifications:
        if n["id"] == notif_id:
            n["read"] = True
            return web.json_response({"success": True})
    return web.json_response({"error": "Notificación no encontrada"}, status=404)


# ── Dashboard de Proyectos ─────────────────────────────────────────────


async def handle_dashboard(request):
    """GET /dashboard - Dashboard completo de proyectos."""
    from hermes_enhanced.project_kb import get_project_list
    from hermes_enhanced.changelog import get_all_changelogs
    from hermes_enhanced.growth import get_growth_stats

    projects = get_project_list()
    changelogs = get_all_changelogs(limit=3)
    growth = get_growth_stats()

    return web.json_response({
        "projects": [
            {
                "name": p["name"],
                "language": p["language"],
                "decisions": p["decisions_count"],
                "errors": p["errors_count"],
                "conventions": p["conventions_count"],
                "last_activity": p.get("updated_at", ""),
                "recent_changes": changelogs.get(p["name"], []),
            }
            for p in projects
        ],
        "stats": {
            "total_projects": len(projects),
            "total_sessions": growth["total_sessions"],
            "total_skills": growth["total_skills"],
            "memory_entries": growth["total_memory_entries"],
            "recent_corrections": growth["last_correction_incidents"],
        },
        "changelogs": changelogs,
    })


async def handle_changelog(request):
    """GET /changelog/{project} - Changelog de un proyecto."""
    from hermes_enhanced.changelog import get_changelog

    project = request.match_info.get("project", "")
    if not project:
        return web.json_response({"error": "project requerido"}, status=400)

    entries = get_changelog(project, limit=30)
    return web.json_response({
        "project": project,
        "entries": entries,
        "total": len(entries),
    })


# ── Main ───────────────────────────────────────────────────────────────


def create_app() -> web.Application:
    app = web.Application()

    # CORS middleware
    @web.middleware
    async def cors_middleware(request, handler):
        if request.method == "OPTIONS":
            response = web.Response()
        else:
            response = await handler(request)
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        return response

    app.middlewares.append(cors_middleware)

    # Rutas existentes
    app.router.add_get("/health", handle_health)
    app.router.add_get("/status", handle_status)
    app.router.add_get("/version", handle_version)
    app.router.add_get("/memory", handle_memory)
    app.router.add_get("/sessions", handle_sessions)
    app.router.add_get("/bujo", handle_bujo)
    app.router.add_get("/ws", handle_ws)

    # Notificaciones
    app.router.add_post("/notify", handle_notify)
    app.router.add_get("/notifications", handle_get_notifications)
    app.router.add_post("/notifications/{notif_id}/read", handle_mark_read)

    # Dashboard de proyectos
    app.router.add_get("/dashboard", handle_dashboard)
    app.router.add_get("/changelog/{project}", handle_changelog)

    return app


def main():
    app = create_app()
    host = HOST
    port = PORT

    logger.info("=" * 60)
    logger.info("Hermes-Enhanced App Bridge v0.1.0")
    logger.info("=" * 60)
    logger.info("Listening on http://%s:%d", host, port)
    logger.info("Endpoints:")
    logger.info("  GET /health     - Health check")
    logger.info("  GET /status     - Full agent status")
    logger.info("  GET /version    - Version info")
    logger.info("  GET /memory     - Memory stats")
    logger.info("  GET /sessions   - Session stats")
    logger.info("  GET /bujo       - BuJo preview")
    logger.info("  GET /ws         - WebSocket events")
    logger.info("  POST /notify    - Push notification (agent -> app)")
    logger.info("  GET /notifications - Pending notifications")
    logger.info("  POST /notifications/{id}/read - Mark as read")
    logger.info("=" * 60)

    web.run_app(app, host=host, port=port)


if __name__ == "__main__":
    main()
