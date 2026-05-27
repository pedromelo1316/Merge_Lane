import json
import os
import queue
import sys
import threading
import time

from comms import open_zenoh_session, open_zenoh_session_with_retry
from simulation_demo import run as run_simulation


def _publish_done(session, station_id, vehicle_id, skipped=False):
    """Sinaliza ao coordinator que este veículo terminou (ou saltou) o cenário actual."""
    payload = json.dumps({
        "station_id": station_id,
        "vehicle_id": vehicle_id,
        "skipped": skipped,
    }).encode()
    session.put(f"coordinator/done/{station_id}", payload)


def _print_scenario_config(scenario, station_id, vehicle_id):
    """Imprime a configuração do veículo no cenário (road, posição inicial, speed limit)."""
    own = next((v for v in scenario.get("vehicles", [])
                if v.get("station_id") == station_id), None)
    if not own:
        print(f"[{time.strftime('%H:%M:%S')}] [{vehicle_id}] Não encontrado no cenário")
        return
    roads = {r["id"]: r for r in scenario.get("roads", [])}
    road  = roads.get(own.get("road"), {})
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] [{vehicle_id}] road={own['road']} "
          f"pos=({own['lat']},{own['lon']}) "
          f"speed_limit={road.get('speed_limit_kmh', '?')}km/h")


def run_scenario(scenario, vehicle_id, station_id, vanetza_session, stop_event):
    """Wrapper fino para manter vehicle_demo.py desacoplado dos internos de simulation_demo.py."""
    run_simulation(scenario, vehicle_id, station_id, vanetza_session, stop_event)


def main():
    """Entry point do veículo (demo): lê variáveis de ambiente, liga ao Vanetza e ao coordinator,
    e processa cenários em loop até ser terminado."""
    vehicle_id  = os.environ.get("VEHICLE_ID")
    station_id  = os.environ.get("STATION_ID")
    vanetza_url = os.environ.get("VANETZA_ZENOH_URL")
    coord_url   = os.environ.get("COORDINATOR_ZENOH_URL")

    if not vehicle_id or station_id is None:
        print(f"[{time.strftime('%H:%M:%S')}] Erro: VEHICLE_ID e STATION_ID são obrigatórios")
        sys.exit(1)

    station_id = int(station_id)

    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] [{vehicle_id}] [DEMO] VEHICLE_ID={vehicle_id} STATION_ID={station_id}")
    print(f"[{ts}] [{vehicle_id}] [DEMO] VANETZA_ZENOH_URL={vanetza_url}")
    print(f"[{ts}] [{vehicle_id}] [DEMO] COORDINATOR_ZENOH_URL={coord_url}")

    print(f"[{ts}] [{vehicle_id}] A ligar ao Vanetza ({vanetza_url})...")
    vanetza_session = open_zenoh_session_with_retry(vanetza_url)
    print(f"[{time.strftime('%H:%M:%S')}] [{vehicle_id}] Vanetza ligado.")

    print(f"[{time.strftime('%H:%M:%S')}] [{vehicle_id}] A ligar ao coordinator ({coord_url})...")
    coord_session = open_zenoh_session(coord_url)
    print(f"[{time.strftime('%H:%M:%S')}] [{vehicle_id}] Coordinator ligado.")

    stop_event     = threading.Event()
    scenario_queue = queue.SimpleQueue()

    def ready_loop():
        """Heartbeat permanente para o coordinator saber que este veículo está vivo."""
        payload = json.dumps({"station_id": station_id}).encode()
        while True:
            coord_session.put(f"coordinator/ready/{station_id}", payload)
            time.sleep(2.0)

    threading.Thread(target=ready_loop, daemon=True).start()

    def on_scenario(sample):
        """Callback Zenoh: aborta o cenário em curso e enfileira o novo."""
        try:
            data = json.loads(bytes(sample.payload).decode())
            stop_event.set()
            scenario_queue.put(data)
        except Exception as e:
            print(f"[{time.strftime('%H:%M:%S')}] [{vehicle_id}] Erro no cenário: {e}")

    coord_session.declare_subscriber("coordinator/scenario", on_scenario)
    print(f"[{time.strftime('%H:%M:%S')}] [{vehicle_id}] [DEMO] Pronto. A aguardar cenário...")

    while True:
        scenario = scenario_queue.get()
        stop_event.clear()

        active = scenario.get("active_vehicles", [])
        name   = scenario.get("name", "?")

        if station_id not in active:
            ts = time.strftime("%H:%M:%S")
            print(f"[{ts}] [{vehicle_id}] Cenário '{name}' — não sou active, a saltar")
            _publish_done(coord_session, station_id, vehicle_id, skipped=True)
            continue

        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] [{vehicle_id}] [DEMO] Cenário recebido: '{name}'")
        _print_scenario_config(scenario, station_id, vehicle_id)

        run_scenario(scenario, vehicle_id, station_id, vanetza_session, stop_event)

        _publish_done(coord_session, station_id, vehicle_id)
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] [{vehicle_id}] Done publicado — a aguardar próximo cenário")


if __name__ == "__main__":
    main()
