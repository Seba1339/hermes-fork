#!/bin/bash
# Hermes-Enhanced Manager
# Uso: ./manage-enhanced.sh {start|stop|restart|status|logs|test|update}
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
ACTION="${1:-status}"

case "$ACTION" in
  start)
    echo "🚀 Arrancando Hermes-Enhanced..."
    sudo systemctl daemon-reload
    sudo systemctl enable hermes-enhanced-gateway hermes-enhanced-bridge 2>/dev/null
    sudo systemctl start hermes-enhanced-gateway hermes-enhanced-bridge
    echo "✅ Servicios iniciados"
    sleep 3
    $0 status
    ;;
  stop)
    echo "🛑 Parando Hermes-Enhanced..."
    sudo systemctl stop hermes-enhanced-gateway hermes-enhanced-bridge
    echo "✅ Servicios detenidos"
    ;;
  restart)
    $0 stop
    sleep 2
    $0 start
    ;;
  status)
    echo "=== Gateway ==="
    sudo systemctl status hermes-enhanced-gateway 2>&1 | grep -E "Active|Main PID|Memory"
    echo ""
    echo "=== Bridge ==="
    sudo systemctl status hermes-enhanced-bridge 2>&1 | grep -E "Active|Main PID|Memory"
    echo ""
    echo "=== Health Check ==="
    GATEWAY=$(curl -s --connect-timeout 3 http://127.0.0.1:8643/health 2>/dev/null || echo "CAIDO")
    BRIDGE=$(curl -s --connect-timeout 3 http://127.0.0.1:8644/health 2>/dev/null || echo "CAIDO")
    echo "Gateway: $GATEWAY"
    echo "Bridge:  $BRIDGE"
    echo ""
    echo "=== URLs ==="
    echo "Gateway: http://localhost:8643/v1"
    echo "Bridge:  http://localhost:8644"
    echo "Public:  https://salvarez786.cl/enhanced/v1/"
    echo ""
    echo "=== Logs ==="
    echo "Gateway: journalctl -u hermes-enhanced-gateway --since '5 min ago'"
    echo "Bridge:  journalctl -u hermes-enhanced-bridge --since '5 min ago'"
    ;;
  logs)
    echo "=== Gateway Logs ==="
    sudo journalctl -u hermes-enhanced-gateway --since "10 min ago" --no-pager 2>/dev/null | tail -20
    echo ""
    echo "=== Bridge Logs ==="
    sudo journalctl -u hermes-enhanced-bridge --since "10 min ago" --no-pager 2>/dev/null | tail -10
    ;;
  test)
    echo "🧪 Testeando Hermes-Enhanced..."
    echo ""
    echo -n "Gateway: "
    curl -s --connect-timeout 5 http://127.0.0.1:8643/health && echo " ✅" || echo " ❌"
    echo -n "Bridge:  "
    curl -s --connect-timeout 5 http://127.0.0.1:8644/health && echo " ✅" || echo " ❌"
    echo -n "Chat:    "
    curl -s --connect-timeout 15 http://127.0.0.1:8643/v1/chat/completions \
      -H "Content-Type: application/json" \
      -d '{"model":"hermes-agent","messages":[{"role":"user","content":"di OK"}],"max_tokens":10}' 2>/dev/null | grep -q "OK" && echo " ✅" || echo " ❌"
    echo -n "Models:  "
    curl -s --connect-timeout 5 http://127.0.0.1:8643/v1/models 2>/dev/null | grep -q "hermes-agent" && echo " ✅" || echo " ❌"
    echo -n "Memo:    "
    curl -s --connect-timeout 5 http://127.0.0.1:8644/status 2>/dev/null | python3 -c "import sys,json; print('✅' if json.load(sys.stdin).get('version') else '❌')" 2>/dev/null || echo " ❌"
    echo ""
    echo "🧪 Test completo"
    ;;
  update)
    echo "📦 Actualizando Hermes-Enhanced desde GitHub..."
    cd "$DIR"
    git pull origin main 2>/dev/null || echo "No hay cambios"
    source .venv/bin/activate
    pip install -e . 2>&1 | tail -1
    echo "✅ Código actualizado. Reinicia con: $0 restart"
    ;;
  *)
    echo "Uso: $0 {start|stop|restart|status|logs|test|update}"
    exit 1
    ;;
esac
