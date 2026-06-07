#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "==> A parar processos Python (bridge, coordinator, HTTP)..."
pkill -f "python3 bridge.py"      2>/dev/null || true
pkill -f "python3 coordinator.py" 2>/dev/null || true
pkill -f "http.server 8000"       2>/dev/null || true

echo "==> A parar containers Docker..."
docker-compose down

echo "Tudo parado."
