#!/bin/bash
# Hermes-Enhanced Launcher
# Usa el fork con memoria holographic + critic loop
set -e

export HERMES_HOME="${HERMES_HOME:-$HOME/.hermes-enhanced}"
export HERMES_ENHANCED="${HERMES_ENHANCED:-1}"

echo "╔══════════════════════════════════════════════════════╗"
echo "║          Hermes-Enhanced v0.1                        ║"
echo "╠══════════════════════════════════════════════════════╣"
echo "║ Home:    $HERMES_HOME                                "
echo "║ Memory:  Holographic (vectorial)                     "
echo "║ Critic:  Activado                                    "
echo "╚══════════════════════════════════════════════════════╝"
echo ""

cd "$(dirname "$0")"

# Activar venv
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
elif [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
fi

# Ejecutar el agente
exec python -m hermes_cli "$@"
