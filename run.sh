#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "==> A parar processos antigos (bridge, HTTP, coordinator)..."
pkill -f "python3 bridge.py"      2>/dev/null || true
pkill -f "python3 coordinator.py" 2>/dev/null || true
pkill -f "http.server 8000"       2>/dev/null || true
sleep 0.5

echo "==> A parar containers antigos..."
docker-compose down

echo "==> A arrancar containers (com rebuild das imagens Python)..."
docker-compose up -d --build

echo "==> À espera que o Zenoh router esteja disponível (127.0.0.1:7446)..."
until nc -z -w 1 127.0.0.1 7446 2>/dev/null; do
    printf "."
    sleep 0.5
done
echo " pronto."

echo "==> À espera que o Zenoh broker Vanetza esteja disponível (192.168.98.10:7447)..."
until nc -z -w 1 192.168.98.10 7447 2>/dev/null; do
    printf "."
    sleep 0.5
done
echo " pronto."

echo "==> A arrancar bridge (bridge.py)..."
python3 bridge.py &
BRIDGE_PID=$!
echo "    bridge PID: $BRIDGE_PID"

echo "==> A arrancar servidor HTTP para o dashboard (porta 8000)..."
python3 -m http.server 8000 --directory "$SCRIPT_DIR" &
HTTP_PID=$!
echo "    http PID: $HTTP_PID"

echo "==> À espera que o WebSocket da bridge esteja disponível (localhost:8765)..."
until nc -z -w 1 127.0.0.1 8765 2>/dev/null; do
    printf "."
    sleep 0.3
done
echo " pronto."

echo "==> Dashboard disponível em: http://localhost:8000/dashboard.html"

echo "==> A arrancar coordinator GUI..."
python3 coordinator.py &
COORDINATOR_PID=$!
echo "    coordinator PID: $COORDINATOR_PID"

echo ""
echo "Pronto. Para parar: kill $BRIDGE_PID $HTTP_PID $COORDINATOR_PID"
