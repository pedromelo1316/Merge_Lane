#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "==> A parar processos antigos (bridge e HTTP)..."
pkill -f "python3 bridge.py" 2>/dev/null || true
pkill -f "http.server 8000"  2>/dev/null || true
sleep 0.5

echo "==> A parar containers antigos..."
docker compose down

echo "==> A arrancar containers..."
docker compose up -d

echo "==> À espera que o Zenoh broker esteja disponível (192.168.98.10:7447)..."
until nc -z 192.168.98.10 7447 2>/dev/null; do
    printf "."
    sleep 1
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

sleep 1

echo "==> A abrir dashboard no Firefox..."
firefox "http://localhost:8000/dashboard.html" &

echo ""
echo "Pronto. Para parar: kill $BRIDGE_PID $HTTP_PID"
echo "Para correr os veículos: python3 run_vehicles.py"
